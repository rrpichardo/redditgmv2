# Phase 2: Durable Job System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ad-hoc in-memory subprocess tracking with a disk-backed job system so long-running classification, export, trend, and Q&A jobs survive server restarts and are observable via status files.

**Architecture:** Each job gets a UUID, writes status to `runtime/<tag>/jobs/<job_id>.json`, and updates heartbeats so the server can reconcile stale/dead jobs to `interrupted` on read. Jobs are spawned as subprocesses via `subprocess.Popen`; no threads or in-memory state. A single-write-per-kind lock prevents double-starts.

**Tech Stack:** Python stdlib (`subprocess`, `os`, `uuid`, `json`, `time`), FastAPI, existing `src/gm_insights.py` for classify logic. No new external dependencies.

---

## File Map

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `src/jobs.py` | write_status, read_status, reconcile, find_active_job, start_job |
| Create | `scripts/__init__.py` | Makes scripts/ a package importable in tests |
| Create | `scripts/classify_job.py` | Full-run LLM classification subprocess (run_classify_job + main) |
| Create | `scripts/pdf_export_job.py` | PDF export stub (Phase 3 fills real impl) |
| Create | `scripts/trend_job.py` | Trend job stub (Phase 5 fills real impl) |
| Create | `scripts/faiss_qa_job.py` | FAISS Q&A stub (Phase 6 fills real impl) |
| Modify | `app.py` | Add POST /api/classify/job, GET /api/classify/status, POST /api/export/pdf-job, GET /api/export/status |
| Create | `tests/test_phase2.py` | All Phase 2 tests |

---

## Task 1: Write tests for src/jobs.py

**Files:**
- Create: `tests/test_phase2.py`

- [ ] **Step 1: Write all jobs.py tests**

