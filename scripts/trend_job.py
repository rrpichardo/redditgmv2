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
    """Stub — Phase 5 replaces this with velocity/z-score trend detection."""
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
