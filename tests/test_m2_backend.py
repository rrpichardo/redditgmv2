"""Tests for M2 backend: pipeline API endpoints and tag writer lock enforcement.

All tests use FastAPI TestClient with in-memory/tmp SQLite so no real network calls
or subprocesses are spawned.
"""

from __future__ import annotations

import os
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Patch RUNTIME before importing app so the server uses tmp dirs
_tmp_runtime = tempfile.mkdtemp()

import app as app_module  # noqa: E402 — import after env setup

# Point RUNTIME at the tmp dir so tests don't pollute the real runtime folder
app_module.RUNTIME = Path(_tmp_runtime)

from app import app  # noqa: E402
from src.run_store import RunStore  # noqa: E402

client = TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Helper: open a RunStore backed by the tmp runtime
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    return app_module.RUNTIME / "runs.db"


def _store() -> RunStore:
    """Return a RunStore connected to the test runtime DB."""
    return RunStore(_db_path())


def _make_run(tag: str, state: str = "completed") -> str:
    """Insert a run row directly into the test DB and return its run_id."""
    run_id = uuid.uuid4().hex
    with _store() as store:
        store.create_run(run_id, tag, config={"provider": "openrouter", "model": "gpt-oss-120b"}, state=state)
    return run_id


def _acquire_lock(tag: str, run_id: str) -> str:
    """Acquire a live tag lock with a real PID so reconcile won't release it."""
    owner_id = f"test-owner-{uuid.uuid4().hex}"
    with _store() as store:
        store.acquire_tag_lock(
            tag,
            owner_id=owner_id,
            run_id=run_id,
            pid=os.getpid(),  # real PID so the lock looks alive
            now=time.time(),
        )
    return owner_id


def _release_lock(tag: str, owner_id: str) -> None:
    with _store() as store:
        store.release_tag_lock(tag, owner_id)


# ---------------------------------------------------------------------------
# 1. POST /api/analyze — success path
# ---------------------------------------------------------------------------

def test_analyze_returns_run_id_and_job_id():
    """A fresh /api/analyze call returns run_id and job_id when no lock is held."""
    tag = f"test_analyze_{uuid.uuid4().hex[:8]}"
    with patch("app.start_job", return_value={"job_id": "j1", "state": "running", "pid": 12345}):
        resp = client.post("/api/analyze", json={"tag": tag})
    assert resp.status_code == 200
    body = resp.json()
    assert "run_id" in body
    assert "job_id" in body
    assert len(body["run_id"]) > 0
    assert len(body["job_id"]) > 0


def test_analyze_run_is_resolvable_before_response_returns():
    tag = f"test_analyze_immediate_{uuid.uuid4().hex[:8]}"
    with patch(
        "app.start_job",
        return_value={"job_id": "job-immediate", "state": "running", "pid": os.getpid()},
    ):
        response = client.post("/api/analyze", json={"tag": tag})

    assert response.status_code == 200
    run_id = response.json()["run_id"]
    status = client.get(f"/api/pipeline/status?tag={tag}&run_id={run_id}")
    assert status.status_code == 200
    assert status.json()["run_id"] == run_id


def test_analyze_second_request_gets_409_without_orphan_row():
    tag = f"test_analyze_concurrent_{uuid.uuid4().hex[:8]}"
    with patch(
        "app.start_job",
        return_value={"job_id": "job-concurrent", "state": "running", "pid": os.getpid()},
    ):
        first = client.post("/api/analyze", json={"tag": tag})
        second = client.post("/api/analyze", json={"tag": tag})

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["active_run_id"] == first.json()["run_id"]
    with _store() as store:
        assert len(store.list_runs(tag)) == 1


def test_analyze_spawn_failure_marks_run_failed_and_releases_lock():
    tag = f"test_analyze_spawn_failure_{uuid.uuid4().hex[:8]}"
    with patch("app.start_job", side_effect=OSError("spawn failed")):
        response = client.post("/api/analyze", json={"tag": tag})

    assert response.status_code == 500
    with _store() as store:
        runs = store.list_runs(tag)
        assert len(runs) == 1
        assert runs[0]["state"] == "failed"
        assert "spawn failed" in (runs[0]["warning"] or "")
        assert store.get_tag_lock(tag) is None


# ---------------------------------------------------------------------------
# 2. POST /api/analyze — 409 when tag lock is held
# ---------------------------------------------------------------------------

