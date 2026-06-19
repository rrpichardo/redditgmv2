import json
import os
import signal
import time
from pathlib import Path

import pandas as pd
import pytest

import scripts.analyze_run as analyze_run_module
from scripts.analyze_run import (
    STEP_ORDER,
    ActiveRunError,
    AnalysisCoordinator,
    AnalyzeConfig,
    StepResult,
    monitor_job,
    prepare_working_set,
    retry_steps,
)
from src.gm_insights import normalize_reddit_frame, save_classified
from src.jobs import HEARTBEAT_STALE_SECS, job_path, start_job, write_status
from src.run_store import RunStore, latest_output_root


def _raw_rows(count: int = 3, *, bad_timestamps: bool = False) -> pd.DataFrame:
    rows = []
    for index in range(count):
        rows.append(
            {
                "id": f"post-{index}",
                "title": f"Detailed vehicle issue number {index}",
                "selftext": f"This is sufficiently detailed customer evidence for row {index}.",
                "subreddit": "gm",
                "created_at": "not-a-date" if bad_timestamps else f"2026-0{index + 1}-01T00:00:00Z",
                "score": str(index + 1),
            }
        )
    return pd.DataFrame(rows)


def _write_raw(runtime_root: Path, tag: str, frame: pd.DataFrame) -> Path:
    path = runtime_root / tag / "data" / "gm_posts.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return path


def _config(*, api_key: str = "test-key", api_key_env: str = "M15_TEST_KEY") -> AnalyzeConfig:
    return AnalyzeConfig(
        provider="openai",
        model="gpt-test",
        base_url="https://example.test/v1",
        api_key_env=api_key_env,
        api_key=api_key,
        n_clusters=2,
    )


def _successful_worker(step: str, coordinator: AnalysisCoordinator, _paths) -> StepResult:
    tag_root = coordinator.staging_root / coordinator.tag
    if step == "classify":
        path = tag_root / "classified" / "classified_posts.csv"
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        eligible = ~frame["skip_classification"].str.lower().isin(["true", "1", "yes"])
        frame.loc[eligible, "classifier_mode"] = "llm"
        frame.loc[eligible, "sentiment"] = "neutral"
        save_classified(frame, path)
        return StepResult(
            state="completed",
            processed=int(eligible.sum()),
            total=int(eligible.sum()),
            artifact_paths=[path],
        )
    if step == "briefing":
        path = tag_root / "reports" / "gm_reddit_synthesis_report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Briefing", encoding="utf-8")
    elif step == "trends":
        path = tag_root / "trends" / "trend_signals.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"signals": {}}', encoding="utf-8")
        (path.parent / "cluster_labels.json").write_text("{}", encoding="utf-8")
    elif step == "trend_pdf":
        path = tag_root / "downloads" / f"{coordinator.tag}_trend_briefing.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"pdf")
    elif step == "qa_index":
        path = tag_root / "qa" / "index.faiss"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"index")
    else:
        raise AssertionError(step)
    return StepResult(state="completed", processed=3, total=3, artifact_paths=[path])


def test_prepare_leaves_raw_rows_pending_and_preserves_completed_rows(tmp_path: Path) -> None:
    raw = _raw_rows(2)
    existing = normalize_reddit_frame(raw.iloc[[0]])
    existing.loc[:, "classifier_mode"] = "llm"
    existing.loc[:, "sentiment"] = "negative"
    output = tmp_path / "classified_posts.csv"

    result = prepare_working_set(raw, output, existing_frame=existing)
    prepared = pd.read_csv(output, dtype=str, keep_default_na=False)

    by_id = prepared.set_index("source_id")
    assert result.analyzable_rows == 2
    assert result.pending_rows == 1
    assert by_id.loc["post-0", "classifier_mode"] == "llm"
    assert by_id.loc["post-0", "sentiment"] == "negative"
    assert by_id.loc["post-1", "classifier_mode"] == ""
    assert "heuristic_preview" not in set(prepared["classifier_mode"])


def test_retry_selection_uses_dependency_map_not_linear_order() -> None:
    assert retry_steps("briefing") == ["briefing"]
    assert retry_steps("trends") == ["trends", "trend_pdf"]
    assert retry_steps("classify") == ["classify", "briefing", "trends", "trend_pdf", "qa_index"]


