import os
import time
from pathlib import Path

import pytest

from src.run_store import (
    AttemptPaths,
    RunStore,
    ensure_attempt_layout,
)


def test_schema_round_trips_and_survives_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "runs.db"
    store = RunStore(db_path)

    assert store.journal_mode() == "wal"
    store.create_run(
        "run-1",
        "gm",
        config={"provider": "openai", "model": "gpt-test"},
        started_at=10.0,
    )
    store.upsert_step(
        "run-1",
        "prepare",
        state="completed",
        started_at=10.0,
        ended_at=11.0,
        processed=3,
        total=3,
        errors=0,
        artifacts=[{"id": "working-set", "path": "/snapshot/data.csv"}],
    )
    attempt = store.start_attempt(
        "run-1", "prepare", started_at=10.0, log_path="/snapshot/step.log"
    )
    store.finish_attempt(
        "run-1",
        "prepare",
        attempt["attempt_no"],
        state="completed",
        ended_at=11.0,
        processed=3,
        total=3,
        artifacts=[{"id": "working-set", "path": "/snapshot/data.csv"}],
    )
    store.update_run("run-1", state="completed", ended_at=11.0)
    store.close()

    reopened = RunStore(db_path)
    run = reopened.get_run("run-1")
    assert run == {
        "run_id": "run-1",
        "tag": "gm",
        "state": "completed",
        "started_at": 10.0,
        "ended_at": 11.0,
        "config": {"provider": "openai", "model": "gpt-test"},
        "warning": None,
        "coordinator_pid": None,
        "heartbeat_at": None,
    }
    assert reopened.get_steps("run-1")[0]["processed"] == 3
    assert reopened.get_attempts("run-1", "prepare")[0]["attempt_no"] == 1
    reopened.close()


