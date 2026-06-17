"""Phase 2 durable job system tests.

Tests are written FIRST (TDD). The modules under test (src/jobs.py,
scripts/classify_job.py) do not exist yet, so these tests will fail on import
until the implementation is added.

Covers:
- write_status / read_status: atomic JSON status file I/O
- reconcile: detect interrupted jobs by pid liveness and heartbeat staleness
- find_active_job: scan job dir for the first non-terminal job matching a kind
- start_job: spawn a subprocess and write initial status
- run_classify_job (scripts): classify pending rows and update status
- /api/classify/job and /api/classify/status FastAPI endpoints
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _make_row(**kwargs) -> dict:
    """Minimal analyzed row with all required fields."""
    defaults = {
        "source_id": "r1",
        "source_type": "comment",
        "post_id_norm": "p1",
        "subreddit_norm": "Silverado",
        "title_norm": "Good title",
        "target_text": "This is a long enough comment to pass junk check",
        "combined_text": "context text",
        "score_norm": 10,
        "created_at_norm": "2024-01-01",
        "permalink_norm": "/r/Silverado/comments/abc",
        "skip_classification": False,
        "classifier_mode": "heuristic_preview",
        "sentiment": "neutral",
        "complaint": 0,
        "competitor_mention": 0,
        "dealer_experience": 0,
        "reliability_concern": 0,
        "software_tech_issue": 0,
        "purchase_intent": 0,
        "loyalty_signal": 0,
        "ev_topic": 0,
        "enthusiast_mod": 0,
        "classic_vintage": 0,
        "top_complaint_category": "not_applicable",
        "multi_complaint_categories": "",
        "vehicle_mentioned": "silverado",
        "comment_type": "shared_experience",
        "competitor_brand": "none",
        "issue_severity": "none",
        "description": "A neutral comment.",
        "engagement_level": "medium",
    }
    defaults.update(kwargs)
    return defaults


def _fake_label() -> dict:
    """Minimal valid LLM label dict for mocking classify_with_llm."""
    return {
        "sentiment": "neutral",
        "complaint": 0,
        "competitor_mention": 0,
        "dealer_experience": 0,
        "reliability_concern": 0,
        "software_tech_issue": 0,
        "purchase_intent": 0,
        "loyalty_signal": 0,
        "ev_topic": 0,
        "enthusiast_mod": 0,
        "classic_vintage": 0,
        "top_complaint_category": "not_applicable",
        "multi_complaint_categories": [],
        "vehicle_mentioned": "unknown",
        "comment_type": "question",
        "competitor_brand": "none",
        "issue_severity": "none",
        "description": "Test description from mock LLM.",
    }


# ---------------------------------------------------------------------------
# TestWriteStatus
# ---------------------------------------------------------------------------

class TestWriteStatus:
    """write_status writes atomically to a JSON file and merges with existing data."""

    def test_creates_parent_dirs(self, tmp_path):
        # The path has nested dirs that don't exist yet
        from src.jobs import write_status
        deep_path = tmp_path / "a" / "b" / "c" / "job.json"
        write_status(deep_path, {"job_id": "j1", "state": "running"})
        # Parent dirs must have been created
        assert deep_path.exists()

    def test_required_fields_written(self, tmp_path):
        # JSON must contain job_id, state, and updated_at
        from src.jobs import write_status
        path = tmp_path / "status.json"
        write_status(path, {"job_id": "j1", "state": "running"})
        data = json.loads(path.read_text())
        assert "job_id" in data
        assert "state" in data
        assert "updated_at" in data

    def test_merges_into_existing_file(self, tmp_path):
        # Second write preserves earlier fields and overwrites updated ones
        from src.jobs import write_status
        path = tmp_path / "status.json"
        # First write sets original_field
        write_status(path, {"job_id": "j1", "state": "running", "original_field": "keep_me"})
        # Second write updates state only
        write_status(path, {"job_id": "j1", "state": "completed"})
        data = json.loads(path.read_text())
        # Both old and new fields must be present
        assert data["original_field"] == "keep_me"
        assert data["state"] == "completed"

    def test_no_tmp_file_leftover(self, tmp_path):
        # Atomic write must clean up; no .tmp file may remain
        from src.jobs import write_status
        path = tmp_path / "status.json"
        write_status(path, {"job_id": "j1", "state": "running"})
        tmp_file = path.with_suffix(".tmp")
        assert not tmp_file.exists()


# ---------------------------------------------------------------------------
# TestReadStatus
# ---------------------------------------------------------------------------

class TestReadStatus:
    """read_status returns None for missing/corrupt files, dict for valid JSON."""

    def test_returns_none_for_missing_file(self, tmp_path):
        from src.jobs import read_status
        missing = tmp_path / "does_not_exist.json"
        assert read_status(missing) is None

    def test_returns_none_for_corrupt_json(self, tmp_path):
        from src.jobs import read_status
        bad = tmp_path / "corrupt.json"
        bad.write_text("not json")
        assert read_status(bad) is None

    def test_returns_dict_for_valid_completed_file(self, tmp_path):
        from src.jobs import read_status
        valid = tmp_path / "status.json"
        payload = {"job_id": "j1", "state": "completed", "processed": 10}
        valid.write_text(json.dumps(payload))
        result = read_status(valid)
        assert isinstance(result, dict)
        assert result["state"] == "completed"
        assert result["job_id"] == "j1"


# ---------------------------------------------------------------------------
# TestReconcile
# ---------------------------------------------------------------------------

class TestReconcile:
    """reconcile(status) detects interrupted jobs and returns updated state."""

    def test_completed_job_unchanged(self):
        from src.jobs import reconcile
        status = {"state": "completed", "pid": 99999999, "heartbeat_at": 0}
        result = reconcile(status)
        assert result["state"] == "completed"

    def test_failed_job_unchanged(self):
        from src.jobs import reconcile
        status = {"state": "failed", "pid": 99999999, "heartbeat_at": 0}
        result = reconcile(status)
        assert result["state"] == "failed"

    def test_interrupted_job_unchanged(self):
        from src.jobs import reconcile
        status = {"state": "interrupted", "pid": 99999999, "heartbeat_at": 0}
        result = reconcile(status)
        assert result["state"] == "interrupted"

    def test_dead_pid_marks_running_as_interrupted(self):
        # PID 99999999 is almost certainly not alive
        from src.jobs import reconcile
        status = {
            "state": "running",
            "pid": 99999999,
            "heartbeat_at": time.time(),
        }
        result = reconcile(status)
        assert result["state"] == "interrupted"

    def test_stale_heartbeat_marks_running_as_interrupted(self):
        # Own PID is alive, but heartbeat was 300s ago — job is stale
        from src.jobs import reconcile
        status = {
            "state": "running",
            "pid": os.getpid(),
            "heartbeat_at": time.time() - 300,
        }
        result = reconcile(status)
        assert result["state"] == "interrupted"

    def test_alive_pid_fresh_heartbeat_stays_running(self):
        # Own PID is alive and heartbeat is fresh — still running
        from src.jobs import reconcile
        status = {
            "state": "running",
            "pid": os.getpid(),
            "heartbeat_at": time.time(),
        }
        result = reconcile(status)
        assert result["state"] == "running"

    def test_reconcile_does_not_mutate_input(self):
        # reconcile must return a new dict and leave the original untouched
        from src.jobs import reconcile
        original_state = "running"
        status = {
            "state": original_state,
            "pid": 99999999,
            "heartbeat_at": time.time(),
        }
        _ = reconcile(status)
        assert status["state"] == original_state


# ---------------------------------------------------------------------------
# TestFindActiveJob
# ---------------------------------------------------------------------------

class TestFindActiveJob:
    """find_active_job scans job files and returns the first non-terminal job of the right kind."""

    def _write_job(self, runtime_root, tag, job_id, **fields):
        """Write a job status file with the given fields."""
        from src.jobs import write_status, job_path
        path = job_path(runtime_root, tag, job_id)
        payload = {
            "job_id": job_id,
            "tag": tag,
            "kind": "classify",
            "state": "running",
            "pid": os.getpid(),
            "heartbeat_at": time.time(),
            "started_at": time.time(),
        }
        payload.update(fields)
        write_status(path, payload)
        return path

    def test_returns_none_when_no_jobs(self, tmp_path):
        from src.jobs import find_active_job
        result = find_active_job(tmp_path, "test_tag", "classify")
        assert result is None

    def test_returns_none_when_all_terminal(self, tmp_path):
        # Only completed and failed jobs — none are active
        from src.jobs import find_active_job
        self._write_job(tmp_path, "test_tag", "j1", state="completed")
        self._write_job(tmp_path, "test_tag", "j2", state="failed")
        result = find_active_job(tmp_path, "test_tag", "classify")
        assert result is None

    def test_returns_running_job_with_alive_pid(self, tmp_path):
        # Running job with own PID and fresh heartbeat should be returned
        from src.jobs import find_active_job
        self._write_job(tmp_path, "test_tag", "j1")
        result = find_active_job(tmp_path, "test_tag", "classify")
        assert result is not None
        assert result["job_id"] == "j1"

    def test_ignores_different_kind(self, tmp_path):
        # pdf_export jobs should not be returned when searching for classify
        from src.jobs import find_active_job
        self._write_job(tmp_path, "test_tag", "j1", kind="pdf_export")
        result = find_active_job(tmp_path, "test_tag", "classify")
        assert result is None

    def test_dead_pid_job_not_returned_as_active(self, tmp_path):
        # reconcile should mark the dead-pid job as interrupted; not returned
        from src.jobs import find_active_job
        self._write_job(
            tmp_path, "test_tag", "j1",
            pid=99999999,
            heartbeat_at=time.time(),
        )
        result = find_active_job(tmp_path, "test_tag", "classify")
        assert result is None


# ---------------------------------------------------------------------------
# TestStartJob
# ---------------------------------------------------------------------------

class TestStartJob:
    """start_job spawns a subprocess and writes the initial status file."""

    def test_creates_job_status_file(self, tmp_path):
        # A JSON status file must appear under job_dir after start_job
        from src.jobs import start_job, job_dir
        script = tmp_path / "noop.py"
        script.write_text("import sys; sys.exit(0)")
        status = start_job(
            runtime_root=tmp_path,
            tag="test_tag",
            kind="classify",
            script=script,
            extra_args=[],
            cwd=tmp_path,
        )
        jdir = job_dir(tmp_path, "test_tag")
        job_files = list(jdir.glob("*.json"))
        assert len(job_files) >= 1

    def test_returns_required_status_fields(self, tmp_path):
        # Returned dict must have every required field
        from src.jobs import start_job
        script = tmp_path / "noop.py"
        script.write_text("import sys; sys.exit(0)")
        status = start_job(
            runtime_root=tmp_path,
            tag="test_tag",
            kind="classify",
            script=script,
            extra_args=[],
            cwd=tmp_path,
        )
        required = [
            "job_id", "tag", "kind", "state", "pid",
            "started_at", "processed", "total", "errors",
            "heartbeat_at", "completed_at", "artifact_paths",
        ]
        for field in required:
            assert field in status, f"Missing required field: {field}"

    def test_status_has_correct_tag_and_kind(self, tmp_path):
        from src.jobs import start_job
        script = tmp_path / "noop.py"
        script.write_text("import sys; sys.exit(0)")
        status = start_job(
            runtime_root=tmp_path,
            tag="my_tag",
            kind="classify",
            script=script,
            extra_args=[],
            cwd=tmp_path,
        )
        assert status["tag"] == "my_tag"
        assert status["kind"] == "classify"

    def test_pid_is_set(self, tmp_path):
        # pid must be a positive integer (the spawned process)
        from src.jobs import start_job
        script = tmp_path / "noop.py"
        script.write_text("import time; time.sleep(0.2)")
        status = start_job(
            runtime_root=tmp_path,
            tag="test_tag",
            kind="classify",
            script=script,
            extra_args=[],
            cwd=tmp_path,
        )
        assert isinstance(status["pid"], int)
        assert status["pid"] > 0


# ---------------------------------------------------------------------------
# TestClassifyJobRun
# ---------------------------------------------------------------------------

class TestClassifyJobRun:
    """run_classify_job classifies pending rows and writes status updates."""

    # ------ instance helpers ------

    def _write_classified(self, tmp_path, rows):
        """Write rows to the canonical classified CSV path and return the path."""
        from src.gm_insights import save_classified
        path = tmp_path / "classified" / "classified_posts.csv"
        save_classified(pd.DataFrame(rows), path)
        return path

    def _write_initial_status(self, runtime_root, tag, job_id):
        """Write a running status file and return its path."""
        from src.jobs import write_status, job_path
        path = job_path(runtime_root, tag, job_id)
        write_status(path, {
            "job_id": job_id,
            "tag": tag,
            "kind": "classify",
            "state": "running",
            "pid": os.getpid(),
        })
        return path

    # ------ tests ------

    def test_classifies_only_pending_rows(self, tmp_path):
        # Rows with classifier_mode="" are pending and should get mode="llm".
        # Rows already classified must remain unchanged.
        from scripts.classify_job import run_classify_job

        job_id = str(uuid.uuid4())
        runtime_root = tmp_path / "runtime"
        tag = "test_tag"
        pending = _make_row(source_id="p1", classifier_mode="")
        done = _make_row(source_id="p2", classifier_mode="llm", sentiment="positive")
        cpath = self._write_classified(tmp_path, [pending, done])
        self._write_initial_status(runtime_root, tag, job_id)

        with patch("scripts.classify_job.classify_with_llm", return_value=_fake_label()):
            run_classify_job(
                classified_path=cpath,
                runtime_root=runtime_root,
                tag=tag,
                job_id=job_id,
            )

        from src.gm_insights import load_classified
        result = load_classified(cpath)
        # Pending row got classified
        p1 = result[result["source_id"] == "p1"].iloc[0]
        assert p1["classifier_mode"] == "llm"
        # Already-done row unchanged
        p2 = result[result["source_id"] == "p2"].iloc[0]
        assert p2["sentiment"] == "positive"

    def test_status_completed_after_successful_run(self, tmp_path):
        # Status file must have state="completed", processed=1, and artifact_paths with the CSV path.
        from scripts.classify_job import run_classify_job
        from src.jobs import read_status, job_path

        job_id = str(uuid.uuid4())
        runtime_root = tmp_path / "runtime"
        tag = "test_tag"
        pending = _make_row(source_id="p1", classifier_mode="")
        cpath = self._write_classified(tmp_path, [pending])
        self._write_initial_status(runtime_root, tag, job_id)

        with patch("scripts.classify_job.classify_with_llm", return_value=_fake_label()):
            run_classify_job(
                classified_path=cpath,
                runtime_root=runtime_root,
                tag=tag,
                job_id=job_id,
            )

        status = read_status(job_path(runtime_root, tag, job_id))
        assert status is not None
        assert status["state"] == "completed"
        assert status["processed"] == 1
        assert str(cpath) in status["artifact_paths"]

    def test_error_rows_written_when_llm_fails(self, tmp_path):
        # When classify_with_llm raises, the row gets classifier_mode="error"
        # and sentiment="error"; status has errors=1 and state="completed".
        from scripts.classify_job import run_classify_job
        from src.jobs import read_status, job_path

        job_id = str(uuid.uuid4())
        runtime_root = tmp_path / "runtime"
        tag = "test_tag"
        pending = _make_row(source_id="p1", classifier_mode="")
        cpath = self._write_classified(tmp_path, [pending])
        self._write_initial_status(runtime_root, tag, job_id)

        with patch("scripts.classify_job.classify_with_llm", side_effect=RuntimeError("LLM boom")):
            run_classify_job(
                classified_path=cpath,
                runtime_root=runtime_root,
                tag=tag,
                job_id=job_id,
            )

        from src.gm_insights import load_classified
        result = load_classified(cpath)
        p1 = result[result["source_id"] == "p1"].iloc[0]
        assert p1["classifier_mode"] == "error"
        assert p1["sentiment"] == "error"

        status = read_status(job_path(runtime_root, tag, job_id))
        assert status["errors"] == 1
        assert status["state"] == "completed"

    def test_limit_restricts_rows_processed(self, tmp_path):
        # With 5 pending rows and limit=2, only 2 should get classified.
        from scripts.classify_job import run_classify_job

        job_id = str(uuid.uuid4())
        runtime_root = tmp_path / "runtime"
        tag = "test_tag"
        rows = [_make_row(source_id=f"p{i}", classifier_mode="") for i in range(5)]
        cpath = self._write_classified(tmp_path, rows)
        self._write_initial_status(runtime_root, tag, job_id)

        call_count = {"n": 0}
        def counting_llm(text, provider):
            call_count["n"] += 1
            return _fake_label()

        with patch("scripts.classify_job.classify_with_llm", side_effect=counting_llm):
            run_classify_job(
                classified_path=cpath,
                runtime_root=runtime_root,
                tag=tag,
                job_id=job_id,
                limit=2,
            )

        assert call_count["n"] == 2

    def test_skip_classification_rows_not_processed(self, tmp_path):
        # Rows with skip_classification=True must never reach the LLM.
        from scripts.classify_job import run_classify_job

        job_id = str(uuid.uuid4())
        runtime_root = tmp_path / "runtime"
        tag = "test_tag"
        # Both rows have classifier_mode="" but one is skip_classification=True
        skip_row = _make_row(source_id="skip1", classifier_mode="", skip_classification=True)
        normal_row = _make_row(source_id="norm1", classifier_mode="")
        cpath = self._write_classified(tmp_path, [skip_row, normal_row])
        self._write_initial_status(runtime_root, tag, job_id)

        with patch("scripts.classify_job.classify_with_llm", side_effect=lambda text, provider: _fake_label()) as mock_llm:
            run_classify_job(
                classified_path=cpath,
                runtime_root=runtime_root,
                tag=tag,
                job_id=job_id,
            )
            # LLM called exactly once (for norm1, not skip1)
            assert mock_llm.call_count == 1


# ---------------------------------------------------------------------------
# TestClassifyJobEndpoint
# ---------------------------------------------------------------------------

class TestClassifyJobEndpoint:
    """FastAPI endpoints: POST /api/classify/job and GET /api/classify/status."""

    @pytest.fixture
    def client_and_runtime(self, tmp_path, monkeypatch):
        import app as app_module
        # Point the app at a fresh tmp runtime so tests are isolated
        monkeypatch.setattr(app_module, "RUNTIME", tmp_path / "runtime")
        from fastapi.testclient import TestClient
        return TestClient(app_module.app), tmp_path / "runtime"

    def _fake_job_status(self, tag: str = "test_tag") -> dict:
        """Minimal job status dict with all required fields."""
        return {
            "job_id": str(uuid.uuid4()),
            "tag": tag,
            "kind": "classify",
            "state": "running",
            "pid": os.getpid(),
            "started_at": time.time(),
            "processed": 0,
            "total": 0,
            "errors": 0,
            "heartbeat_at": time.time(),
            "completed_at": None,
            "artifact_paths": [],
        }

    def test_returns_400_when_no_classified_csv(self, client_and_runtime):
        # POST /api/classify/job should 400 when no classified CSV exists for the tag
        client, runtime = client_and_runtime
        response = client.post("/api/classify/job", json={"tag": "test_tag"})
        assert response.status_code == 400

    def test_starts_job_and_returns_started_true(self, client_and_runtime, tmp_path):
        # With CSV present and start_job mocked, endpoint returns started=True
        client, runtime = client_and_runtime
        import app as app_module

        # Create the classified CSV at the path the app expects
        cpath = runtime / "test_tag" / "classified" / "classified_posts.csv"
        cpath.parent.mkdir(parents=True, exist_ok=True)
        from src.gm_insights import save_classified
        save_classified(pd.DataFrame([_make_row(classifier_mode="")]), cpath)

        fake_status = self._fake_job_status("test_tag")
        with patch("app.start_job", return_value=fake_status) as mock_start:
            response = client.post("/api/classify/job", json={"tag": "test_tag"})

        assert response.status_code == 200
        data = response.json()
        assert data.get("started") is True
        mock_start.assert_called_once()

    def test_does_not_double_start_running_job(self, client_and_runtime):
        # If find_active_job returns an existing job, start_job must NOT be called
        client, runtime = client_and_runtime
        import app as app_module

        # Create classified CSV so we pass the 400 guard
        cpath = runtime / "test_tag" / "classified" / "classified_posts.csv"
        cpath.parent.mkdir(parents=True, exist_ok=True)
        from src.gm_insights import save_classified
        save_classified(pd.DataFrame([_make_row(classifier_mode="")]), cpath)

        existing = self._fake_job_status("test_tag")
        with patch("app.find_active_job", return_value=existing):
            with patch("app.start_job") as mock_start:
                response = client.post("/api/classify/job", json={"tag": "test_tag"})

        assert response.status_code == 200
        data = response.json()
        assert data.get("started") is False
        mock_start.assert_not_called()

    def test_classify_status_returns_idle_when_no_jobs(self, client_and_runtime):
        # GET /api/classify/status with no jobs should return state="idle"
        client, runtime = client_and_runtime
        with patch("app.find_active_job", return_value=None):
            response = client.get("/api/classify/status", params={"tag": "test_tag"})
        assert response.status_code == 200
        data = response.json()
        assert data.get("state") == "idle"