```python
# tests/test_phase2.py
"""Phase 2: Durable Job System tests."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Shared helpers (duplicated from test_phase0 to keep files independent)
# ---------------------------------------------------------------------------

def _make_row(**kwargs) -> dict:
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
# src/jobs.py — write_status
# ---------------------------------------------------------------------------

class TestWriteStatus:
    def test_creates_parent_dirs(self, tmp_path):
        from src.jobs import write_status
        path = tmp_path / "deep" / "nested" / "job.json"
        write_status(path, {"job_id": "abc", "state": "running"})
        assert path.exists()

    def test_required_fields_written(self, tmp_path):
        from src.jobs import write_status
        path = tmp_path / "job.json"
        write_status(path, {"job_id": "abc", "state": "running", "kind": "classify"})
        data = json.loads(path.read_text())
        assert data["job_id"] == "abc"
        assert data["state"] == "running"
        # updated_at is added automatically
        assert "updated_at" in data

    def test_merges_into_existing_file(self, tmp_path):
        from src.jobs import write_status
        path = tmp_path / "job.json"
        write_status(path, {"job_id": "abc", "state": "pending"})
        write_status(path, {"state": "running", "pid": 42})
        data = json.loads(path.read_text())
        # Earlier fields preserved
        assert data["job_id"] == "abc"
        # Later fields overwrite
        assert data["state"] == "running"
        assert data["pid"] == 42

    def test_no_tmp_file_leftover(self, tmp_path):
        from src.jobs import write_status
        path = tmp_path / "job.json"
        write_status(path, {"state": "running"})
        assert not path.with_suffix(".tmp").exists()


# ---------------------------------------------------------------------------
# src/jobs.py — read_status
# ---------------------------------------------------------------------------

class TestReadStatus:
    def test_returns_none_for_missing_file(self, tmp_path):
        from src.jobs import read_status
        assert read_status(tmp_path / "nonexistent.json") is None

    def test_returns_none_for_corrupt_json(self, tmp_path):
        from src.jobs import read_status
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        assert read_status(path) is None

    def test_returns_dict_for_valid_completed_file(self, tmp_path):
        from src.jobs import read_status, write_status
        path = tmp_path / "job.json"
        write_status(path, {"job_id": "x", "state": "completed", "kind": "classify"})
        result = read_status(path)
        assert result is not None
        assert result["state"] == "completed"


# ---------------------------------------------------------------------------
# src/jobs.py — reconcile
# ---------------------------------------------------------------------------

class TestReconcile:
    def test_completed_job_unchanged(self):
        from src.jobs import reconcile
        status = {"state": "completed", "pid": 99999999}
        assert reconcile(status)["state"] == "completed"

    def test_failed_job_unchanged(self):
        from src.jobs import reconcile
        status = {"state": "failed", "pid": 99999999}
        assert reconcile(status)["state"] == "failed"

    def test_interrupted_job_unchanged(self):
        from src.jobs import reconcile
        status = {"state": "interrupted", "pid": 99999999}
        assert reconcile(status)["state"] == "interrupted"

    def test_dead_pid_marks_running_as_interrupted(self):
        from src.jobs import reconcile
        status = {
            "state": "running",
            "pid": 99999999,  # PID almost certainly dead
            "heartbeat_at": time.time(),
        }
        assert reconcile(status)["state"] == "interrupted"

    def test_stale_heartbeat_marks_running_as_interrupted(self):
        from src.jobs import reconcile
        status = {
            "state": "running",
            "pid": os.getpid(),  # current PID — alive
            "heartbeat_at": time.time() - 300,  # 5 minutes ago — stale
        }
        assert reconcile(status)["state"] == "interrupted"

    def test_alive_pid_fresh_heartbeat_stays_running(self):
        from src.jobs import reconcile
        status = {
            "state": "running",
            "pid": os.getpid(),  # alive
            "heartbeat_at": time.time(),  # fresh
        }
        assert reconcile(status)["state"] == "running"

    def test_reconcile_does_not_mutate_input(self):
        from src.jobs import reconcile
        status = {"state": "running", "pid": 99999999, "heartbeat_at": time.time()}
        original = dict(status)
        reconcile(status)
        assert status == original  # input unchanged


# ---------------------------------------------------------------------------
# src/jobs.py — find_active_job
# ---------------------------------------------------------------------------

class TestFindActiveJob:
    def _write_job(self, runtime_root, tag, job_id, kind, state, pid=None):
        from src.jobs import write_status, job_path
        path = job_path(runtime_root, tag, job_id)
        write_status(path, {
            "job_id": job_id,
            "tag": tag,
            "kind": kind,
            "state": state,
            "pid": pid if pid is not None else 99999999,
            "heartbeat_at": time.time(),
            "started_at": time.time(),
        })

    def test_returns_none_when_no_jobs(self, tmp_path):
        from src.jobs import find_active_job
        assert find_active_job(tmp_path, "my_tag", "classify") is None

    def test_returns_none_when_all_terminal(self, tmp_path):
        from src.jobs import find_active_job
        self._write_job(tmp_path, "my_tag", "abc", "classify", "completed")
        self._write_job(tmp_path, "my_tag", "def", "classify", "failed")
        assert find_active_job(tmp_path, "my_tag", "classify") is None

    def test_returns_running_job_with_alive_pid(self, tmp_path):
        from src.jobs import find_active_job
        self._write_job(tmp_path, "my_tag", "abc123", "classify", "running", pid=os.getpid())
        result = find_active_job(tmp_path, "my_tag", "classify")
        assert result is not None
        assert result["job_id"] == "abc123"

    def test_ignores_different_kind(self, tmp_path):
        from src.jobs import find_active_job
        self._write_job(tmp_path, "my_tag", "xyz", "pdf_export", "running", pid=os.getpid())
        assert find_active_job(tmp_path, "my_tag", "classify") is None

    def test_dead_pid_job_not_returned_as_active(self, tmp_path):
        from src.jobs import find_active_job
        # PID 99999999 is dead — reconcile will mark it interrupted
        self._write_job(tmp_path, "my_tag", "dead", "classify", "running", pid=99999999)
        assert find_active_job(tmp_path, "my_tag", "classify") is None


# ---------------------------------------------------------------------------
# src/jobs.py — start_job
# ---------------------------------------------------------------------------

class TestStartJob:
    def test_creates_job_status_file(self, tmp_path):
        from src.jobs import start_job, job_dir
        script = tmp_path / "noop.py"
        script.write_text("import sys; sys.exit(0)", encoding="utf-8")
        start_job(tmp_path, "test_tag", "classify", script, [], cwd=tmp_path)
        files = list(job_dir(tmp_path, "test_tag").glob("*.json"))
        assert len(files) == 1

    def test_returns_required_status_fields(self, tmp_path):
        from src.jobs import start_job
        script = tmp_path / "noop.py"
        script.write_text("import sys; sys.exit(0)", encoding="utf-8")
        status = start_job(tmp_path, "test_tag", "classify", script, [], cwd=tmp_path)
        for field in ["job_id", "tag", "kind", "state", "pid", "started_at",
                      "processed", "total", "errors", "heartbeat_at",
                      "completed_at", "artifact_paths"]:
            assert field in status, f"Missing field: {field}"

    def test_status_has_correct_tag_and_kind(self, tmp_path):
        from src.jobs import start_job
        script = tmp_path / "noop.py"
        script.write_text("import sys; sys.exit(0)", encoding="utf-8")
        status = start_job(tmp_path, "my_tag", "pdf_export", script, [], cwd=tmp_path)
        assert status["tag"] == "my_tag"
        assert status["kind"] == "pdf_export"

    def test_pid_is_set(self, tmp_path):
        from src.jobs import start_job
        script = tmp_path / "noop.py"
        script.write_text("import time; time.sleep(0.2)", encoding="utf-8")
        status = start_job(tmp_path, "test_tag", "classify", script, [], cwd=tmp_path)
        assert isinstance(status["pid"], int)
        assert status["pid"] > 0


# ---------------------------------------------------------------------------
# scripts/classify_job.py — run_classify_job
# ---------------------------------------------------------------------------

class TestClassifyJobRun:
    def _write_classified(self, tmp_path, rows):
        from src.gm_insights import save_classified
        path = tmp_path / "classified" / "classified_posts.csv"
        save_classified(pd.DataFrame(rows), path)
        return path

    def _write_initial_status(self, runtime_root, tag, job_id):
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

    def test_classifies_only_pending_rows(self, tmp_path):
        from scripts.classify_job import run_classify_job
        from src.gm_insights import ProviderConfig, load_classified

        rows = [
            _make_row(source_id="pending1", classifier_mode=""),
            _make_row(source_id="pending2", classifier_mode=""),
            _make_row(source_id="done", classifier_mode="heuristic_preview"),
        ]
        cpath = self._write_classified(tmp_path, rows)
        runtime_root = tmp_path / "runtime"
        status_path = self._write_initial_status(runtime_root, "test_tag", "job1")

        provider = ProviderConfig(
            provider="openrouter", model="t",
            base_url="https://example.com", api_key_env="X", api_key="fake",
        )
        with patch("scripts.classify_job.classify_with_llm", return_value=_fake_label()):
            run_classify_job("test_tag", "job1", runtime_root, cpath, provider)

        result = load_classified(cpath)
        pending1 = result[result["source_id"] == "pending1"].iloc[0]
        pending2 = result[result["source_id"] == "pending2"].iloc[0]
        done = result[result["source_id"] == "done"].iloc[0]
        assert pending1["classifier_mode"] == "llm"
        assert pending2["classifier_mode"] == "llm"
        assert done["classifier_mode"] == "heuristic_preview"  # unchanged

    def test_status_completed_after_successful_run(self, tmp_path):
        from scripts.classify_job import run_classify_job
        from src.gm_insights import ProviderConfig
        from src.jobs import read_status, job_path

        rows = [_make_row(source_id="p1", classifier_mode="")]
        cpath = self._write_classified(tmp_path, rows)
        runtime_root = tmp_path / "runtime"
        self._write_initial_status(runtime_root, "test_tag", "job2")
        spath = job_path(runtime_root, "test_tag", "job2")

        provider = ProviderConfig(
            provider="openrouter", model="t",
            base_url="https://example.com", api_key_env="X", api_key="fake",
        )
        with patch("scripts.classify_job.classify_with_llm", return_value=_fake_label()):
            run_classify_job("test_tag", "job2", runtime_root, cpath, provider)

        status = read_status(spath)
        assert status["state"] == "completed"
        assert status["processed"] == 1
        assert status["total"] == 1
        assert str(cpath) in status["artifact_paths"]

    def test_error_rows_written_when_llm_fails(self, tmp_path):
        from scripts.classify_job import run_classify_job
        from src.gm_insights import ProviderConfig, load_classified
        from src.jobs import read_status, job_path

        rows = [_make_row(source_id="fail_row", classifier_mode="")]
        cpath = self._write_classified(tmp_path, rows)
        runtime_root = tmp_path / "runtime"
        self._write_initial_status(runtime_root, "test_tag", "job3")
        spath = job_path(runtime_root, "test_tag", "job3")

        provider = ProviderConfig(
            provider="openrouter", model="t",
            base_url="https://example.com", api_key_env="X", api_key="fake",
        )
        with patch("scripts.classify_job.classify_with_llm", side_effect=RuntimeError("LLM down")):
            run_classify_job("test_tag", "job3", runtime_root, cpath, provider)

        result = load_classified(cpath)
        fail_row = result[result["source_id"] == "fail_row"].iloc[0]
        assert fail_row["classifier_mode"] == "error"
        assert fail_row["sentiment"] == "error"

        status = read_status(spath)
        assert status["errors"] == 1
        assert status["state"] == "completed"  # completed with errors, not failed

    def test_limit_restricts_rows_processed(self, tmp_path):
        from scripts.classify_job import run_classify_job
        from src.gm_insights import ProviderConfig, load_classified
        from src.jobs import read_status, job_path

        rows = [_make_row(source_id=f"r{i}", classifier_mode="") for i in range(5)]
        cpath = self._write_classified(tmp_path, rows)
        runtime_root = tmp_path / "runtime"
        self._write_initial_status(runtime_root, "test_tag", "job4")
        spath = job_path(runtime_root, "test_tag", "job4")

        provider = ProviderConfig(
            provider="openrouter", model="t",
            base_url="https://example.com", api_key_env="X", api_key="fake",
        )
        with patch("scripts.classify_job.classify_with_llm", return_value=_fake_label()):
            run_classify_job("test_tag", "job4", runtime_root, cpath, provider, limit=2)

        status = read_status(spath)
        assert status["total"] == 2
        assert status["processed"] == 2

        result = load_classified(cpath)
        llm_rows = result[result["classifier_mode"] == "llm"]
        assert len(llm_rows) == 2

    def test_skip_classification_rows_not_processed(self, tmp_path):
        from scripts.classify_job import run_classify_job
        from src.gm_insights import ProviderConfig, load_classified
        from src.jobs import read_status, job_path

        rows = [
            _make_row(source_id="junk", classifier_mode="", skip_classification=True),
            _make_row(source_id="ok", classifier_mode=""),
        ]
        cpath = self._write_classified(tmp_path, rows)
        runtime_root = tmp_path / "runtime"
        self._write_initial_status(runtime_root, "test_tag", "job5")

        provider = ProviderConfig(
            provider="openrouter", model="t",
            base_url="https://example.com", api_key_env="X", api_key="fake",
        )
        with patch("scripts.classify_job.classify_with_llm", return_value=_fake_label()) as mock_llm:
            run_classify_job("test_tag", "job5", runtime_root, cpath, provider)
        # Only 1 row eligible; junk was excluded
        assert mock_llm.call_count == 1


# ---------------------------------------------------------------------------
# app.py — new classify/job endpoints
# ---------------------------------------------------------------------------

class TestClassifyJobEndpoint:
    @pytest.fixture
    def client_and_runtime(self, tmp_path, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "RUNTIME", tmp_path / "runtime")
        from fastapi.testclient import TestClient
        return TestClient(app_module.app), tmp_path / "runtime"

    def test_returns_400_when_no_classified_csv(self, client_and_runtime):
        client, _ = client_and_runtime
        resp = client.post("/api/classify/job", json={"tag": "no_data", "api_key": "fake"})
        assert resp.status_code == 400

    def test_starts_job_and_returns_started_true(self, tmp_path, monkeypatch):
        import app as app_module
        runtime = tmp_path / "runtime"
        monkeypatch.setattr(app_module, "RUNTIME", runtime)
        from fastapi.testclient import TestClient

        # Create a classified CSV so the endpoint proceeds
        cpath = runtime / "test_tag" / "classified" / "classified_posts.csv"
        cpath.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([_make_row(source_id="p1", classifier_mode="")]).to_csv(cpath, index=False)

        # Mock start_job so no real subprocess spawns
        fake_status = {
            "job_id": "fakejob", "tag": "test_tag", "kind": "classify",
            "state": "running", "pid": 12345, "processed": 0, "total": 0,
            "errors": 0, "started_at": time.time(), "heartbeat_at": None,
            "updated_at": time.time(), "completed_at": None, "artifact_paths": [],
        }
        with patch("app.start_job", return_value=fake_status):
            client = TestClient(app_module.app)
            resp = client.post("/api/classify/job", json={"tag": "test_tag", "api_key": "fake"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["started"] is True
        assert data["job_id"] == "fakejob"

    def test_does_not_double_start_running_job(self, tmp_path, monkeypatch):
        import app as app_module
        runtime = tmp_path / "runtime"
        monkeypatch.setattr(app_module, "RUNTIME", runtime)
        from fastapi.testclient import TestClient

        cpath = runtime / "test_tag" / "classified" / "classified_posts.csv"
        cpath.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([_make_row()]).to_csv(cpath, index=False)

        existing = {
            "job_id": "alreadyrunning", "tag": "test_tag", "kind": "classify",
            "state": "running", "pid": os.getpid(), "processed": 5, "total": 10,
            "errors": 0, "started_at": time.time(), "heartbeat_at": time.time(),
            "updated_at": time.time(), "completed_at": None, "artifact_paths": [],
        }
        with patch("app.find_active_job", return_value=existing):
            with patch("app.start_job") as mock_start:
                client = TestClient(app_module.app)
                resp = client.post("/api/classify/job", json={"tag": "test_tag", "api_key": "fake"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["started"] is False
        mock_start.assert_not_called()

    def test_classify_status_returns_idle_when_no_jobs(self, client_and_runtime):
        client, _ = client_and_runtime
        resp = client.get("/api/classify/status", params={"tag": "empty_tag"})
        assert resp.status_code == 200
        assert resp.json()["state"] == "idle"
```

