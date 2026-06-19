"""Sequential full-analysis coordinator with durable run/attempt history."""

from __future__ import annotations

import argparse
import os
import signal
import shutil
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd

from src.gm_insights import (
    EXPECTED_KEYS,
    ensure_label_columns,
    load_runtime_frame,
    normalize_reddit_frame,
    save_classified,
)
from src.jobs import job_path, read_status, start_job, write_status
from src.run_store import (
    AttemptPaths,
    RunStore,
    ensure_attempt_layout,
    latest_output_root,
    run_staging_root,
)


STEP_ORDER = ["prepare", "classify", "briefing", "trends", "trend_pdf", "qa_index"]
DEPENDENCIES = {
    "prepare": ("classify",),
    "classify": ("briefing", "trends", "qa_index"),
    "trends": ("trend_pdf",),
}
_SUCCESS_STATES = {"completed", "completed_with_warnings"}
_PUBLISH_FAMILIES = ("classified", "reports", "trends", "downloads", "qa")
CLASSIFY_FAIL_ERROR_RATE = 0.5
_FAMILY_PRODUCERS = {
    "classified": "classify",
    "reports": "briefing",
    "trends": "trends",
    "downloads": "trend_pdf",
    "qa": "qa_index",
}


class ActiveRunError(RuntimeError):
    def __init__(self, active_run_id: str | None):
        self.active_run_id = active_run_id
        super().__init__(f"tag already has an active run: {active_run_id}")


@dataclass(frozen=True)
class AnalyzeConfig:
    provider: str = "openrouter"
    model: str = "gpt-oss-120b"
    base_url: str = "https://openrouter.ai/api/v1"
    api_key_env: str = "OPENROUTER_API_KEY"
    api_key: str = ""
    n_clusters: int = 10
    embedding_model: str = "text-embedding-3-small"

    def effective_api_key(self) -> str:
        return self.api_key or os.getenv(self.api_key_env, "")

    def persisted(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "n_clusters": self.n_clusters,
            "embedding_model": self.embedding_model,
        }


@dataclass(frozen=True)
class PrepareResult:
    output_path: Path
    analyzable_rows: int
    pending_rows: int
    valid_timestamps: int
    date_span_days: int
    trend_warning: str | None = None


@dataclass
class StepResult:
    state: str
    processed: int = 0
    total: int = 0
    errors: int = 0
    warning: str | None = None
    artifact_paths: list[Path] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


ChildRunner = Callable[[str, "AnalysisCoordinator", AttemptPaths], StepResult]


def terminate_worker(pid: int, *, grace_seconds: float = 2.0) -> None:
    """Stop and reap a supervised child before staging or locks are released."""
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass

    deadline = time.monotonic() + max(0.0, grace_seconds)
    while time.monotonic() < deadline:
        try:
            reaped_pid, _status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return
        if reaped_pid:
            return
        time.sleep(0.02)

    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass


def retry_steps(step: str) -> list[str]:
    """Return a step plus only its transitive dependents in execution order."""
    if step not in STEP_ORDER:
        raise ValueError(f"unknown step: {step}")
    selected = {step}
    queue = [step]
    while queue:
        parent = queue.pop(0)
        for child in DEPENDENCIES.get(parent, ()):
            if child not in selected:
                selected.add(child)
                queue.append(child)
    return [name for name in STEP_ORDER if name in selected]


