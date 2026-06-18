# scripts/pdf_export_job.py
"""Subprocess job: render chart PNGs and package as ZIP or briefing PDF."""
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
    classified_path: Path | None = None,
) -> None:
    """Render charts and export as ZIP ("charts") or PDF ("briefing").

    Requires a classified CSV at classified_path. Missing file → failed state.
    All output artifacts land under runtime_root/<tag>/downloads/.
    """
    status_path = job_path(runtime_root, tag, job_id)
    try:
        # Initial heartbeat — keeps the API from marking the job stale
        write_status(status_path, {"heartbeat_at": time.time(), "total": 0})

        if classified_path is None or not Path(classified_path).exists():
            raise FileNotFoundError(
                f"Classified CSV not found: {classified_path}. "
                "Run preview or LLM classification first."
            )
        classified_path = Path(classified_path)

        # --- Import heavy modules here (not at module load) ---
        from src.gm_insights import (  # noqa: PLC0415
            filter_analyzed,
            load_classified,
            fallback_synthesis,
            summary_payload,
        )
        from src.charts import build_chart_payload  # noqa: PLC0415
        from src.pdf_export import (  # noqa: PLC0415
            build_briefing_pdf,
            build_charts_zip,
            render_all_charts,
        )

        # Load and filter data
        df = load_classified(classified_path)
        selected = filter_analyzed(df, {}) if not df.empty else df

        # Build the chart spec + data payload
        payload = build_chart_payload(selected)

        write_status(status_path, {"heartbeat_at": time.time()})

        # Render PNGs into runtime/<tag>/charts/<kind>/
        charts_dir = runtime_root / tag / "charts" / kind
        png_paths = render_all_charts(payload["chart_specs"], payload["chart_data"], charts_dir)

        write_status(status_path, {
            "heartbeat_at": time.time(),
            "processed": len(png_paths),
        })

        # Package artifacts
        downloads_dir = runtime_root / tag / "downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)

        if kind == "charts":
            zip_path = downloads_dir / f"{tag}_charts.zip"
            build_charts_zip(png_paths, zip_path)
            artifact_paths = [str(zip_path)]
        else:  # briefing
            summary = summary_payload(selected) if not selected.empty else {}
            report_md = runtime_root / tag / "reports" / "gm_reddit_synthesis_report.md"
            report_text = (
                report_md.read_text(encoding="utf-8")
                if report_md.exists()
                else fallback_synthesis(summary)
            )
            pdf_path = downloads_dir / f"{tag}_briefing.pdf"
            build_briefing_pdf(png_paths, summary, report_text, pdf_path)
            artifact_paths = [str(pdf_path)]

        write_status(status_path, {
            "state": "completed",
            "processed": len(png_paths),
            "total": len(png_paths),
            "completed_at": time.time(),
            "artifact_paths": artifact_paths,
        })

    except Exception as exc:
        write_status(status_path, {
            "state": "failed",
            "error": str(exc),
            "completed_at": time.time(),
        })
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="PDF/chart export worker")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--job_id", required=True)
    parser.add_argument("--runtime_root", required=True)
    parser.add_argument("--kind", default="charts", choices=["charts", "briefing"])
    parser.add_argument("--classified_path", default="", help="Path to classified CSV")
    args = parser.parse_args()

    run_pdf_export_job(
        tag=args.tag,
        job_id=args.job_id,
        runtime_root=Path(args.runtime_root),
        kind=args.kind,
        classified_path=Path(args.classified_path) if args.classified_path else None,
    )


if __name__ == "__main__":
    main()
