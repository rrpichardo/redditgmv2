"""Subprocess job: render the synthesis briefing PDF without blocking Markdown."""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.jobs import job_path, write_status
from src.pdf_export import build_synthesis_pdf_artifact


def run_synthesis_pdf_job(
    tag: str,
    job_id: str,
    runtime_root: Path,
    classified_path: Path,
    *,
    output_root: Path | None = None,
) -> None:
    """Render a generation-scoped PDF while retaining Markdown on render failure."""
    status_path = job_path(runtime_root, tag, job_id)
    destination_root = Path(output_root) if output_root is not None else Path(runtime_root)
    reports = destination_root / tag / "reports"
    markdown_path = reports / "gm_reddit_synthesis_report.md"
    pdf_path = reports / "gm_reddit_synthesis_report.pdf"
    charts_dir = destination_root / tag / "charts" / "synthesis"

    try:
        write_status(status_path, {"heartbeat_at": time.time(), "phase": "loading_briefing"})
        if not markdown_path.is_file():
            raise FileNotFoundError(f"Synthesis Markdown not found: {markdown_path}")
        report_text = markdown_path.read_text(encoding="utf-8")
        pdf_path.unlink(missing_ok=True)

        write_status(status_path, {"heartbeat_at": time.time(), "phase": "rendering_pdf"})
        try:
            build_synthesis_pdf_artifact(
                Path(classified_path), report_text, pdf_path, charts_dir
            )
        except Exception as pdf_exc:
            traceback.print_exc()
            pdf_path.unlink(missing_ok=True)
            write_status(
                status_path,
                {
                    "state": "completed_with_warnings",
                    "completed_at": time.time(),
                    "artifact_paths": [str(markdown_path)],
                    "failed_artifacts": ["pdf"],
                    "formats": {"markdown": "ready", "pdf": "failed"},
                    "warning": f"Markdown briefing completed; PDF rendering failed: {pdf_exc}",
                },
            )
            return

        write_status(
            status_path,
            {
                "state": "completed",
                "completed_at": time.time(),
                "artifact_paths": [str(markdown_path), str(pdf_path)],
                "failed_artifacts": [],
                "formats": {"markdown": "ready", "pdf": "ready"},
            },
        )
    except Exception as exc:
        traceback.print_exc()
        write_status(
            status_path,
            {"state": "failed", "error": str(exc), "completed_at": time.time()},
        )
        raise SystemExit(1) from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthesis briefing PDF worker")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--job_id", required=True)
    parser.add_argument("--runtime_root", required=True)
    parser.add_argument("--classified_path", required=True)
    parser.add_argument("--output_root", default="")
    args = parser.parse_args()
    run_synthesis_pdf_job(
        args.tag,
        args.job_id,
        Path(args.runtime_root),
        Path(args.classified_path),
        output_root=Path(args.output_root) if args.output_root else None,
    )


if __name__ == "__main__":
    main()
