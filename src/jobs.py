"""Durable job status tracking for background workers.

Each job is represented by a JSON file under:
    <runtime_root>/<tag>/jobs/<job_id>.json

Workers write heartbeats to this file so the API can detect stalled/dead jobs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# A running job whose heartbeat is older than this (seconds) is considered dead
HEARTBEAT_STALE_SECS = 120

# All valid job kinds in this system
JOB_KINDS = frozenset({"classify", "pdf_export", "trend", "trend_briefing", "faiss_qa"})

# States that mean the job is done (no longer active)
_TERMINAL_STATES = frozenset({"completed", "failed", "interrupted"})


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def job_dir(runtime_root: Path, tag: str) -> Path:
    """Return the directory that holds all job files for a given tag."""
    return runtime_root / tag / "jobs"


def job_path(runtime_root: Path, tag: str, job_id: str) -> Path:
    """Return the JSON status file path for a specific job."""
    return job_dir(runtime_root, tag) / f"{job_id}.json"


# ---------------------------------------------------------------------------
# Atomic status I/O
# ---------------------------------------------------------------------------

def write_status(path: Path, fields: dict) -> None:
    """Merge `fields` into the existing JSON at `path` and save atomically.

    - Reads the current file if it exists (silently ignores read errors)
    - Overwrites any matching keys with the new values
    - Always sets `updated_at` to the current timestamp
    - Uses a .tmp file + os.replace() so the write is crash-safe
    """
    # Ensure the directory exists before we try to write anything
    path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing data so we can merge rather than overwrite the whole file
    existing: dict = {}
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # Missing file, empty file, or corrupt JSON — start fresh
        pass

    # Merge: new fields win over existing ones
    merged = {**existing, **fields, "updated_at": time.time()}

    # Write to a sibling .tmp file first, then atomically rename
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(merged), encoding="utf-8")
    os.replace(tmp, path)  # atomic on POSIX; no partial writes visible to readers


def read_status(path: Path) -> dict | None:
    """Read and reconcile the job status at `path`.

    Returns None if the file is missing or its JSON is invalid.
    Otherwise returns the reconciled status dict (see `reconcile`).
    """
    try:
        raw = path.read_text(encoding="utf-8")
        status = json.loads(raw)
    except Exception:
        # File missing or corrupt — callers treat this as "no info"
        return None

    return reconcile(status)


# ---------------------------------------------------------------------------
# Liveness check
# ---------------------------------------------------------------------------

def reconcile(status: dict) -> dict:
    """Detect stalled/dead running jobs and upgrade their state to 'interrupted'.

    Non-running states pass through unchanged.
    Returns a NEW dict if the state changes; returns the input dict as-is otherwise
    (no mutation either way when the state stays the same).
    """
    # Terminal states (completed / failed / interrupted) are final — nothing to check
    if status.get("state") != "running":
        return status

    pid = status.get("pid")
    heartbeat_at = status.get("heartbeat_at")

    # Check whether the worker process is still alive
    pid_alive = False
    if pid is not None:
        try:
            os.kill(int(pid), 0)  # signal 0 = liveness probe, no actual signal sent
            pid_alive = True
        except OSError:
            # Process doesn't exist or we don't have permission — treat as dead
            pid_alive = False

    # Check whether the heartbeat is stale — None means not yet written (grace period)
    heartbeat_stale = (
        heartbeat_at is not None
        and (time.time() - float(heartbeat_at)) > HEARTBEAT_STALE_SECS
    )

    # Mark as interrupted only if the process is dead OR the heartbeat is stale.
    # heartbeat_at=None is a grace period (job just spawned), not a stale signal.
    if not pid_alive or heartbeat_stale:
        # Return a COPY — never mutate the caller's dict
        return {**status, "state": "interrupted", "updated_at": time.time()}

    # Both checks passed — job is genuinely running
    return status


# ---------------------------------------------------------------------------
# Job scanning
# ---------------------------------------------------------------------------

def find_active_job(runtime_root: Path, tag: str, kind: str) -> dict | None:
    """Return the reconciled status of the most recent non-terminal job of `kind`.

    Scans all *.json files in the job directory for the given tag.
    Returns None if no directory exists or all found jobs are in terminal states.
    Among eligible candidates, returns the one with the highest `started_at`.
    """
    jdir = job_dir(runtime_root, tag)
    if not jdir.exists():
        return None

    best: dict | None = None
    best_started: float = -1.0

    for json_file in jdir.glob("*.json"):
        # Skip files we can't read or that have wrong/missing kind
        try:
            raw = json.loads(json_file.read_text())
        except Exception:
            continue

        if raw.get("kind") != kind:
            continue

        # Reconcile so dead-pid / stale-heartbeat jobs get promoted to interrupted
        status = reconcile(raw)

        # Skip terminal states (completed, failed, interrupted after reconcile)
        if status.get("state") in _TERMINAL_STATES:
            continue

        # Track the most recently started active job
        started = float(status.get("started_at", 0))
        if started > best_started:
            best_started = started
            best = status

    return best


# ---------------------------------------------------------------------------
# Job launcher
# ---------------------------------------------------------------------------

def start_job(
    runtime_root: Path,
    tag: str,
    kind: str,
    script: Path,
    extra_args: list[str],
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> dict:
    """Spawn a worker subprocess and write its initial status file.

    The worker is responsible for updating the status file (heartbeat, progress,
    completion). This function only creates the file and launches the process.

    Returns the initial status dict with `state="running"` and `pid` set.
    """
    job_id = uuid.uuid4().hex
    now = time.time()

    # Build the initial status dict before spawning so we have a clean baseline
    initial_status: dict[str, Any] = {
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

    status_path = job_path(runtime_root, tag, job_id)

    # Write the pending status before spawning so the file exists before the
    # worker process might try to read it
    write_status(status_path, initial_status)

    # Build the command the worker will receive
    cmd = [
        sys.executable,
        str(script),
        "--tag", tag,
        "--job_id", job_id,
        "--runtime_root", str(runtime_root),
    ] + extra_args

    # Spawn the worker; inherit + extend the current environment
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        env={**os.environ, **(env or {})},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Update the status file with the real PID and transition to "running"
    write_status(status_path, {"state": "running", "pid": proc.pid})

    # Return a dict reflecting the final state (initial_status + overrides)
    return {**initial_status, "state": "running", "pid": proc.pid}