def test_scripted_run_writes_complete_ledger_logs_snapshots_and_latest(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    db_path = runtime_root / "runs.db"
    _write_raw(runtime_root, "gm", _raw_rows(3))
    coordinator = AnalysisCoordinator(
        runtime_root=runtime_root,
        db_path=db_path,
        tag="gm",
        run_id="run-1",
        config=_config(),
        child_runner=_successful_worker,
    )

    result = coordinator.run()

    assert result["state"] == "completed"
    with RunStore(db_path) as store:
        steps = store.get_steps("run-1", STEP_ORDER)
        attempts = store.get_attempts("run-1")
        assert [step["name"] for step in steps] == STEP_ORDER
        assert all(step["state"] == "completed" for step in steps)
        assert len(attempts) == len(STEP_ORDER)
        assert store.get_step("run-1", "classify")["total"] == 3
        assert store.get_tag_lock("gm") is None
        assert "test-key" not in json.dumps(store.get_run("run-1")["config"])
        for attempt in attempts:
            assert Path(attempt["log_path"]).exists()
            assert Path(attempt["log_path"]).read_text(encoding="utf-8")
            assert attempt["artifacts"]
            for artifact in attempt["artifacts"]:
                assert Path(artifact["path"]).exists()
    output_root = latest_output_root(runtime_root, "gm")
    assert (output_root / "classified" / ".run_id").read_text() == "run-1"
    assert (output_root / "trends" / ".run_id").read_text() == "run-1"
    assert not (runtime_root / "gm" / "runs" / "run-1" / "staging").exists()


def test_concurrent_run_is_rejected_by_tag_lock(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    db_path = runtime_root / "runs.db"
    _write_raw(runtime_root, "gm", _raw_rows(2))
    with RunStore(db_path) as store:
        store.acquire_tag_lock(
            "gm", owner_id="active-owner", run_id="active-run", pid=os.getpid()
        )
    coordinator = AnalysisCoordinator(
        runtime_root=runtime_root,
        db_path=db_path,
        tag="gm",
        run_id="contender",
        config=_config(),
        child_runner=_successful_worker,
    )

    with pytest.raises(ActiveRunError) as exc_info:
        coordinator.run()

    assert exc_info.value.active_run_id == "active-run"
    with RunStore(db_path) as store:
        assert store.get_run("contender") is None


def test_killed_child_reconciles_to_failed(tmp_path: Path) -> None:
    script = tmp_path / "sleeper.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    status = start_job(runtime_root, "gm", "classify", script, [], cwd=tmp_path)
    os.kill(status["pid"], signal.SIGKILL)
    os.waitpid(status["pid"], 0)

    result = monitor_job(runtime_root, "gm", status["job_id"], poll_interval=0.01)

    assert result.state == "failed"
    assert "interrupted" in (result.warning or "")


def test_stale_heartbeat_terminates_and_reaps_live_child(tmp_path: Path) -> None:
    script = tmp_path / "sleeper.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    status = start_job(runtime_root, "gm", "classify", script, [], cwd=tmp_path)
    write_status(
        job_path(runtime_root, "gm", status["job_id"]),
        {"heartbeat_at": time.time() - HEARTBEAT_STALE_SECS - 1},
    )

    try:
        result = monitor_job(runtime_root, "gm", status["job_id"], poll_interval=0.01)

        assert result.state == "failed"
        with pytest.raises(OSError):
            os.kill(status["pid"], 0)
    finally:
        try:
            os.kill(status["pid"], signal.SIGKILL)
        except OSError:
            pass
        try:
            os.waitpid(status["pid"], 0)
        except (ChildProcessError, OSError):
            pass


def test_degraded_classify_marks_step_and_run_completed_with_warnings(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_raw(runtime_root, "gm", _raw_rows(3))

    def degraded(step, coordinator, paths):
        result = _successful_worker(step, coordinator, paths)
        if step == "classify":
            return StepResult(
                state="completed",
                processed=2,
                total=3,
                errors=1,
                artifact_paths=result.artifact_paths,
            )
        return result

    coordinator = AnalysisCoordinator(
        runtime_root=runtime_root,
        db_path=runtime_root / "runs.db",
        tag="gm",
        run_id="degraded",
        config=_config(),
        child_runner=degraded,
    )

    result = coordinator.run()

    assert result["state"] == "completed_with_warnings"
    with RunStore(runtime_root / "runs.db") as store:
        classify = store.get_step("degraded", "classify")
        assert classify["state"] == "completed_with_warnings"
        assert classify["error_rate"] == pytest.approx(1 / 3)
        assert "1 of 3" in classify["warning"]


def test_all_failed_classification_fails_step_and_blocks_dependents(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_raw(runtime_root, "gm", _raw_rows(3))
    called: list[str] = []

    def all_failed(step, coordinator, paths):
        called.append(step)
        result = _successful_worker(step, coordinator, paths)
        if step == "classify":
            return StepResult(
                state="completed",
                processed=0,
                total=3,
                errors=3,
                artifact_paths=result.artifact_paths,
            )
        return result

    coordinator = AnalysisCoordinator(
        runtime_root=runtime_root,
        db_path=runtime_root / "runs.db",
        tag="gm",
        run_id="all-failed",
        config=_config(),
        child_runner=all_failed,
    )

    result = coordinator.run()

    assert result["state"] == "failed"
    assert called == ["classify"]
    with RunStore(runtime_root / "runs.db") as store:
        classify = store.get_step("all-failed", "classify")
        assert classify["state"] == "failed"
        assert classify["error_rate"] == 1.0
        assert store.get_step("all-failed", "briefing")["state"] == "blocked"
        assert store.get_step("all-failed", "qa_index")["state"] == "blocked"


def test_bad_timestamps_warn_trends_while_other_steps_continue(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_raw(runtime_root, "gm", _raw_rows(3, bad_timestamps=True))
    coordinator = AnalysisCoordinator(
        runtime_root=runtime_root,
        db_path=runtime_root / "runs.db",
        tag="gm",
        run_id="bad-time",
        config=_config(),
        child_runner=_successful_worker,
    )

    result = coordinator.run()

    assert result["state"] == "completed_with_warnings"
    with RunStore(runtime_root / "runs.db") as store:
        trends = store.get_step("bad-time", "trends")
        assert trends["state"] == "completed_with_warnings"
        assert "timestamps" in trends["warning"].lower()
        assert store.get_step("bad-time", "qa_index")["state"] == "completed"


def test_one_row_and_missing_key_apply_per_step_guards(tmp_path: Path, monkeypatch) -> None:
    runtime_root = tmp_path / "runtime"
    _write_raw(runtime_root, "gm", _raw_rows(1))
    monkeypatch.delenv("MISSING_M15_KEY", raising=False)
    called: list[str] = []

    def worker(step, coordinator, paths):
        called.append(step)
        return _successful_worker(step, coordinator, paths)

    coordinator = AnalysisCoordinator(
        runtime_root=runtime_root,
        db_path=runtime_root / "runs.db",
        tag="gm",
        run_id="guarded",
        config=_config(api_key="", api_key_env="MISSING_M15_KEY"),
        child_runner=worker,
    )

    result = coordinator.run()

    assert result["state"] == "completed_with_warnings"
    assert called == ["briefing"]
    with RunStore(runtime_root / "runs.db") as store:
        assert store.get_step("guarded", "classify")["state"] == "blocked"
        assert store.get_step("guarded", "classify")["total"] == 1
        assert store.get_step("guarded", "briefing")["state"] == "completed"
        assert store.get_step("guarded", "trends")["state"] == "blocked"
        assert store.get_step("guarded", "trend_pdf")["state"] == "skipped"
        assert store.get_step("guarded", "qa_index")["state"] == "blocked"


def test_zero_analyzable_rows_blocks_run_without_children(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_raw(
        runtime_root,
        "gm",
        pd.DataFrame([{"id": "junk", "title": "x", "selftext": "[deleted]", "subreddit": "gm"}]),
    )

    def should_not_run(*_args):
        raise AssertionError("no child should run")

    coordinator = AnalysisCoordinator(
        runtime_root=runtime_root,
        db_path=runtime_root / "runs.db",
        tag="gm",
        run_id="empty",
        config=_config(),
        child_runner=should_not_run,
    )

    result = coordinator.run()

    assert result["state"] == "blocked"
    with RunStore(runtime_root / "runs.db") as store:
        assert store.get_step("empty", "prepare")["state"] == "blocked"
        assert all(Path(row["log_path"]).exists() for row in store.get_attempts("empty"))


def test_failed_run_never_overwrites_tag_latest(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_raw(runtime_root, "gm", _raw_rows(3))
    latest = runtime_root / "gm" / "classified"
    latest.mkdir(parents=True)
    (latest / "sentinel.txt").write_text("previous generation", encoding="utf-8")

    def failing(step, coordinator, paths):
        if step == "trends":
            return StepResult(state="failed", warning="forced failure")
        return _successful_worker(step, coordinator, paths)

    coordinator = AnalysisCoordinator(
        runtime_root=runtime_root,
        db_path=runtime_root / "runs.db",
        tag="gm",
        run_id="failed-run",
        config=_config(),
        child_runner=failing,
    )

    result = coordinator.run()

    assert result["state"] == "failed"
    assert (latest / "sentinel.txt").read_text(encoding="utf-8") == "previous generation"
    assert not (latest / ".run_id").exists()


def test_missing_key_run_preserves_latest_for_blocked_families(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    tag_root = runtime_root / "gm"

    old_classified = normalize_reddit_frame(_raw_rows(1))
    old_classified.loc[:, "classifier_mode"] = "llm"
    old_classified.loc[:, "sentiment"] = "negative"
    old_path = tag_root / "classified" / "classified_posts.csv"
    save_classified(old_classified, old_path)
    os.utime(old_path, (1, 1))
    for family in ("trends", "qa"):
        family_root = tag_root / family
        family_root.mkdir(parents=True)
        (family_root / "sentinel.txt").write_text("previous-good", encoding="utf-8")

    _write_raw(runtime_root, "gm", _raw_rows(2))
    monkeypatch.delenv("MISSING_M15_KEY", raising=False)
    coordinator = AnalysisCoordinator(
        runtime_root=runtime_root,
        db_path=runtime_root / "runs.db",
        tag="gm",
        run_id="no-key",
        config=_config(api_key="", api_key_env="MISSING_M15_KEY"),
        child_runner=_successful_worker,
    )

    result = coordinator.run()

    assert result["state"] == "completed_with_warnings"
    current = tag_root / "current"
    assert current.is_symlink()
    assert (current / "trends" / "sentinel.txt").read_text() == "previous-good"
    assert (current / "qa" / "sentinel.txt").read_text() == "previous-good"
    published = pd.read_csv(
        current / "classified" / "classified_posts.csv",
        dtype=str,
        keep_default_na=False,
    )
    assert published["classifier_mode"].tolist() == ["llm"]


def test_publish_pointer_failure_keeps_previous_generation_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    tag_root = runtime_root / "gm"
    _write_raw(runtime_root, "gm", _raw_rows(3))
    for family in ("classified", "reports", "trends", "downloads", "qa"):
        family_root = tag_root / family
        family_root.mkdir(parents=True, exist_ok=True)
        (family_root / "old.txt").write_text("old-generation", encoding="utf-8")

    original_replace = analyze_run_module.os.replace

    def fail_pointer_swap(source: Path | str, destination: Path | str) -> None:
        if Path(destination).name == "current":
            raise OSError("forced pointer swap failure")
        original_replace(source, destination)

    monkeypatch.setattr(analyze_run_module.os, "replace", fail_pointer_swap)
    coordinator = AnalysisCoordinator(
        runtime_root=runtime_root,
        db_path=runtime_root / "runs.db",
        tag="gm",
        run_id="publish-fail",
        config=_config(),
        child_runner=_successful_worker,
    )

    result = coordinator.run()

    assert result["state"] == "failed"
    assert not (tag_root / "current").exists()
    for family in ("classified", "reports", "trends", "downloads", "qa"):
        assert (tag_root / family / "old.txt").read_text() == "old-generation"
