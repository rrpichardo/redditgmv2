"""SQLite run ledger and immutable attempt filesystem helpers."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from src.jobs import reconcile


RUN_STATES = frozenset(
    {
        "pending",
        "running",
        "completed",
        "completed_with_warnings",
        "blocked",
        "failed",
        "cancelled",
    }
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

    @staticmethod
    def _lock_is_interrupted(row: sqlite3.Row, timestamp: float) -> bool:
        pid = row["pid"]
        pid_alive = False
        if pid is not None:
            try:
                os.kill(int(pid), 0)
                pid_alive = True
            except OSError:
                pass
        heartbeat = row["heartbeat_at"]
        heartbeat_stale = heartbeat is not None and timestamp - float(heartbeat) > 120
        return not pid_alive or heartbeat_stale

    def _reclaim_lock_in_transaction(self, row: sqlite3.Row, timestamp: float) -> None:
        self._conn.execute("DELETE FROM tag_locks WHERE tag=?", (row["tag"],))
        self._conn.execute(
            """
            UPDATE runs
            SET state='failed', ended_at=?, warning=?, heartbeat_at=?
            WHERE run_id=? AND state='pending'
            """,
            (
                timestamp,
                "Coordinator failed before adopting the reserved run.",
                timestamp,
                row["run_id"],
            ),
        )

    def _reserve_lock_in_transaction(
        self,
        tag: str,
        *,
        run_id: str,
        owner_id: str,
        pid: int | None,
        timestamp: float,
    ) -> LockAcquireResult:
        row = self._conn.execute(
            "SELECT * FROM tag_locks WHERE tag=?", (tag,)
        ).fetchone()
        reclaimed = False
        if row:
            if not self._lock_is_interrupted(row, timestamp):
                return LockAcquireResult(
                    False,
                    str(row["run_id"]),
                    str(row["owner_id"]),
                    False,
                )
            self._reclaim_lock_in_transaction(row, timestamp)
            reclaimed = True
        self._conn.execute(
            """
            INSERT INTO tag_locks (tag, owner_id, run_id, pid, acquired_at, heartbeat_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (tag, owner_id, run_id, pid, timestamp, timestamp),
        )
        return LockAcquireResult(True, run_id, owner_id, reclaimed)

    def reserve_new_run(
        self,
        tag: str,
        *,
        run_id: str,
        owner_id: str,
        pid: int | None,
        config: dict[str, Any],
        step_names: Iterable[str],
        now: float | None = None,
    ) -> LockAcquireResult:
        """Atomically reserve a tag and create a pending run ledger."""
        timestamp = time.time() if now is None else float(now)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            result = self._reserve_lock_in_transaction(
                tag,
                run_id=run_id,
                owner_id=owner_id,
                pid=pid,
                timestamp=timestamp,
            )
            if not result.acquired:
                self._conn.execute("COMMIT")
                return result
            self._conn.execute(
                """
                INSERT INTO runs
                    (run_id, tag, state, started_at, config_json, coordinator_pid, heartbeat_at)
                VALUES (?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    run_id,
                    tag,
                    timestamp,
                    _json_dump(_scrub_secrets(config)),
                    pid,
                    timestamp,
                ),
            )
            for step in step_names:
                self.upsert_step(run_id, str(step), state="pending")
            self._conn.execute("COMMIT")
            return result
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def reserve_retry(
        self,
        tag: str,
        *,
        run_id: str,
        owner_id: str,
        pid: int | None,
        step_names: Iterable[str],
        now: float | None = None,
    ) -> LockAcquireResult:
        """Atomically reserve a tag and reset a terminal run for retry."""
        timestamp = time.time() if now is None else float(now)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            run = self._conn.execute(
                "SELECT * FROM runs WHERE run_id=? AND tag=?", (run_id, tag)
            ).fetchone()
            if not run:
                raise KeyError(run_id)
            if run["state"] in {"pending", "running"}:
                raise ValueError("run is still active")
            result = self._reserve_lock_in_transaction(
                tag,
                run_id=run_id,
                owner_id=owner_id,
                pid=pid,
                timestamp=timestamp,
            )
            if not result.acquired:
                self._conn.execute("COMMIT")
                return result
            self._conn.execute(
                """
                UPDATE runs
                SET state='pending', ended_at=NULL, warning=NULL,
                    coordinator_pid=?, heartbeat_at=?
                WHERE run_id=?
                """,
                (pid, timestamp, run_id),
            )
            for step in step_names:
                self.upsert_step(run_id, str(step), state="pending")
            self._conn.execute("COMMIT")
            return result
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def adopt_reserved_run(
        self,
        tag: str,
        *,
        run_id: str,
        owner_id: str,
        pid: int,
        now: float | None = None,
    ) -> bool:
        """Adopt a matching pending run without creating or reviving rows."""
        timestamp = time.time() if now is None else float(now)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            run = self._conn.execute(
                "SELECT 1 FROM runs WHERE run_id=? AND tag=? AND state='pending'",
                (run_id, tag),
            ).fetchone()
            lock = self._conn.execute(
                "SELECT 1 FROM tag_locks WHERE tag=? AND run_id=? AND owner_id=?",
                (tag, run_id, owner_id),
            ).fetchone()
            if not run or not lock:
                self._conn.execute("COMMIT")
                return False
            self._conn.execute(
                """
                UPDATE runs
                SET state='running', coordinator_pid=?, heartbeat_at=?,
                    ended_at=NULL, warning=NULL
                WHERE run_id=?
                """,
                (pid, timestamp, run_id),
            )
            self._conn.execute(
                """
                UPDATE tag_locks SET pid=?, heartbeat_at=?
                WHERE tag=? AND owner_id=?
                """,
                (pid, timestamp, tag, owner_id),
            )
            self._conn.execute("COMMIT")
            return True
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def attach_reserved_pid(
        self,
        tag: str,
        *,
        run_id: str,
        owner_id: str,
        pid: int,
        now: float | None = None,
    ) -> bool:
        """Attach the spawned child PID before the coordinator adopts the run."""
        timestamp = time.time() if now is None else float(now)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            run_cursor = self._conn.execute(
                """
                UPDATE runs SET coordinator_pid=?, heartbeat_at=?
                WHERE run_id=? AND tag=? AND state IN ('pending', 'running')
                """,
                (pid, timestamp, run_id, tag),
            )
            lock_cursor = self._conn.execute(
                """
                UPDATE tag_locks SET pid=?, heartbeat_at=?
                WHERE tag=? AND run_id=? AND owner_id=?
                """,
                (pid, timestamp, tag, run_id, owner_id),
            )
            ok = run_cursor.rowcount == 1 and lock_cursor.rowcount == 1
            self._conn.execute("COMMIT")
            return ok
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def fail_reserved_run(
        self,
        tag: str,
        *,
        run_id: str,
        owner_id: str,
        warning: str,
        now: float | None = None,
    ) -> bool:
        """Fail a pending reservation and release only its matching lock."""
        timestamp = time.time() if now is None else float(now)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = self._conn.execute(
                """
                UPDATE runs
                SET state='failed', ended_at=?, warning=?, heartbeat_at=?
                WHERE run_id=? AND tag=? AND state='pending'
                """,
                (timestamp, warning[:1000], timestamp, run_id, tag),
            )
            self._conn.execute(
                "DELETE FROM tag_locks WHERE tag=? AND run_id=? AND owner_id=?",
                (tag, run_id, owner_id),
            )
            self._conn.execute("COMMIT")
            return cursor.rowcount == 1
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def reconcile_pending_run(self, run_id: str, *, now: float | None = None) -> bool:
        """Fail and unlock a pending run whose coordinator died or went stale."""
        timestamp = time.time() if now is None else float(now)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            run = self._conn.execute(
                "SELECT * FROM runs WHERE run_id=? AND state='pending'", (run_id,)
            ).fetchone()
            if not run:
                self._conn.execute("COMMIT")
                return False
            pseudo_lock = {
                "pid": run["coordinator_pid"],
                "heartbeat_at": run["heartbeat_at"],
            }
            pid_alive = False
            if pseudo_lock["pid"] is not None:
                try:
                    os.kill(int(pseudo_lock["pid"]), 0)
                    pid_alive = True
                except OSError:
                    pass
            heartbeat = pseudo_lock["heartbeat_at"]
            stale = heartbeat is not None and timestamp - float(heartbeat) > 120
            if pid_alive and not stale:
                self._conn.execute("COMMIT")
                return False
            self._conn.execute(
                """
                UPDATE runs
                SET state='failed', ended_at=?, warning=?, heartbeat_at=?
                WHERE run_id=? AND state='pending'
                """,
                (
                    timestamp,
                    "Coordinator failed before adopting the reserved run.",
                    timestamp,
                    run_id,
                ),
            )
            self._conn.execute(
                "DELETE FROM tag_locks WHERE tag=? AND run_id=?",
                (run["tag"], run_id),
            )
            self._conn.execute("COMMIT")
            return True
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

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