def test_analyze_rejects_when_tag_lock_held():
    """A /api/analyze call returns 409 when another run holds the tag lock."""
    tag = f"test_analyze_locked_{uuid.uuid4().hex[:8]}"
    run_id = _make_run(tag)
    owner = _acquire_lock(tag, run_id)
    try:
        with patch("app.start_job", return_value={"job_id": "j2", "state": "running", "pid": 9999}):
            resp = client.post("/api/analyze", json={"tag": tag})
        assert resp.status_code == 409
        conflict = resp.json()
        assert conflict["error"] == "run_active"
        assert conflict["active_run_id"] == run_id
    finally:
        _release_lock(tag, owner)


# ---------------------------------------------------------------------------
# 3. GET /api/pipeline/status — returns run + steps
# ---------------------------------------------------------------------------

def test_pipeline_status_returns_step_list():
    """pipeline/status returns the run dict with a steps list."""
    tag = f"test_status_{uuid.uuid4().hex[:8]}"
    run_id = uuid.uuid4().hex
    with _store() as store:
        store.create_run(run_id, tag, config={}, state="completed")
        store.upsert_step(run_id, "prepare", state="completed")
        store.upsert_step(run_id, "classify", state="completed")

    resp = client.get(f"/api/pipeline/status?tag={tag}&run_id={run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == run_id
    assert isinstance(body["steps"], list)
    step_names = [s["name"] for s in body["steps"]]
    assert "prepare" in step_names
    assert "classify" in step_names


# ---------------------------------------------------------------------------
# 4. GET /api/pipeline/runs — returns a list
# ---------------------------------------------------------------------------

def test_pipeline_runs_returns_list():
    """pipeline/runs returns a JSON list (possibly empty)."""
    tag = f"test_runs_{uuid.uuid4().hex[:8]}"
    resp = client.get(f"/api/pipeline/runs?tag={tag}&limit=5")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# 5. POST /api/pipeline/cancel — returns cancelled: True
# ---------------------------------------------------------------------------

def test_pipeline_cancel_terminates_job():
    """cancel endpoint returns cancelled:True when lock matches and no actual process is running."""
    tag = f"test_cancel_{uuid.uuid4().hex[:8]}"
    run_id = _make_run(tag)
    owner = _acquire_lock(tag, run_id)

    # find_active_job returns None — no real subprocess running in tests
    with patch("app.find_active_job", return_value=None):
        resp = client.post("/api/pipeline/cancel", json={"tag": tag, "run_id": run_id})

    assert resp.status_code == 200
    body = resp.json()
    assert body["cancelled"] is True
    assert body["state"] == "cancelled"

    # Verify the run state was updated and lock was released
    with _store() as store:
        run = store.get_run(run_id)
        assert run["state"] == "cancelled"
        assert store.get_tag_lock(tag) is None


# ---------------------------------------------------------------------------
# 6. POST /api/pipeline/retry — resets steps and re-launches
# ---------------------------------------------------------------------------

def test_pipeline_retry_resets_steps_and_relaunches():
    """retry endpoint resets affected steps to pending and calls start_job."""
    tag = f"test_retry_{uuid.uuid4().hex[:8]}"
    run_id = uuid.uuid4().hex

    # Set up a completed run with a failed classify step
    with _store() as store:
        store.create_run(run_id, tag, config={"provider": "openrouter", "model": "gpt-oss-120b", "n_clusters": 10}, state="failed")
        for step in ["prepare", "classify", "briefing", "trends", "trend_pdf", "qa_index"]:
            store.upsert_step(run_id, step, state="completed" if step == "prepare" else "failed")

    with patch("app.start_job", return_value={"job_id": "j-retry", "state": "running", "pid": 1}) as mock_start:
        resp = client.post("/api/pipeline/retry", json={"tag": tag, "run_id": run_id, "step": "classify"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == run_id
    assert "job_id" in body
    mock_start.assert_called_once()

    # Steps in the retry set (classify and its dependents) should be reset to pending
    with _store() as store:
        for step in ["classify", "briefing", "trends", "trend_pdf", "qa_index"]:
            row = store.get_step(run_id, step)
            assert row is not None
            assert row["state"] == "pending", f"{step} should be pending after retry, got {row['state']}"


# ---------------------------------------------------------------------------
# 7. POST /api/upload — blocked when run is active
# ---------------------------------------------------------------------------

def test_upload_blocked_when_run_active():
    """Upload is rejected with 409 when the tag lock is held."""
    tag = f"test_upload_lock_{uuid.uuid4().hex[:8]}"
    run_id = _make_run(tag)
    owner = _acquire_lock(tag, run_id)
    try:
        resp = client.post(
            f"/api/upload?tag={tag}",
            files={"file": ("test.csv", b"id,title\n1,hello", "text/csv")},
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error"] == "run_active"
    finally:
        _release_lock(tag, owner)


# ---------------------------------------------------------------------------
# 8. POST /api/classify/job — blocked when run is active
# ---------------------------------------------------------------------------

def test_classify_job_blocked_when_run_active():
    """classify/job is rejected with 409 when the tag lock is held."""
    tag = f"test_classify_lock_{uuid.uuid4().hex[:8]}"
    run_id = _make_run(tag)
    owner = _acquire_lock(tag, run_id)
    try:
        resp = client.post("/api/classify/job", json={"tag": tag, "provider": "openrouter"})
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error"] == "run_active"
    finally:
        _release_lock(tag, owner)


# ---------------------------------------------------------------------------
# 9. POST /api/pipeline/retry — blocked when tag lock is held
# ---------------------------------------------------------------------------

def test_retry_blocked_when_run_active():
    """retry is rejected with 409 when the tag lock is held by any active run."""
    tag = f"test_retry_lock_{uuid.uuid4().hex[:8]}"
    run_id = _make_run(tag, state="failed")
    owner = _acquire_lock(tag, run_id)
    try:
        resp = client.post(
            "/api/pipeline/retry",
            json={"tag": tag, "run_id": run_id, "step": "classify"},
        )
        assert resp.status_code == 409
        conflict = resp.json()
        assert conflict["error"] == "run_active"
        assert conflict["active_run_id"] == run_id
    finally:
        _release_lock(tag, owner)


# ---------------------------------------------------------------------------
# 10. GET /api/pipeline/artifact — rejects paths outside snapshot root
# ---------------------------------------------------------------------------

def test_artifact_rejects_non_snapshot_path():
    """Artifact endpoint returns 404 when the stored path escapes the snapshot root."""
    import json as _json

    tag = f"test_artifact_traversal_{uuid.uuid4().hex[:8]}"
    run_id = uuid.uuid4().hex
    step = "classify"

    # Create a run and a completed attempt whose artifact points to a data/ path
    # that is under runtime/<tag>/ but NOT under runtime/<tag>/runs/<run_id>/
    fake_artifact_path = str(app_module.RUNTIME / tag / "data" / "something.csv")

    with _store() as store:
        store.create_run(run_id, tag, config={}, state="completed")
        store.upsert_step(run_id, step, state="completed")
        # Inject an attempt row directly with the rogue artifact path.
        # Use BEGIN IMMEDIATE / COMMIT because isolation_level=None means autocommit.
        store._conn.execute("BEGIN IMMEDIATE")
        store._conn.execute(
            """
            INSERT INTO attempts
                (run_id, step, attempt_no, state, started_at, log_path, artifacts_json, metadata_json)
            VALUES (?, ?, 1, 'completed', ?, '', ?, '{}')
            """,
            (
                run_id,
                step,
                time.time(),
                _json.dumps([{"path": fake_artifact_path, "name": "something.csv", "type": "csv"}]),
            ),
        )
        store._conn.execute("COMMIT")

    resp = client.get(f"/api/pipeline/artifact?tag={tag}&run_id={run_id}&step={step}&id=0")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/pipeline/log
# ---------------------------------------------------------------------------

def test_pipeline_log_returns_text_for_existing_attempt():
    """Log endpoint returns text/plain content when an attempt log exists."""
    from src.run_store import ensure_attempt_layout

    tag = f"test_log_{uuid.uuid4().hex[:8]}"
    run_id = uuid.uuid4().hex
    step = "classify"

    # Create run, step, and attempt with a real log file.
    paths = ensure_attempt_layout(app_module.RUNTIME, tag, run_id, step, 1)
    paths.log_path.write_text("classify started\nclassify done\n", encoding="utf-8")

    with _store() as store:
        store.create_run(run_id, tag, config={})
        store.upsert_step(run_id, step, state="completed")
        attempt = store.start_attempt(run_id, step, log_path=str(paths.log_path))
        store.finish_attempt(run_id, step, attempt_no=attempt["attempt_no"], state="completed", processed=3, total=3)

    resp = client.get(f"/api/pipeline/log?tag={tag}&run_id={run_id}&step={step}")
    assert resp.status_code == 200
    assert "classify done" in resp.text


def test_pipeline_log_returns_404_for_unknown_run():
    """Log endpoint returns 404 when the run_id does not exist."""
    resp = client.get(f"/api/pipeline/log?tag=nonexistent&run_id=doesnotexist&step=classify")
    assert resp.status_code == 404
