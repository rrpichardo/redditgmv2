"""Subprocess job: synthesize a strategy briefing from classified data."""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.briefing import write_briefing
from src.gm_insights import ProviderConfig, analyzed_frame, load_working_classified
from src.jobs import job_path, write_status


def briefing_output_path(output_root: Path, tag: str) -> Path:
    return output_root / tag / "reports" / "gm_reddit_synthesis_report.md"


def run_briefing_job(
    tag: str,
    job_id: str,
    runtime_root: Path,
    classified_path: Path,
    provider: ProviderConfig,
    *,
    output_root: Path | None = None,
    use_llm: bool = False,
) -> None:
    """Generate a briefing and write lifecycle fields to the canonical job status."""
    status_path = job_path(runtime_root, tag, job_id)
    destination_root = Path(output_root) if output_root is not None else Path(runtime_root)
    output_path = briefing_output_path(destination_root, tag)
    try:
        write_status(status_path, {"heartbeat_at": time.time(), "phase": "loading_classified"})
        # Prepared working sets already contain the full label schema, so
        # load_classified() would incorrectly upgrade pending blank modes to
        # "imported". Preserve those blanks until classification completes.
        frame = load_working_classified(classified_path)
        analyzed_rows = len(analyzed_frame(frame))
        result = write_briefing(
            frame,
            output_path,
            provider=provider,
            use_llm=use_llm,
            fallback_on_error=True,
        )
        fields = {
            "state": "completed",
            "completed_at": time.time(),
            "processed": analyzed_rows,
            "total": len(frame),
            "artifact_paths": [str(output_path)],
            "used_llm": result.used_llm,
        }
        if result.warning:
            fields["briefing_warning"] = result.warning
        write_status(status_path, fields)
    except Exception as exc:
        traceback.print_exc()
        write_status(
            status_path,
            {"state": "failed", "error": str(exc), "completed_at": time.time()},
        )
        raise SystemExit(1) from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Strategy briefing worker")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--job_id", required=True)
    parser.add_argument("--runtime_root", required=True)
    parser.add_argument("--classified_path", required=True)
    parser.add_argument("--output_root", default="")
    parser.add_argument("--provider", default="openrouter")
    parser.add_argument("--base_url", default="https://openrouter.ai/api/v1")
    parser.add_argument("--model", default="gpt-oss-120b")
    parser.add_argument("--api_key_env", default="OPENROUTER_API_KEY")
    parser.add_argument("--use_llm", action="store_true")
    args = parser.parse_args()

    provider = ProviderConfig(
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        api_key="",
    )
    run_briefing_job(
        tag=args.tag,
        job_id=args.job_id,
        runtime_root=Path(args.runtime_root),
        classified_path=Path(args.classified_path),
        provider=provider,
        output_root=Path(args.output_root) if args.output_root else None,
        use_llm=args.use_llm,
    )


if __name__ == "__main__":
    main()