def _eligible_mask(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series([], dtype=bool, index=frame.index)
    values = frame["skip_classification"].astype(str).str.lower()
    return ~values.isin(["true", "1", "yes"])


def _merge_completed_labels(prepared: pd.DataFrame, existing: pd.DataFrame) -> pd.DataFrame:
    if prepared.empty or existing.empty or "source_id" not in existing:
        return prepared
    prior = ensure_label_columns(existing.copy())
    completed = prior[prior["classifier_mode"].isin(["llm", "imported"])]
    if completed.empty:
        return prepared
    completed = completed.drop_duplicates("source_id", keep="last").set_index("source_id")
    output = prepared.copy()
    columns = [*EXPECTED_KEYS, "classifier_mode"]
    for index, source_id in output["source_id"].astype(str).items():
        if source_id not in completed.index:
            continue
        for column in columns:
            if column in completed:
                output.at[index, column] = completed.at[source_id, column]
    return ensure_label_columns(output)


def prepare_working_set(
    source_frame: pd.DataFrame,
    output_path: Path,
    *,
    existing_frame: pd.DataFrame | None = None,
) -> PrepareResult:
    """Normalize source rows without marking pending rows as preview-classified."""
    already_normalized = {
        "source_id",
        "combined_text",
        "classifier_mode",
        "skip_classification",
    }.issubset(source_frame.columns)
    if already_normalized:
        prepared = ensure_label_columns(source_frame.copy())
    else:
        prepared = normalize_reddit_frame(source_frame)
    if existing_frame is not None:
        prepared = _merge_completed_labels(prepared, existing_frame)

    # Preview/error rows are retryable. Only imported/LLM rows remain complete.
    if not prepared.empty:
        retryable = ~prepared["classifier_mode"].isin(["llm", "imported"])
        prepared.loc[retryable, "classifier_mode"] = ""

    save_classified(prepared, output_path)
    eligible = _eligible_mask(prepared)
    analyzable = int(eligible.sum())
    pending = int(
        (eligible & prepared["classifier_mode"].fillna("").astype(str).eq("")).sum()
    ) if not prepared.empty else 0

    if analyzable:
        timestamps = pd.to_datetime(prepared.loc[eligible, "created_at_norm"], errors="coerce")
        valid = timestamps.dropna()
    else:
        valid = pd.Series([], dtype="datetime64[ns]")
    valid_count = int(valid.size)
    span_days = int((valid.max() - valid.min()).days) if valid_count > 1 else 0
    warning = None
    if analyzable and valid_count == 0:
        warning = "No valid timestamps; trend signals will use insufficient-data fallbacks."
    elif valid_count < analyzable:
        warning = (
            f"Only {valid_count} of {analyzable} timestamps are valid; "
            "trend signals may be incomplete."
        )
    elif analyzable > 1 and span_days < 37:
        warning = (
            f"Timestamp span is {span_days} days; less than the 37-day recent/baseline window, "
            "so trend signals are directional."
        )
    return PrepareResult(
        output_path=Path(output_path),
        analyzable_rows=analyzable,
        pending_rows=pending,
        valid_timestamps=valid_count,
        date_span_days=span_days,
        trend_warning=warning,
    )


def monitor_job(
    runtime_root: Path,
    tag: str,
    job_id: str,
    *,
    poll_interval: float = 0.2,
    heartbeat: Callable[[], None] | None = None,
) -> StepResult:
    """Wait for one child status and convert dead/interrupted workers to failed."""
    status_file = job_path(runtime_root, tag, job_id)
    while True:
        status = read_status(status_file)
        if status is None:
            return StepResult(state="failed", warning="Worker status file disappeared.")
        state = status.get("state")
        if state == "completed":
            warning = status.get("trend_warning") or status.get("briefing_warning")
            return StepResult(
                state="completed",
                processed=int(status.get("processed", status.get("n_docs", 0)) or 0),
                total=int(status.get("total", status.get("n_docs", 0)) or 0),
                errors=int(status.get("errors", 0) or 0),
                warning=str(warning) if warning else None,
                artifact_paths=[Path(value) for value in status.get("artifact_paths", [])],
                metadata={
                    key: value
                    for key, value in status.items()
                    if key in {"n_clusters", "n_docs", "doc_count", "used_llm"}
                },
            )
        if state == "failed":
            return StepResult(state="failed", warning=str(status.get("error") or "Worker failed."))
        if state == "interrupted":
            pid = status.get("pid")
            if pid:
                terminate_worker(int(pid))
            return StepResult(state="failed", warning="Worker interrupted (dead PID or stale heartbeat).")

        pid = status.get("pid")
        if pid:
            try:
                reaped_pid, return_code = os.waitpid(int(pid), os.WNOHANG)
            except ChildProcessError:
                reaped_pid, return_code = 0, 0
            if reaped_pid:
                return StepResult(
                    state="failed",
                    warning=f"Worker interrupted before terminal status (exit={return_code}).",
                )
        if heartbeat:
            heartbeat()
        time.sleep(max(0.01, poll_interval))


class AnalysisCoordinator:
    def __init__(
        self,
        *,
        runtime_root: Path,
        db_path: Path,
        tag: str,
        run_id: str,
        config: AnalyzeConfig,
        job_id: str | None = None,
        project_root: Path = _PROJECT_ROOT,
        child_runner: ChildRunner | None = None,
        steps_to_run: list[str] | None = None,
    ):
        self.runtime_root = Path(runtime_root)
        self.db_path = Path(db_path)
        self.tag = tag
        self.run_id = run_id
        self.config = config
        self.job_id = job_id
        self.project_root = Path(project_root)
        self.owner_id = job_id or f"analyze-{run_id}"
        self.staging_root = run_staging_root(self.runtime_root, tag, run_id)
        self.child_runner = child_runner or self._run_subprocess_step
        self.prepare_result: PrepareResult | None = None
        self._store: RunStore | None = None
        self._lock_acquired = False
        # When set, only these steps will be executed; others are skipped (state left untouched)
        self.steps_to_run: list[str] | None = steps_to_run

    @property
    def store(self) -> RunStore:
        if self._store is None:
            raise RuntimeError("coordinator store is not open")
        return self._store

    @property
    def staging_tag_root(self) -> Path:
        return self.staging_root / self.tag

    @property
    def working_classified_path(self) -> Path:
        return self.staging_tag_root / "classified" / "classified_posts.csv"

    def run(self) -> dict[str, object]:
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self._store = RunStore(self.db_path)
        try:
            lock = self.store.acquire_tag_lock(
                self.tag,
                owner_id=self.owner_id,
                run_id=self.run_id,
                pid=os.getpid(),
            )
            if not lock.acquired:
                raise ActiveRunError(lock.active_run_id)
            self._lock_acquired = True

            # Retry path: run record already exists; just mark it running again.
            # Fresh path: create the run record and initialize all steps as pending.
            if self.steps_to_run is not None:
                self.store.update_run(
                    self.run_id,
                    state="running",
                    heartbeat_at=time.time(),
                )
            else:
                self.store.create_run(
                    self.run_id,
                    self.tag,
                    config=self.config.persisted(),
                    coordinator_pid=os.getpid(),
                    heartbeat_at=time.time(),
                )
                for step in STEP_ORDER:
                    self.store.upsert_step(self.run_id, step, state="pending")
            self._coordinator_status("running")

            # Retry path: if prepare is not in the steps to run, reconstruct PrepareResult
            # from the working data files instead of re-running the prepare step.
            if self.steps_to_run is not None and "prepare" not in self.steps_to_run:
                self.prepare_result = self._load_prepare_result()
            else:
                self.prepare_result = self._execute_prepare()
            if self.prepare_result.analyzable_rows == 0:
                # Only block steps that are in our execution set (or all, for a fresh run)
                to_run = set(self.steps_to_run) if self.steps_to_run is not None else set(STEP_ORDER)
                if "classify" in to_run:
                    self._record_decision("classify", "blocked", "No analyzable rows.")
                if "briefing" in to_run:
                    self._record_decision("briefing", "blocked", "No analyzable rows.")
                if "trends" in to_run:
                    self._record_decision("trends", "blocked", "No analyzable rows.")
                if "trend_pdf" in to_run:
                    self._record_decision("trend_pdf", "skipped", "Trends did not run.")
                if "qa_index" in to_run:
                    self._record_decision("qa_index", "blocked", "No analyzable rows.")
                final_state = "blocked"
            else:
                self._execute_remaining_steps()
                final_state = self._derive_run_state()

            ended = time.time()
            warning = self._run_warning()
            if final_state in {"completed", "completed_with_warnings"}:
                self._publish_latest()
            self.store.update_run(
                self.run_id,
                state=final_state,
                ended_at=ended,
                warning=warning,
                heartbeat_at=ended,
            )
            self._coordinator_status(
                "failed" if final_state == "failed" else "completed",
                run_state=final_state,
            )
            return self.store.get_run(self.run_id) or {"run_id": self.run_id, "state": final_state}
        except ActiveRunError:
            raise
        except Exception as exc:
            traceback.print_exc()
            if self.store.get_run(self.run_id):
                self.store.update_run(
                    self.run_id,
                    state="failed",
                    ended_at=time.time(),
                    warning=str(exc)[:1000],
                )
            self._coordinator_status("failed", error=str(exc))
            return self.store.get_run(self.run_id) or {"run_id": self.run_id, "state": "failed"}
        finally:
            if self._lock_acquired:
                self.store.release_tag_lock(self.tag, self.owner_id)
            if self.staging_root.exists():
                shutil.rmtree(self.staging_root)
            self.store.close()
            self._store = None

    def _load_inputs(self) -> tuple[pd.DataFrame, pd.DataFrame | None]:
        tag_root = self.runtime_root / self.tag
        data_root = tag_root / "data"
        classified_path = tag_root / "classified" / "classified_posts.csv"
        raw_files = list(data_root.glob("*.csv")) if data_root.exists() else []
        raw_mtime = max((path.stat().st_mtime for path in raw_files), default=-1.0)
        classified_mtime = classified_path.stat().st_mtime if classified_path.exists() else -1.0
        existing = None
        if classified_path.exists():
            existing = ensure_label_columns(
                pd.read_csv(classified_path, dtype=str, keep_default_na=False)
            )
        if raw_files and (not classified_path.exists() or raw_mtime > classified_mtime):
            return load_runtime_frame(data_root), existing
        if existing is not None:
            return existing, existing
        return load_runtime_frame(data_root), None

    def _load_prepare_result(self) -> PrepareResult:
        """Reconstruct a PrepareResult from the working classified file without re-running prepare.

        Used in retry mode when 'prepare' is not in steps_to_run. Reads whatever CSV
        exists in the published output root (not staging) to derive row counts.
        """
        tag_root = self.runtime_root / self.tag
        classified = tag_root / "classified" / "classified_posts.csv"
        if not classified.exists():
            # No prior classified file — treat as zero analyzable rows
            return PrepareResult(
                output_path=classified,
                analyzable_rows=0,
                pending_rows=0,
                valid_timestamps=0,
                date_span_days=0,
            )
        frame = pd.read_csv(classified, dtype=str, keep_default_na=False)
        from src.gm_insights import ensure_label_columns
        frame = ensure_label_columns(frame)
        eligible = ~frame["skip_classification"].astype(str).str.lower().isin(["true", "1", "yes"])
        analyzable = int(eligible.sum())
        pending = int(
            (eligible & frame["classifier_mode"].fillna("").astype(str).eq("")).sum()
        ) if not frame.empty else 0
        if analyzable:
            timestamps = pd.to_datetime(frame.loc[eligible, "created_at_norm"], errors="coerce")
            valid = timestamps.dropna()
        else:
            valid = pd.Series([], dtype="datetime64[ns]")
        valid_count = int(valid.size)
        span_days = int((valid.max() - valid.min()).days) if valid_count > 1 else 0
        return PrepareResult(
            output_path=classified,
            analyzable_rows=analyzable,
            pending_rows=pending,
            valid_timestamps=valid_count,
            date_span_days=span_days,
        )

    def _execute_prepare(self) -> PrepareResult:
        attempt_no, paths = self._start_attempt("prepare")
        self._log(paths, "prepare: loading source data")
        try:
            source, existing = self._load_inputs()
            result = prepare_working_set(
                source,
                self.working_classified_path,
                existing_frame=existing,
            )
            state = "completed" if result.analyzable_rows else "blocked"
            warning = None if result.analyzable_rows else "No analyzable rows."
            step_result = StepResult(
                state=state,
                processed=result.analyzable_rows,
                total=len(source),
                warning=warning,
                artifact_paths=[result.output_path],
                metadata={
                    "pending_rows": result.pending_rows,
                    "valid_timestamps": result.valid_timestamps,
                    "date_span_days": result.date_span_days,
                },
            )
            self._finish_attempt("prepare", attempt_no, paths, step_result)
            self.store.upsert_step(
                self.run_id,
                "classify",
                state="pending",
                total=result.pending_rows,
            )
            return result
        except Exception as exc:
            self._log(paths, traceback.format_exc())
            failed = StepResult(state="failed", warning=str(exc))
            self._finish_attempt("prepare", attempt_no, paths, failed)
            raise

    def _should_run(self, step: str) -> bool:
        """Return True if this step should be executed (respects steps_to_run filter)."""
        if self.steps_to_run is None:
            return True
        return step in self.steps_to_run

    def _execute_remaining_steps(self) -> None:
        assert self.prepare_result is not None
        has_key = bool(self.config.effective_api_key())
        pending = self.prepare_result.pending_rows

        # classify step — run only when included in steps_to_run (or all steps)
        if self._should_run("classify"):
            if pending and not has_key:
                classify = self._record_decision(
                    "classify",
                    "blocked",
                    f"No effective API key ({self.config.api_key_env}).",
                    total=pending,
                )
            elif pending:
                classify = self._execute_step("classify")
            else:
                classify = self._execute_step(
                    "classify",
                    immediate=StepResult(
                        state="completed",
                        processed=0,
                        total=0,
                        artifact_paths=[self.working_classified_path],
                    ),
                )
        else:
            # Step not in retry set — read its current DB state to determine cascade behavior
            existing = self.store.get_step(self.run_id, "classify")
            classify = StepResult(state=existing["state"] if existing else "skipped")

        if classify.state == "failed":
            # Only record downstream blocks for steps that are in our execution set
            if self._should_run("briefing"):
                self._record_decision("briefing", "blocked", "Classification failed.")
            if self._should_run("trends"):
                self._record_decision("trends", "blocked", "Classification failed.")
            if self._should_run("trend_pdf"):
                self._record_decision("trend_pdf", "skipped", "Trends did not run.")
            if self._should_run("qa_index"):
                self._record_decision("qa_index", "blocked", "Classification failed.")
            return

        if self._should_run("briefing"):
            self._execute_step("briefing")

        # trends step
        if self._should_run("trends"):
            if self.prepare_result.analyzable_rows < 2:
                trends = self._record_decision(
                    "trends", "blocked", "At least 2 analyzable rows are required for clustering."
                )
            elif not has_key:
                trends = self._record_decision(
                    "trends", "blocked", f"No effective API key ({self.config.api_key_env})."
                )
            else:
                trends = self._execute_step("trends")
        else:
            existing = self.store.get_step(self.run_id, "trends")
            trends = StepResult(state=existing["state"] if existing else "skipped")

        if self._should_run("trend_pdf"):
            if trends.state in _SUCCESS_STATES:
                self._execute_step("trend_pdf")
            else:
                self._record_decision("trend_pdf", "skipped", "Trends did not complete.")

        if self._should_run("qa_index"):
            if not has_key:
                self._record_decision(
                    "qa_index", "blocked", f"No effective API key ({self.config.api_key_env})."
                )
            else:
                self._execute_step("qa_index")

    def _start_attempt(self, step: str) -> tuple[int, AttemptPaths]:
        next_no = len(self.store.get_attempts(self.run_id, step)) + 1
        paths = ensure_attempt_layout(
            self.runtime_root, self.tag, self.run_id, step, next_no
        )
        attempt = self.store.start_attempt(
            self.run_id,
            step,
            log_path=str(paths.log_path),
            metadata={"run_id": self.run_id, "step": step},
        )
        return int(attempt["attempt_no"]), paths

    def _execute_step(
        self, step: str, *, immediate: StepResult | None = None
    ) -> StepResult:
        attempt_no, paths = self._start_attempt(step)
        self._log(paths, f"{step}: started attempt {attempt_no}")
        try:
            result = immediate or self.child_runner(step, self, paths)
            result = self._apply_quality(step, result)
        except Exception as exc:
            self._log(paths, traceback.format_exc())
            result = StepResult(state="failed", warning=str(exc))
        self._finish_attempt(step, attempt_no, paths, result)
        return result

    def _record_decision(
        self,
        step: str,
        state: str,
        warning: str,
        *,
        total: int = 0,
    ) -> StepResult:
        return self._execute_step(
            step,
            immediate=StepResult(state=state, total=total, warning=warning),
        )

    def _apply_quality(self, step: str, result: StepResult) -> StepResult:
        if result.state == "completed" and result.errors:
            error_rate = result.errors / result.total if result.total else 1.0
            if step == "classify" and (
                result.processed == 0 or error_rate >= CLASSIFY_FAIL_ERROR_RATE
            ):
                result.state = "failed"
                result.warning = result.warning or (
                    f"Classification quality gate failed: {result.errors} of "
                    f"{result.total} rows failed."
                )
            else:
                result.state = "completed_with_warnings"
                result.warning = result.warning or (
                    f"{result.errors} of {result.total} rows failed; successful rows were retained."
                )
        if step == "trends" and result.state in _SUCCESS_STATES:
            timestamp_warning = self.prepare_result.trend_warning if self.prepare_result else None
            if timestamp_warning:
                result.state = "completed_with_warnings"
                result.warning = "; ".join(
                    value for value in (result.warning, timestamp_warning) if value
                )
        if result.state == "completed" and result.warning:
            result.state = "completed_with_warnings"
        return result

    def _finish_attempt(
        self,
        step: str,
        attempt_no: int,
        paths: AttemptPaths,
        result: StepResult,
    ) -> None:
        artifacts: list[dict[str, object]] = []
        if result.artifact_paths:
            try:
                artifacts = self._snapshot_artifacts(result.artifact_paths, paths)
            except Exception as exc:
                self._log(paths, traceback.format_exc())
                if result.state in _SUCCESS_STATES:
                    result.state = "failed"
                    result.warning = f"Artifact snapshot failed: {exc}"
        self._log(paths, f"{step}: {result.state}" + (f" — {result.warning}" if result.warning else ""))
        self.store.finish_attempt(
            self.run_id,
            step,
            attempt_no,
            state=result.state,
            processed=result.processed,
            total=result.total,
            errors=result.errors,
            warning=result.warning,
            artifacts=artifacts,
            metadata=result.metadata,
        )
        self._heartbeat()

    def _snapshot_artifacts(
        self, artifact_paths: list[Path], paths: AttemptPaths
    ) -> list[dict[str, object]]:
        descriptors: list[dict[str, object]] = []
        seen: set[Path] = set()
        staging_tag = self.staging_tag_root.resolve()
        for source_value in artifact_paths:
            source = Path(source_value).resolve()
            if source in seen:
                continue
            seen.add(source)
            if not source.exists():
                raise FileNotFoundError(source)
            try:
                relative = source.relative_to(staging_tag)
            except ValueError as exc:
                raise ValueError(f"artifact is outside run staging: {source}") from exc
            destination = paths.artifacts_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                temp = destination.with_name(destination.name + ".tmp")
                if temp.exists():
                    shutil.rmtree(temp)
                shutil.copytree(source, temp)
                os.replace(temp, destination)
            else:
                temp = destination.with_suffix(destination.suffix + ".tmp")
                shutil.copy2(source, temp)
                os.replace(temp, destination)
            descriptors.append(
                {
                    "id": relative.as_posix(),
                    "name": source.name,
                    "bytes": self._path_size(destination),
                    "path": str(destination),
                }
            )
        return descriptors

    @staticmethod
    def _path_size(path: Path) -> int:
        if path.is_file():
            return path.stat().st_size
        return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())

    @staticmethod
    def _log(paths: AttemptPaths, message: str) -> None:
        with paths.log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{time.time():.3f}] {message.rstrip()}\n")

    def _run_subprocess_step(
        self, step: str, _coordinator: "AnalysisCoordinator", paths: AttemptPaths
    ) -> StepResult:
        scripts = {
            "classify": ("classify", "classify_job.py"),
            "briefing": ("briefing", "briefing_job.py"),
            "trends": ("trend", "trend_job.py"),
            "trend_pdf": ("trend_briefing", "trend_briefing_job.py"),
            "qa_index": ("faiss_qa", "faiss_qa_job.py"),
        }
        kind, script_name = scripts[step]
        common = ["--output_root", str(self.staging_root)]
        provider = [
            "--provider", self.config.provider,
            "--model", self.config.model,
            "--api_key_env", self.config.api_key_env,
        ]
        if step in {"classify", "briefing", "trends", "qa_index"}:
            provider[2:2] = ["--base_url", self.config.base_url]
        args = list(common)
        if step in {"classify", "briefing", "trends", "qa_index"}:
            args += ["--classified_path", str(self.working_classified_path)]
        if step in {"classify", "briefing", "trends", "qa_index"}:
            args += provider
        if step == "briefing" and self.config.effective_api_key():
            args.append("--use_llm")
        if step == "trends":
            args += [
                "--n_clusters", str(self.config.n_clusters),
                "--embedding_model", self.config.embedding_model,
            ]
        if step == "qa_index":
            args += ["--embedding_model", self.config.embedding_model]
        env: dict[str, str] = {}
        if self.config.api_key:
            env[self.config.api_key_env] = self.config.api_key
        status = start_job(
            self.runtime_root,
            self.tag,
            kind,
            self.project_root / "scripts" / script_name,
            args,
            env=env,
            cwd=self.project_root,
            log_path=paths.log_path,
        )
        return monitor_job(
            self.runtime_root,
            self.tag,
            status["job_id"],
            heartbeat=self._heartbeat,
        )

    def _heartbeat(self) -> None:
        now = time.time()
        self.store.heartbeat_tag_lock(
            self.tag, self.owner_id, pid=os.getpid(), now=now
        )
        self.store.update_run(self.run_id, heartbeat_at=now)
        self._coordinator_status("running", heartbeat_at=now)

    def _coordinator_status(self, state: str, **fields: object) -> None:
        if not self.job_id:
            return
        write_status(
            job_path(self.runtime_root, self.tag, self.job_id),
            {"state": state, "run_id": self.run_id, **fields},
        )

    def _derive_run_state(self) -> str:
        states = [row["state"] for row in self.store.get_steps(self.run_id, STEP_ORDER)]
        if "failed" in states:
            return "failed"
        if any(state in {"completed_with_warnings", "blocked", "skipped"} for state in states):
            return "completed_with_warnings"
        return "completed"

    def _run_warning(self) -> str | None:
        warnings = [
            row["warning"]
            for row in self.store.get_steps(self.run_id, STEP_ORDER)
            if row.get("warning")
        ]
        return "; ".join(dict.fromkeys(warnings))[:2000] if warnings else None

    def _publish_latest(self) -> None:
        tag_root = self.runtime_root / self.tag
        tag_root.mkdir(parents=True, exist_ok=True)
        generations = tag_root / "generations"
        generations.mkdir(parents=True, exist_ok=True)
        generation_id = f"{self.run_id}-{uuid.uuid4().hex[:12]}"
        temp_root = generations / f".{generation_id}.tmp"
        generation_root = generations / generation_id
        previous_root = latest_output_root(self.runtime_root, self.tag)

        if temp_root.exists():
            shutil.rmtree(temp_root)
        temp_root.mkdir(parents=True)

        for family in _PUBLISH_FAMILIES:
            source = self.staging_tag_root / family
            previous = previous_root / family
            destination = temp_root / family
            producer = self.store.get_step(self.run_id, _FAMILY_PRODUCERS[family])
            should_publish = bool(producer and producer["state"] in _SUCCESS_STATES)

            if previous.exists():
                shutil.copytree(previous, destination)

            if should_publish:
                if not source.exists():
                    raise FileNotFoundError(
                        f"successful {producer['name']} step produced no {family} family"
                    )
                if family == "downloads" and destination.exists():
                    shutil.copytree(source, destination, dirs_exist_ok=True)
                else:
                    if destination.exists():
                        shutil.rmtree(destination)
                    shutil.copytree(source, destination)
                (destination / ".run_id").write_text(self.run_id, encoding="utf-8")

        os.replace(temp_root, generation_root)
        pointer_temp = tag_root / f".current.{generation_id}.tmp"
        if pointer_temp.exists() or pointer_temp.is_symlink():
            pointer_temp.unlink()
        os.symlink(Path("generations") / generation_id, pointer_temp)
        try:
            os.replace(pointer_temp, tag_root / "current")
        except Exception:
            if pointer_temp.exists() or pointer_temp.is_symlink():
                pointer_temp.unlink()
            shutil.rmtree(generation_root, ignore_errors=True)
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full redditgm analysis pipeline")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--job_id", default="")
    parser.add_argument("--runtime_root", required=True)
    parser.add_argument("--run_id", default="")
    parser.add_argument("--db_path", default="")
    parser.add_argument("--provider", default="openrouter")
    parser.add_argument("--base_url", default="https://openrouter.ai/api/v1")
    parser.add_argument("--model", default="gpt-oss-120b")
    parser.add_argument("--api_key_env", default="OPENROUTER_API_KEY")
    parser.add_argument("--n_clusters", type=int, default=10)
    parser.add_argument("--embedding_model", default="text-embedding-3-small")
    # When set, only execute this step and its transitive dependents; all other steps are skipped
    parser.add_argument("--retry-from-step", default="", dest="retry_from_step")
    args = parser.parse_args()

    runtime_root = Path(args.runtime_root)
    run_id = args.run_id or uuid.uuid4().hex

    # Resolve the steps_to_run list when --retry-from-step is provided
    steps_to_run: list[str] | None = None
    if args.retry_from_step:
        steps_to_run = retry_steps(args.retry_from_step)

    coordinator = AnalysisCoordinator(
        runtime_root=runtime_root,
        db_path=Path(args.db_path) if args.db_path else runtime_root / "runs.db",
        tag=args.tag,
        run_id=run_id,
        job_id=args.job_id or None,
        config=AnalyzeConfig(
            provider=args.provider,
            model=args.model,
            base_url=args.base_url,
            api_key_env=args.api_key_env,
            n_clusters=max(2, args.n_clusters),
            embedding_model=args.embedding_model,
        ),
        steps_to_run=steps_to_run,
    )
    try:
        result = coordinator.run()
    except ActiveRunError as exc:
        if args.job_id:
            write_status(
                job_path(runtime_root, args.tag, args.job_id),
                {"state": "failed", "error": str(exc), "active_run_id": exc.active_run_id},
            )
        raise SystemExit(2) from exc
    if result.get("state") == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