- [ ] **Step 2: Run tests to confirm they all fail (src/jobs.py and scripts don't exist yet)**

```bash
cd /Users/ricopichardo/Claude/redditgmv2/.claude/worktrees/priceless-ptolemy-b13de0
python -m pytest tests/test_phase2.py -x --tb=short 2>&1 | head -30
```

Expected: ModuleNotFoundError or ImportError for `src.jobs` and `scripts.classify_job`.

- [ ] **Step 3: Commit test file**

```bash
git add tests/test_phase2.py
git commit -m "test(phase2): add durable job system tests before implementation"
```

---

## Task 2: Implement src/jobs.py

**Files:**
- Create: `src/jobs.py`

- [ ] **Step 1: Write src/jobs.py**

```python
# src/jobs.py
"""Durable job system: disk-backed status files, heartbeat, stale reconciliation."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

# After this many seconds with no heartbeat update, a 'running' job is considered stale
HEARTBEAT_STALE_SECS = 120

JOB_KINDS = frozenset({"classify", "pdf_export", "trend", "faiss_qa"})


def job_dir(runtime_root: Path, tag: str) -> Path:
    return runtime_root / tag / "jobs"


def job_path(runtime_root: Path, tag: str, job_id: str) -> Path:
    return job_dir(runtime_root, tag) / f"{job_id}.json"


def _write_atomic(path: Path, data: dict[str, Any]) -> None:
    """Write JSON to a temp file then atomically replace the destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def write_status(path: Path, fields: dict[str, Any]) -> None:
    """Merge fields into the existing status file and write atomically.
    Automatically stamps updated_at on every write."""
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing.update(fields)
    existing["updated_at"] = time.time()
    _write_atomic(path, existing)


def _pid_alive(pid: int | None) -> bool:
    """Return True if pid exists in the process table."""
    if pid is None:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def reconcile(status: dict[str, Any]) -> dict[str, Any]:
    """If a 'running' job has a dead PID or stale heartbeat, return a copy marked 'interrupted'.
    Terminal states (completed, failed, interrupted) pass through unchanged."""
    if status.get("state") != "running":
        return status
    pid = status.get("pid")
    heartbeat_at = float(status.get("heartbeat_at") or 0)
    pid_dead = not _pid_alive(pid)
    heartbeat_stale = (time.time() - heartbeat_at) > HEARTBEAT_STALE_SECS
    if pid_dead or heartbeat_stale:
        out = dict(status)
        out["state"] = "interrupted"
        out["updated_at"] = time.time()
        return out
    return status


def read_status(path: Path) -> dict[str, Any] | None:
    """Read a job status file and reconcile it. Returns None if missing or corrupt."""
    if not path.exists():
        return None
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return reconcile(status)


def find_active_job(runtime_root: Path, tag: str, kind: str) -> dict[str, Any] | None:
    """Return the reconciled status of the most recent non-terminal job of a kind, or None."""
    d = job_dir(runtime_root, tag)
    if not d.exists():
        return None
    best: dict[str, Any] | None = None
    best_start = 0.0
    for path in d.glob("*.json"):
        status = read_status(path)
        if status is None:
            continue
        if status.get("kind") != kind:
            continue
        if status.get("state") in ("completed", "failed", "interrupted"):
            continue
        start = float(status.get("started_at", 0))
        if start > best_start:
            best = status
            best_start = start
    return best


def start_job(
    runtime_root: Path,
    tag: str,
    kind: str,
    script: Path,
    extra_args: list[str],
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Write a pending status file, spawn the script as a subprocess, then update to 'running'.
    Returns the initial status dict (with pid set).
    The caller passes cwd=ROOT so the subprocess can import src.* packages."""
    job_id = uuid.uuid4().hex
    status_path = job_path(runtime_root, tag, job_id)
    now = time.time()
    initial: dict[str, Any] = {
        "job_id": job_id,
        "tag": tag,
        "kind": kind,
        "state": "pending",
        "pid": None,
        "processed": 0,
        "total": 0,
        "errors": 0,
        "started_at": now,
        "heartbeat_at": None,
        "updated_at": now,
        "completed_at": None,
        "artifact_paths": [],
    }
    _write_atomic(status_path, initial)

    cmd = (
        [sys.executable, str(script), "--tag", tag, "--job_id", job_id,
         "--runtime_root", str(runtime_root)]
        + extra_args
    )
    proc_env = {**os.environ, **(env or {})}
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=proc_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    write_status(status_path, {"state": "running", "pid": proc.pid})
    initial["state"] = "running"
    initial["pid"] = proc.pid
    return initial
```

- [ ] **Step 2: Run jobs.py tests**

```bash
python -m pytest tests/test_phase2.py::TestWriteStatus tests/test_phase2.py::TestReadStatus tests/test_phase2.py::TestReconcile tests/test_phase2.py::TestFindActiveJob tests/test_phase2.py::TestStartJob -v
```

Expected: All pass.

- [ ] **Step 3: Commit**

```bash
git add src/jobs.py
git commit -m "feat(phase2): add src/jobs.py — durable job status, heartbeat, reconciliation"
```

---

## Task 3: Implement scripts package and classify_job.py

**Files:**
- Create: `scripts/__init__.py`
- Create: `scripts/classify_job.py`

- [ ] **Step 1: Create scripts/__init__.py**

```python
# scripts/__init__.py
# Makes scripts/ a package so tests can import run_classify_job directly.
```

- [ ] **Step 2: Write scripts/classify_job.py**

```python
# scripts/classify_job.py
"""Subprocess job: full-run LLM classification with resume and chunked atomic saves.

Entry point: python scripts/classify_job.py --tag TAG --job_id JOB_ID ...

Key behaviors:
- Only processes rows where classifier_mode is empty (resume-safe).
- Saves CSV and writes heartbeat every CHUNK_SIZE rows.
- On per-row LLM errors: writes classifier_mode='error', continues — job still completes.
- On catastrophic errors (CSV unreadable, etc.): writes state=failed and exits 1.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path when run directly as a subprocess
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.gm_insights import (
    ProviderConfig,
    classify_with_llm,
    complete_label,
    load_classified,
    save_classified,
)
from src.jobs import job_path, write_status

CHUNK_SIZE = 10  # Save and heartbeat every N rows


def run_classify_job(
    tag: str,
    job_id: str,
    runtime_root: Path,
    classified: Path,
    provider: ProviderConfig,
    limit: int = 0,
) -> None:
    """Core classification loop — separated from argparse for direct testing.

    Reads classified CSV, finds unclassified rows (classifier_mode==''),
    runs LLM on each, and saves back in chunks. Resume-safe: already-classified
    rows are skipped. Per-row errors are recorded in-place and do not abort the job.
    """
    status_path = job_path(runtime_root, tag, job_id)
    try:
        df = load_classified(classified)
        # Only rows that haven't been through any classifier yet are eligible
        pending_mask = (~df["skip_classification"]) & (df["classifier_mode"].fillna("").eq(""))
        pending = df[pending_mask]
        if limit > 0:
            pending = pending.head(limit)
        total = len(pending)

        write_status(status_path, {"total": total, "heartbeat_at": time.time()})

        processed = 0
        errors = 0
        # Process in chunks so we save progress regularly
        indices = pending.index.tolist()
        for chunk_start in range(0, total, CHUNK_SIZE):
            chunk_indices = indices[chunk_start : chunk_start + CHUNK_SIZE]
            for idx in chunk_indices:
                row = df.loc[idx]
                try:
                    label = complete_label(
                        classify_with_llm(str(row["combined_text"]), provider)
                    )
                    for key, value in label.items():
                        if key == "multi_complaint_categories" and isinstance(value, list):
                            value = ", ".join(value)
                        df.at[idx, key] = value
                    df.at[idx, "classifier_mode"] = "llm"
                    processed += 1
                except Exception:
                    # Record error in-place; job continues with remaining rows
                    df.at[idx, "classifier_mode"] = "error"
                    df.at[idx, "sentiment"] = "error"
                    errors += 1
            # Atomic chunked save after each chunk
            save_classified(df, classified)
            write_status(status_path, {
                "processed": processed,
                "total": total,
                "errors": errors,
                "heartbeat_at": time.time(),
            })

        write_status(status_path, {
            "state": "completed",
            "processed": processed,
            "total": total,
            "errors": errors,
            "completed_at": time.time(),
            "artifact_paths": [str(classified)],
        })

    except Exception as exc:
        write_status(status_path, {
            "state": "failed",
            "error": str(exc),
            "completed_at": time.time(),
        })
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM classify job subprocess")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--job_id", required=True)
    parser.add_argument("--runtime_root", required=True)
    parser.add_argument("--classified_path", required=True)
    parser.add_argument("--provider", default="openrouter")
    parser.add_argument("--base_url", default="https://openrouter.ai/api/v1")
    parser.add_argument("--model", default="gpt-oss-120b")
    parser.add_argument("--api_key_env", default="OPENROUTER_API_KEY")
    # API key is read from env (api_key_env) inside classify_with_llm; never stored on disk
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    provider = ProviderConfig(
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        api_key="",  # resolved from env inside classify_with_llm
    )
    run_classify_job(
        tag=args.tag,
        job_id=args.job_id,
        runtime_root=Path(args.runtime_root),
        classified=Path(args.classified_path),
        provider=provider,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run classify_job tests**

```bash
python -m pytest tests/test_phase2.py::TestClassifyJobRun -v
```

Expected: All 5 tests pass.

- [ ] **Step 4: Commit**

```bash
git add scripts/__init__.py scripts/classify_job.py
git commit -m "feat(phase2): add scripts/classify_job.py — full-run LLM classify with resume"
```

---

## Task 4: Implement stub scripts

**Files:**
- Create: `scripts/pdf_export_job.py`
- Create: `scripts/trend_job.py`
- Create: `scripts/faiss_qa_job.py`

- [ ] **Step 1: Write scripts/pdf_export_job.py**

```python
# scripts/pdf_export_job.py
"""Subprocess job: PDF export (charts + briefing). Phase 3 fills real rendering."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.jobs import job_path, write_status


def run_pdf_export_job(
    tag: str,
    job_id: str,
    runtime_root: Path,
    kind: str = "charts",
) -> None:
    """Stub — Phase 3 replaces this with real matplotlib/PDF rendering."""
    status_path = job_path(runtime_root, tag, job_id)
    try:
        write_status(status_path, {"heartbeat_at": time.time(), "total": 0})
        # Phase 3 will render charts here and write artifact paths
        write_status(status_path, {
            "state": "completed",
            "completed_at": time.time(),
            "artifact_paths": [],
        })
    except Exception as exc:
        write_status(status_path, {
            "state": "failed",
            "error": str(exc),
            "completed_at": time.time(),
        })
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--job_id", required=True)
    parser.add_argument("--runtime_root", required=True)
    parser.add_argument("--kind", default="charts", choices=["charts", "briefing"])
    args = parser.parse_args()
    run_pdf_export_job(args.tag, args.job_id, Path(args.runtime_root), args.kind)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write scripts/trend_job.py**

```python
# scripts/trend_job.py
"""Subprocess job: trend detection. Phase 5 fills real implementation."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.jobs import job_path, write_status


def run_trend_job(tag: str, job_id: str, runtime_root: Path) -> None:
    """Stub — Phase 5 replaces this with velocity / z-score trend detection."""
    status_path = job_path(runtime_root, tag, job_id)
    try:
        write_status(status_path, {"heartbeat_at": time.time(), "total": 0})
        write_status(status_path, {
            "state": "completed",
            "completed_at": time.time(),
            "artifact_paths": [],
        })
    except Exception as exc:
        write_status(status_path, {
            "state": "failed",
            "error": str(exc),
            "completed_at": time.time(),
        })
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--job_id", required=True)
    parser.add_argument("--runtime_root", required=True)
    args = parser.parse_args()
    run_trend_job(args.tag, args.job_id, Path(args.runtime_root))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Write scripts/faiss_qa_job.py**

```python
# scripts/faiss_qa_job.py
"""Subprocess job: FAISS Q&A index build. Phase 6 fills real implementation."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.jobs import job_path, write_status


def run_faiss_qa_job(tag: str, job_id: str, runtime_root: Path) -> None:
    """Stub — Phase 6 replaces this with embedding + FAISS index build."""
    status_path = job_path(runtime_root, tag, job_id)
    try:
        write_status(status_path, {"heartbeat_at": time.time(), "total": 0})
        write_status(status_path, {
            "state": "completed",
            "completed_at": time.time(),
            "artifact_paths": [],
        })
    except Exception as exc:
        write_status(status_path, {
            "state": "failed",
            "error": str(exc),
            "completed_at": time.time(),
        })
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--job_id", required=True)
    parser.add_argument("--runtime_root", required=True)
    args = parser.parse_args()
    run_faiss_qa_job(args.tag, args.job_id, Path(args.runtime_root))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: py_compile check**

```bash
python -m py_compile scripts/pdf_export_job.py scripts/trend_job.py scripts/faiss_qa_job.py && echo "OK"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add scripts/pdf_export_job.py scripts/trend_job.py scripts/faiss_qa_job.py
git commit -m "feat(phase2): add stub job scripts for pdf_export, trend, faiss_qa"
```

---

## Task 5: Add new endpoints to app.py

**Files:**
- Modify: `app.py`

The four new endpoints sit after the existing `/api/briefing` endpoint. The imports at the top of app.py also need two additions: `from src.jobs import ...`.

- [ ] **Step 1: Add import at top of app.py (after existing imports)**

Add to the import block, after the `from src.gm_insights import (...)` block:

```python
from src.jobs import find_active_job, job_path, read_status, start_job
```

- [ ] **Step 2: Add two new request models (after `BriefingRequest`)**

```python
class ClassifyJobRequest(BaseModel):
    tag: str = DEFAULT_TAG
    provider: str = "openrouter"
    model: str = ""
    api_key: str = ""
    limit: int = 0  # 0 = all pending rows


class PdfExportJobRequest(BaseModel):
    tag: str = DEFAULT_TAG
    kind: str = "charts"  # "charts" | "briefing"
```

- [ ] **Step 3: Add the four endpoints (add after the existing `/api/briefing` endpoint)**

```python
@app.post("/api/classify/job")
def classify_job(request: ClassifyJobRequest) -> JSONResponse:
    """Start a full-run LLM classify subprocess job for a tag.
    Returns existing job status (started=False) if one is already running."""
    tag = clean_tag(request.tag)
    active = find_active_job(RUNTIME, tag, "classify")
    if active:
        return safe_json({**active, "started": False})
    cpath = classified_path(tag)
    if not cpath.exists():
        raise HTTPException(
            status_code=400,
            detail="No classified CSV found for this tag. Run preview classification first.",
        )
    pcfg = provider_config(request.provider, request.model, request.api_key)
    # Pass the key via env so it never appears in the process argv
    env: dict[str, str] = {}
    if request.api_key:
        env[pcfg.api_key_env] = request.api_key
    extra_args = [
        "--classified_path", str(cpath),
        "--provider", pcfg.provider,
        "--base_url", pcfg.base_url,
        "--model", pcfg.model,
        "--api_key_env", pcfg.api_key_env,
        "--limit", str(max(0, request.limit)),
    ]
    status = start_job(
        RUNTIME, tag, "classify",
        ROOT / "scripts" / "classify_job.py",
        extra_args,
        env=env,
        cwd=ROOT,
    )
    return safe_json({**status, "started": True})


@app.get("/api/classify/status")
def classify_status(tag: str = DEFAULT_TAG, job_id: str = "") -> JSONResponse:
    """Return the most recent classify job status for a tag.
    Reconciles dead PIDs / stale heartbeats to 'interrupted' on read."""
    tag = clean_tag(tag)
    if job_id:
        status = read_status(job_path(RUNTIME, tag, job_id))
    else:
        status = find_active_job(RUNTIME, tag, "classify")
        if not status:
            # Fall back to most recent terminal job
            d = RUNTIME / tag / "jobs"
            if d.exists():
                all_jobs = []
                for p in d.glob("*.json"):
                    s = read_status(p)
                    if s and s.get("kind") == "classify":
                        all_jobs.append(s)
                if all_jobs:
                    status = max(all_jobs, key=lambda s: float(s.get("started_at", 0)))
    if not status:
        return safe_json({"state": "idle", "tag": tag})
    return safe_json(status)


@app.post("/api/export/pdf-job")
def pdf_export_job(request: PdfExportJobRequest) -> JSONResponse:
    """Start a PDF export subprocess job (stub — Phase 3 fills real rendering)."""
    tag = clean_tag(request.tag)
    active = find_active_job(RUNTIME, tag, "pdf_export")
    if active:
        return safe_json({**active, "started": False})
    extra_args = ["--kind", request.kind]
    status = start_job(
        RUNTIME, tag, "pdf_export",
        ROOT / "scripts" / "pdf_export_job.py",
        extra_args,
        cwd=ROOT,
    )
    return safe_json({**status, "started": True})


@app.get("/api/export/status")
def pdf_export_status(tag: str = DEFAULT_TAG, job_id: str = "") -> JSONResponse:
    """Return the most recent pdf_export job status for a tag."""
    tag = clean_tag(tag)
    if job_id:
        status = read_status(job_path(RUNTIME, tag, job_id))
    else:
        status = find_active_job(RUNTIME, tag, "pdf_export")
    if not status:
        return safe_json({"state": "idle", "tag": tag})
    return safe_json(status)
```

- [ ] **Step 4: py_compile app.py**

```bash
python -m py_compile app.py && echo "OK"
```

Expected: `OK`

- [ ] **Step 5: Run endpoint tests**

```bash
python -m pytest tests/test_phase2.py::TestClassifyJobEndpoint -v
```

Expected: All 4 pass.

- [ ] **Step 6: Commit**

```bash
git add app.py
git commit -m "feat(phase2): add /api/classify/job, /api/classify/status, /api/export/pdf-job, /api/export/status"
```

---

## Task 6: Full test suite + py_compile verification

**Files:** No changes — verification only.

- [ ] **Step 1: py_compile all src and scripts**

```bash
python -m py_compile app.py src/gm_insights.py src/charts.py src/jobs.py \
  scripts/classify_job.py scripts/pdf_export_job.py \
  scripts/trend_job.py scripts/faiss_qa_job.py && echo "ALL OK"
```

Expected: `ALL OK`

- [ ] **Step 2: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short
```

Expected: All tests in test_phase0.py and test_phase2.py pass. Zero failures.

- [ ] **Step 3: Final commit if needed**

If there are any fixup commits needed after the test run, make them here. Otherwise:

```bash
git log --oneline -10
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered by |
|---|---|
| `src/jobs.py` for job metadata, status-file helpers, heartbeat, stale reconciliation, single-write-per-tag locking | Task 2 |
| `scripts/classify_job.py` with resume from pending, chunked atomic saves, error-row persistence | Task 3 |
| `scripts/pdf_export_job.py` stub | Task 4 |
| `scripts/trend_job.py` stub | Task 4 |
| `scripts/faiss_qa_job.py` stub | Task 4 |
| Job status under `runtime/<tag>/jobs/<job_id>.json` | Task 2 (`job_path`) |
| Status fields: `job_id`, `tag`, `kind`, `state`, `pid`, `processed`, `total`, `errors`, `started_at`, `heartbeat_at`, `updated_at`, `completed_at`, `artifact_paths` | Task 2 (`start_job`, `write_status`), Task 3 |
| API starts jobs with `subprocess.Popen` | Task 2 (`start_job`), Task 5 |
| Status reads reconcile dead PIDs / stale heartbeats to `failed`/`interrupted` | Task 2 (`reconcile`) — spec says `failed` or `interrupted`; implementation uses `interrupted` for both PID-dead and stale-heartbeat cases |
| `POST /api/classify/job`, `GET /api/classify/status` | Task 5 |
| `POST /api/export/pdf-job`, `GET /api/export/status` | Task 5 |
| Test: subprocess lifecycle | TestStartJob |
| Test: stale-job reconciliation | TestReconcile |
| Test: classification resume (pending only) | TestClassifyJobRun |

**Gap check:** The spec says stale-job reconciliation marks to `failed` or `interrupted`. The implementation uses `interrupted` for both dead-PID and stale-heartbeat. This matches the more nuanced phrasing "stale heartbeats to `interrupted`" while the word `failed` in the spec refers to jobs that explicitly exit with non-zero code (which write `state=failed` themselves). No gap.

**Type consistency check:** `run_classify_job` signature in classify_job.py uses `ProviderConfig` which is imported from `src.gm_insights`. `start_job` in jobs.py takes `Path` for `script`, and app.py passes `ROOT / "scripts" / "classify_job.py"` — consistent. `job_path` returns `Path` everywhere — consistent.
