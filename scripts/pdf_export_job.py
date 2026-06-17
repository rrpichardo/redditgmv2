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