def test_create_run_scrubs_api_keys_from_persisted_config(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.db")
    store.create_run(
        "run-1",
        "gm",
        config={
            "provider": "openai",
            "api_key": "secret",
            "nested": {"apiKey": "also-secret", "model": "gpt-test"},
        },
    )

    config = store.get_run("run-1")["config"]
    assert config == {"provider": "openai", "nested": {"model": "gpt-test"}}
    assert "secret" not in (tmp_path / "runs.db").read_bytes().decode("latin1")


def test_second_concurrent_lock_fails_and_owner_can_release(tmp_path: Path) -> None:
    db_path = tmp_path / "runs.db"
    first = RunStore(db_path)
    second = RunStore(db_path)

    acquired = first.acquire_tag_lock(
        "gm", owner_id="owner-1", run_id="run-1", pid=os.getpid()
    )
    rejected = second.acquire_tag_lock(
        "gm", owner_id="owner-2", run_id="run-2", pid=os.getpid()
    )

    assert acquired.acquired is True
    assert rejected.acquired is False
    assert rejected.active_run_id == "run-1"
    assert first.release_tag_lock("gm", "wrong-owner") is False
    assert first.release_tag_lock("gm", "owner-1") is True
    assert second.acquire_tag_lock(
        "gm", owner_id="owner-2", run_id="run-2", pid=os.getpid()
    ).acquired is True


def test_stale_dead_owner_is_reclaimed_before_lock_is_granted(tmp_path: Path) -> None:
    db_path = tmp_path / "runs.db"
    first = RunStore(db_path)
    second = RunStore(db_path)
    first.acquire_tag_lock(
        "gm", owner_id="dead-owner", run_id="dead-run", pid=999_999_999, now=10.0
    )

    result = second.acquire_tag_lock(
        "gm", owner_id="new-owner", run_id="new-run", pid=os.getpid(), now=20.0
    )

    assert result.acquired is True
    assert result.reclaimed is True
    assert second.get_tag_lock("gm")["owner_id"] == "new-owner"


def test_reserve_new_run_creates_pending_run_steps_and_lock(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.db")
    now = time.time()

    result = store.reserve_new_run(
        "gm",
        run_id="run-1",
        owner_id="job-1",
        pid=os.getpid(),
        config={"provider": "openrouter"},
        step_names=["prepare", "classify"],
        now=now,
    )

    assert result.acquired is True
    assert store.get_run("run-1")["state"] == "pending"
    assert [row["state"] for row in store.get_steps("run-1")] == ["pending", "pending"]
    assert store.get_tag_lock("gm")["owner_id"] == "job-1"


def test_second_reservation_returns_active_run_without_orphan(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.db")
    now = time.time()
    first = store.reserve_new_run(
        "gm",
        run_id="run-1",
        owner_id="job-1",
        pid=os.getpid(),
        config={},
        step_names=["prepare"],
        now=now,
    )
    second = store.reserve_new_run(
        "gm",
        run_id="run-2",
        owner_id="job-2",
        pid=os.getpid(),
        config={},
        step_names=["prepare"],
        now=now + 1,
    )

    assert first.acquired is True
    assert second.acquired is False
    assert second.active_run_id == "run-1"
    assert store.get_run("run-2") is None


def test_adopt_reserved_run_requires_matching_pending_row_and_owner(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.db")
    now = time.time()
    store.reserve_new_run(
        "gm",
        run_id="run-1",
        owner_id="job-1",
        pid=os.getpid(),
        config={},
        step_names=["prepare"],
        now=now,
    )

    assert store.adopt_reserved_run(
        "gm", run_id="run-1", owner_id="job-1", pid=1234, now=now + 1
    ) is True
    assert store.get_run("run-1")["state"] == "running"
    assert store.get_tag_lock("gm")["pid"] == 1234
    assert store.adopt_reserved_run(
        "gm", run_id="missing", owner_id="job-1", pid=1234, now=now + 2
    ) is False


def test_reconcile_dead_pending_run_marks_failed_and_releases_lock(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.db")
    now = time.time()
    store.reserve_new_run(
        "gm",
        run_id="run-1",
        owner_id="job-1",
        pid=999_999_999,
        config={},
        step_names=["prepare"],
        now=now,
    )

    assert store.reconcile_pending_run("run-1", now=now + 1) is True
    assert store.get_run("run-1")["state"] == "failed"
    assert store.get_tag_lock("gm") is None


def test_attempt_numbers_are_monotonic_and_steps_project_latest_attempt(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.db")
    store.create_run("run-1", "gm", config={})
    first = store.start_attempt("run-1", "classify", log_path="one.log")
    store.finish_attempt(
        "run-1", "classify", first["attempt_no"], state="failed", warning="boom"
    )
    second = store.start_attempt("run-1", "classify", log_path="two.log")
    store.finish_attempt(
        "run-1", "classify", second["attempt_no"], state="completed"
    )

    assert [row["attempt_no"] for row in store.get_attempts("run-1", "classify")] == [1, 2]
    step = store.get_step("run-1", "classify")
    assert step["attempt_no"] == 2
    assert step["state"] == "completed"
    assert step["log_path"] == "two.log"


def test_attempt_layout_and_retention_keep_logs_but_prune_old_blobs(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    store = RunStore(tmp_path / "runs.db")

    for number in range(1, 4):
        run_id = f"run-{number}"
        store.create_run(run_id, "gm", config={}, started_at=float(number))
        paths = ensure_attempt_layout(runtime_root, "gm", run_id, "trends", 1)
        assert isinstance(paths, AttemptPaths)
        paths.log_path.write_text(f"log {number}", encoding="utf-8")
        (paths.artifacts_dir / "large.bin").write_bytes(b"x" * 20)

    pruned = store.prune_attempt_artifacts(runtime_root, "gm", keep_last_runs=2)

    oldest = ensure_attempt_layout(runtime_root, "gm", "run-1", "trends", 1)
    newest = ensure_attempt_layout(runtime_root, "gm", "run-3", "trends", 1)
    assert pruned == ["run-1"]
    assert oldest.log_path.read_text(encoding="utf-8") == "log 1"
    assert not (oldest.artifacts_dir / "large.bin").exists()
    assert (newest.artifacts_dir / "large.bin").exists()


def test_invalid_run_and_step_states_are_rejected(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.db")
    with pytest.raises(ValueError, match="run state"):
        store.create_run("run-1", "gm", config={}, state="bogus")

    store.create_run("run-2", "gm", config={})
    with pytest.raises(ValueError, match="step state"):
        store.upsert_step("run-2", "prepare", state="interrupted")
