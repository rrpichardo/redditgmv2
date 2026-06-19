# scripts/trend_briefing_job.py
"""Subprocess job: generate a trend briefing PDF from existing cluster + trend signal artifacts."""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.jobs import job_path, write_status
from src.trend_insights import trends_dir


def run_trend_briefing_job(
    tag: str,
    job_id: str,
    runtime_root: Path,
    provider: str = "openrouter",
    model: str = "",
    api_key_env: str = "OPENROUTER_API_KEY",
    api_key: str = "",
    output_root: Path | None = None,
) -> None:
    """Load cluster artifacts + trend signals, then render a trend briefing PDF."""
    status_path = job_path(runtime_root, tag, job_id)

    def heartbeat(note: str = "") -> None:
        fields: dict = {"heartbeat_at": time.time()}
        if note:
            fields["phase"] = note
        write_status(status_path, fields)

    destination_root = Path(output_root) if output_root is not None else Path(runtime_root)
    try:
        heartbeat("loading_artifacts")

        tdir = trends_dir(destination_root, tag)
        labels_path = tdir / "cluster_labels.json"
        examples_path = tdir / "cluster_examples.json"
        signals_path = tdir / "trend_signals.json"

        if not labels_path.exists():
            raise FileNotFoundError(
                "cluster_labels.json not found. Run /api/trends/run first."
            )

        labels: dict = json.loads(labels_path.read_text(encoding="utf-8"))
        examples: dict = json.loads(examples_path.read_text(encoding="utf-8")) if examples_path.exists() else {}
        signals: dict = json.loads(signals_path.read_text(encoding="utf-8")) if signals_path.exists() else {}

        heartbeat("rendering_pdf")

        from src.pdf_export import build_trend_briefing_pdf

        downloads = destination_root / tag / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        pdf_path = downloads / f"{tag}_trend_briefing.pdf"

        build_trend_briefing_pdf(
            labels=labels,
            examples=examples,
            signals=signals,
            pdf_path=pdf_path,
        )

        write_status(status_path, {
            "state": "completed",
            "completed_at": time.time(),
            "artifact_paths": [str(pdf_path)],
        })

    except Exception as exc:
        traceback.print_exc()
        write_status(status_path, {
            "state": "failed",
            "error": str(exc),
            "completed_at": time.time(),
        })
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 5 trend briefing PDF worker")

    parser.add_argument("--tag", required=True)
    parser.add_argument("--job_id", required=True)
    parser.add_argument("--runtime_root", required=True)
    parser.add_argument("--output_root", default="")

    # LLM provider (optional — for future narrative generation)
    parser.add_argument("--provider", default="openrouter")
    parser.add_argument("--model", default="")
    parser.add_argument("--api_key_env", default="OPENROUTER_API_KEY")

    args = parser.parse_args()

    run_trend_briefing_job(
        tag=args.tag,
        job_id=args.job_id,
        runtime_root=Path(args.runtime_root),
        provider=args.provider,
        model=args.model,
        api_key_env=args.api_key_env,
        output_root=Path(args.output_root) if args.output_root else None,
    )


if __name__ == "__main__":
    main()
