"""SQLite run ledger and immutable attempt filesystem helpers."""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from src.jobs import reconcile


RUN_STATES = frozenset(
    {"running", "completed", "completed_with_warnings", "blocked", "failed", "cancelled"}
)
STEP_STATES = frozenset(
    {
        "pending",
        "running",
        "completed",
        "completed_with_warnings",
        "blocked",
        "skipped",
        "failed",
        "cancelled",
    }
)


@dataclass(frozen=True)
class LockAcquireResult:
    acquired: bool
    active_run_id: str | None = None
    active_owner_id: str | None = None
    reclaimed: bool = False


@dataclass(frozen=True)
class AttemptPaths:
    root: Path
    artifacts_dir: Path
    log_path: Path


def _safe_component(value: str, label: str) -> str:
    cleaned = "".join(ch for ch in str(value) if ch.isalnum() or ch in "-_")
    if not cleaned or cleaned != str(value):
        raise ValueError(f"invalid {label}: {value!r}")
    return cleaned


def attempt_paths(
    runtime_root: Path,
    tag: str,
    run_id: str,
    step: str,
    attempt_no: int,
) -> AttemptPaths:
    """Return path-validated locations for one run step attempt."""
    if int(attempt_no) < 1:
        raise ValueError("attempt_no must be at least 1")
    root = (
        Path(runtime_root)
        / _safe_component(tag, "tag")
        / "runs"
        / _safe_component(run_id, "run_id")
        / "attempts"
        / _safe_component(step, "step")
        / str(int(attempt_no))
    )
    return AttemptPaths(root=root, artifacts_dir=root / "artifacts", log_path=root / "step.log")


def ensure_attempt_layout(
    runtime_root: Path,
    tag: str,
    run_id: str,
    step: str,
    attempt_no: int,
) -> AttemptPaths:
    """Create the immutable-attempt directory skeleton and return its paths."""
    paths = attempt_paths(runtime_root, tag, run_id, step, attempt_no)
    paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
    paths.log_path.touch(exist_ok=True)
    return paths


def run_staging_root(runtime_root: Path, tag: str, run_id: str) -> Path:
    """Return a run-local worker runtime root used before publication."""
    return (
        Path(runtime_root)
        / _safe_component(tag, "tag")
        / "runs"
        / _safe_component(run_id, "run_id")
        / "staging"
    )


def latest_output_root(runtime_root: Path, tag: str) -> Path:
    """Return the atomically published output generation, or the legacy tag root."""
    tag_root = Path(runtime_root) / _safe_component(tag, "tag")
    current = tag_root / "current"
    if current.is_symlink() or current.exists():
        return current
    return tag_root


def _scrub_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "").replace("_", "")
            if any(marker in normalized for marker in ("apikey", "secret", "token", "password")):
                continue
            clean[str(key)] = _scrub_secrets(child)
        return clean
    if isinstance(value, list):
        return [_scrub_secrets(item) for item in value]
    return value


def _json_dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


class RunStore:
    """Transactional SQLite metadata store for coordinated analysis runs."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self.db_path,
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._create_schema()

    def __enter__(self) -> "RunStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    def journal_mode(self) -> str:
        return str(self._conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()

    def _create_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                tag TEXT NOT NULL,
                state TEXT NOT NULL,
                started_at REAL NOT NULL,
                ended_at REAL,
                config_json TEXT NOT NULL,
                warning TEXT,
                coordinator_pid INTEGER,
                heartbeat_at REAL
            );
            CREATE INDEX IF NOT EXISTS runs_tag_started_idx
                ON runs(tag, started_at DESC);

            CREATE TABLE IF NOT EXISTS steps (
                run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                state TEXT NOT NULL,
                attempt_no INTEGER NOT NULL DEFAULT 0,
                started_at REAL,
                ended_at REAL,
                processed INTEGER NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0,
                errors INTEGER NOT NULL DEFAULT 0,
                error_rate REAL NOT NULL DEFAULT 0,
                log_path TEXT,
                warning TEXT,
                artifacts_json TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY (run_id, name)
            );

            CREATE TABLE IF NOT EXISTS attempts (
                run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                step TEXT NOT NULL,
                attempt_no INTEGER NOT NULL,
                state TEXT NOT NULL,
                started_at REAL NOT NULL,
                ended_at REAL,
                processed INTEGER NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0,
                errors INTEGER NOT NULL DEFAULT 0,
                error_rate REAL NOT NULL DEFAULT 0,
                log_path TEXT NOT NULL,
                warning TEXT,
                artifacts_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (run_id, step, attempt_no)
            );

            CREATE TABLE IF NOT EXISTS tag_locks (
                tag TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                pid INTEGER,
                acquired_at REAL NOT NULL,
                heartbeat_at REAL
            );
            """
        )

    def create_run(
        self,
        run_id: str,
        tag: str,
        *,
        config: dict[str, Any],
        state: str = "running",
        started_at: float | None = None,
        coordinator_pid: int | None = None,
        heartbeat_at: float | None = None,
    ) -> dict[str, Any]:
        self._validate_run_state(state)
        started = time.time() if started_at is None else float(started_at)
        self._conn.execute(
            """
            INSERT INTO runs
                (run_id, tag, state, started_at, config_json, coordinator_pid, heartbeat_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                tag,
                state,
                started,
                _json_dump(_scrub_secrets(config)),
                coordinator_pid,
                heartbeat_at,
            ),
        )
        return self.get_run(run_id)

    def update_run(self, run_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {"state", "ended_at", "warning", "coordinator_pid", "heartbeat_at"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unsupported run fields: {sorted(unknown)}")
        if "state" in fields:
            self._validate_run_state(str(fields["state"]))
        if not fields:
            return self.get_run(run_id)
        assignments = ", ".join(f"{name}=?" for name in fields)
        cursor = self._conn.execute(
            f"UPDATE runs SET {assignments} WHERE run_id=?",
            (*fields.values(), run_id),
        )
        if cursor.rowcount != 1:
            raise KeyError(run_id)
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return self._run_dict(row) if row else None

    def list_runs(self, tag: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM runs WHERE tag=? ORDER BY started_at DESC, run_id DESC LIMIT ?",
            (tag, max(0, int(limit))),
        ).fetchall()
        return [self._run_dict(row) for row in rows]

    def upsert_step(self, run_id: str, name: str, *, state: str, **fields: Any) -> dict[str, Any]:
        self._validate_step_state(state)
        allowed = {
            "attempt_no",
            "started_at",
            "ended_at",
            "processed",
            "total",
            "errors",
            "error_rate",
            "log_path",
            "warning",
            "artifacts",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unsupported step fields: {sorted(unknown)}")
        payload = {
            "attempt_no": 0,
            "started_at": None,
            "ended_at": None,
            "processed": 0,
            "total": 0,
            "errors": 0,
            "error_rate": 0.0,
            "log_path": None,
            "warning": None,
            "artifacts": [],
            **fields,
        }
        self._conn.execute(
            """
            INSERT INTO steps
                (run_id, name, state, attempt_no, started_at, ended_at, processed, total,
                 errors, error_rate, log_path, warning, artifacts_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, name) DO UPDATE SET
                state=excluded.state,
                attempt_no=excluded.attempt_no,
                started_at=excluded.started_at,
                ended_at=excluded.ended_at,
                processed=excluded.processed,
                total=excluded.total,
                errors=excluded.errors,
                error_rate=excluded.error_rate,
                log_path=excluded.log_path,
                warning=excluded.warning,
                artifacts_json=excluded.artifacts_json
            """,
            (
                run_id,
                name,
                state,
                int(payload["attempt_no"]),
                payload["started_at"],
                payload["ended_at"],
                int(payload["processed"]),
                int(payload["total"]),
                int(payload["errors"]),
                float(payload["error_rate"]),
                payload["log_path"],
                payload["warning"],
                _json_dump(payload["artifacts"]),
            ),
        )
        return self.get_step(run_id, name)

    def get_step(self, run_id: str, name: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM steps WHERE run_id=? AND name=?", (run_id, name)
        ).fetchone()
        return self._step_dict(row) if row else None

    def get_steps(self, run_id: str, order: Iterable[str] | None = None) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM steps WHERE run_id=?", (run_id,)
        ).fetchall()
        decoded = [self._step_dict(row) for row in rows]
        if order is None:
            return sorted(decoded, key=lambda row: row["name"])
        positions = {name: index for index, name in enumerate(order)}
        return sorted(decoded, key=lambda row: positions.get(row["name"], len(positions)))

    def start_attempt(
        self,
        run_id: str,
        step: str,
        *,
        log_path: str,
        started_at: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        started = time.time() if started_at is None else float(started_at)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM attempts WHERE run_id=? AND step=?",
                (run_id, step),
            ).fetchone()
            attempt_no = int(row[0])
            self._conn.execute(
                """
                INSERT INTO attempts
                    (run_id, step, attempt_no, state, started_at, log_path, metadata_json)
                VALUES (?, ?, ?, 'running', ?, ?, ?)
                """,
                (run_id, step, attempt_no, started, log_path, _json_dump(metadata or {})),
            )
            self._conn.execute(
                """
                INSERT INTO steps (run_id, name, state, attempt_no, started_at, log_path)
                VALUES (?, ?, 'running', ?, ?, ?)
                ON CONFLICT(run_id, name) DO UPDATE SET
                    state='running', attempt_no=excluded.attempt_no,
                    started_at=excluded.started_at, ended_at=NULL,
                    processed=0, total=0, errors=0, error_rate=0,
                    log_path=excluded.log_path, warning=NULL, artifacts_json='[]'
                """,
                (run_id, step, attempt_no, started, log_path),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return self.get_attempt(run_id, step, attempt_no)

    def finish_attempt(
        self,
        run_id: str,
        step: str,
        attempt_no: int,
        *,
        state: str,
        ended_at: float | None = None,
        processed: int = 0,
        total: int = 0,
        errors: int = 0,
        error_rate: float | None = None,
        warning: str | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._validate_step_state(state)
        ended = time.time() if ended_at is None else float(ended_at)
        rate = float(error_rate) if error_rate is not None else (
            float(errors) / float(total) if total else 0.0
        )
        artifact_rows = artifacts or []
        metadata_rows = metadata or {}
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = self._conn.execute(
                """
                UPDATE attempts SET state=?, ended_at=?, processed=?, total=?, errors=?,
                    error_rate=?, warning=?, artifacts_json=?, metadata_json=?
                WHERE run_id=? AND step=? AND attempt_no=? AND state='running'
                """,
                (
                    state,
                    ended,
                    int(processed),
                    int(total),
                    int(errors),
                    rate,
                    warning,
                    _json_dump(artifact_rows),
                    _json_dump(metadata_rows),
                    run_id,
                    step,
                    int(attempt_no),
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"attempt is missing or already terminal: {run_id}/{step}/{attempt_no}")
            self._conn.execute(
                """
                UPDATE steps SET state=?, ended_at=?, processed=?, total=?, errors=?, error_rate=?,
                    warning=?, artifacts_json=?
                WHERE run_id=? AND name=? AND attempt_no=?
                """,
                (
                    state,
                    ended,
                    int(processed),
                    int(total),
                    int(errors),
                    rate,
                    warning,
                    _json_dump(artifact_rows),
                    run_id,
                    step,
                    int(attempt_no),
                ),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return self.get_attempt(run_id, step, attempt_no)

    def get_attempt(self, run_id: str, step: str, attempt_no: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM attempts WHERE run_id=? AND step=? AND attempt_no=?",
            (run_id, step, int(attempt_no)),
        ).fetchone()
        return self._attempt_dict(row) if row else None

    def get_attempts(self, run_id: str, step: str | None = None) -> list[dict[str, Any]]:
        if step is None:
            rows = self._conn.execute(
                "SELECT * FROM attempts WHERE run_id=? ORDER BY step, attempt_no", (run_id,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM attempts WHERE run_id=? AND step=? ORDER BY attempt_no",
                (run_id, step),
            ).fetchall()
        return [self._attempt_dict(row) for row in rows]

    def acquire_tag_lock(
        self,
        tag: str,
        *,
        owner_id: str,
        run_id: str,
        pid: int | None,
        now: float | None = None,
    ) -> LockAcquireResult:
        timestamp = time.time() if now is None else float(now)
        reclaimed = False
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute("SELECT * FROM tag_locks WHERE tag=?", (tag,)).fetchone()
            if row:
                existing = dict(row)
                if existing["owner_id"] == owner_id:
                    self._conn.execute(
                        "UPDATE tag_locks SET heartbeat_at=?, pid=?, run_id=? WHERE tag=?",
                        (timestamp, pid, run_id, tag),
                    )
                    self._conn.execute("COMMIT")
                    return LockAcquireResult(True, run_id, owner_id, False)
                status = reconcile(
                    {
                        "state": "running",
                        "pid": existing["pid"],
                        "heartbeat_at": existing["heartbeat_at"],
                    }
                )
                if status.get("state") != "interrupted":
                    self._conn.execute("COMMIT")
                    return LockAcquireResult(
                        False,
                        str(existing["run_id"]),
                        str(existing["owner_id"]),
                        False,
                    )
                self._conn.execute("DELETE FROM tag_locks WHERE tag=?", (tag,))
                reclaimed = True
            self._conn.execute(
                """
                INSERT INTO tag_locks (tag, owner_id, run_id, pid, acquired_at, heartbeat_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (tag, owner_id, run_id, pid, timestamp, timestamp),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return LockAcquireResult(True, run_id, owner_id, reclaimed)

    def heartbeat_tag_lock(
        self, tag: str, owner_id: str, *, pid: int | None = None, now: float | None = None
    ) -> bool:
        timestamp = time.time() if now is None else float(now)
        if pid is None:
            cursor = self._conn.execute(
                "UPDATE tag_locks SET heartbeat_at=? WHERE tag=? AND owner_id=?",
                (timestamp, tag, owner_id),
            )
        else:
            cursor = self._conn.execute(
                "UPDATE tag_locks SET heartbeat_at=?, pid=? WHERE tag=? AND owner_id=?",
                (timestamp, pid, tag, owner_id),
            )
        return cursor.rowcount == 1

    def release_tag_lock(self, tag: str, owner_id: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM tag_locks WHERE tag=? AND owner_id=?", (tag, owner_id)
        )
        return cursor.rowcount == 1

    def get_tag_lock(self, tag: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM tag_locks WHERE tag=?", (tag,)).fetchone()
        return dict(row) if row else None

    def reconcile_tag_lock(self, tag: str) -> bool:
        """Delete a stale tag lock; return True only when one was reclaimed."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute("SELECT * FROM tag_locks WHERE tag=?", (tag,)).fetchone()
            if not row:
                self._conn.execute("COMMIT")
                return False
            status = reconcile(
                {"state": "running", "pid": row["pid"], "heartbeat_at": row["heartbeat_at"]}
            )
            if status.get("state") != "interrupted":
                self._conn.execute("COMMIT")
                return False
            self._conn.execute("DELETE FROM tag_locks WHERE tag=?", (tag,))
            self._conn.execute("COMMIT")
            return True
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def prune_attempt_artifacts(
        self, runtime_root: Path, tag: str, *, keep_last_runs: int
    ) -> list[str]:
        """Prune old attempt artifact blobs while retaining rows and step.log files."""
        keep = max(0, int(keep_last_runs))
        runs = self.list_runs(tag, limit=1_000_000)
        pruned: list[str] = []
        for run in runs[keep:]:
            attempts_root = (
                Path(runtime_root)
                / _safe_component(tag, "tag")
                / "runs"
                / _safe_component(run["run_id"], "run_id")
                / "attempts"
            )
            removed = False
            for artifacts_dir in attempts_root.glob("*/*/artifacts"):
                if artifacts_dir.is_dir():
                    shutil.rmtree(artifacts_dir)
                    removed = True
            if removed:
                pruned.append(run["run_id"])
        return pruned

    @staticmethod
    def _validate_run_state(state: str) -> None:
        if state not in RUN_STATES:
            raise ValueError(f"invalid run state: {state}")

    @staticmethod
    def _validate_step_state(state: str) -> None:
        if state not in STEP_STATES:
            raise ValueError(f"invalid step state: {state}")

    @staticmethod
    def _run_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "tag": row["tag"],
            "state": row["state"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "config": _json_load(row["config_json"], {}),
            "warning": row["warning"],
            "coordinator_pid": row["coordinator_pid"],
            "heartbeat_at": row["heartbeat_at"],
        }

    @staticmethod
    def _step_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["artifacts"] = _json_load(result.pop("artifacts_json"), [])
        return result

    @staticmethod
    def _attempt_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["artifacts"] = _json_load(result.pop("artifacts_json"), [])
        result["metadata"] = _json_load(result.pop("metadata_json"), {})
        return result
