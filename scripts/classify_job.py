# Full-run LLM classification worker script.
# Called as a subprocess by start_job(); also importable for direct testing.

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path when run directly as a subprocess.
# This must happen before any src.* imports.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import argparse
import time

# Module-level imports so patch("scripts.classify_job.classify_with_llm") works in tests
import pandas as pd

from src.gm_insights import (
    classify_with_llm,
    complete_label,
    save_classified,
    ensure_label_columns,
    ProviderConfig,
)
from src.jobs import job_path, write_status

# Process pending rows in chunks of this size before writing a heartbeat
CHUNK_SIZE = 10


def run_classify_job(
    tag: str,
    job_id: str,
    runtime_root: Path,
    classified_path: Path,
    provider: ProviderConfig,
    limit: int = 0,
) -> None:
    """Classify all pending rows in classified_path and write status updates.

    Pending rows are those where skip_classification is False AND
    classifier_mode is empty string "". Already-classified rows are skipped.

    Per-row LLM errors set classifier_mode="error" and sentiment="error" but
    do NOT abort the job — the final state is always "completed". Only a
    catastrophic exception (e.g., CSV unreadable) produces state="failed".
    """
    # Resolve the status file path upfront so we can write to it throughout
    status_path = job_path(runtime_root, tag, job_id)

    try:
        # ------------------------------------------------------------------ #
        # 1. Load the DataFrame and identify pending rows                      #
        # ------------------------------------------------------------------ #
        # Use _read_csv + ensure_label_columns instead of load_classified so
        # that rows with classifier_mode="" stay "" (load_classified goes
        # through normalize_reddit_frame which upgrades "" to "imported").
        df = ensure_label_columns(pd.read_csv(classified_path, dtype=str, keep_default_na=False))

        # skip_classification may be stored as the string "True"/"False" in CSV
        skip_col = df["skip_classification"].astype(str).str.lower()
        skip_flag = skip_col.isin(["true", "1", "yes"])

        # Pending = not skipped AND classifier_mode is still empty
        pending_mask = (
            ~skip_flag
            & (df["classifier_mode"].fillna("") == "")
        )
        pending_indices = df[pending_mask].index.tolist()

        # Respect optional row limit
        if limit > 0:
            pending_indices = pending_indices[:limit]

        total = len(pending_indices)

        # ------------------------------------------------------------------ #
        # 2. Write initial heartbeat so the API knows the job is alive         #
        # ------------------------------------------------------------------ #
        write_status(status_path, {"total": total, "heartbeat_at": time.time()})

        processed = 0
        errors = 0

        # ------------------------------------------------------------------ #
        # 3. Process rows in chunks                                            #
        # ------------------------------------------------------------------ #
        for chunk_start in range(0, total, CHUNK_SIZE):
            chunk = pending_indices[chunk_start : chunk_start + CHUNK_SIZE]

            for idx in chunk:
                row = df.loc[idx]
                try:
                    # Call the LLM — mocked in tests via patch("scripts.classify_job.classify_with_llm")
                    raw_label = classify_with_llm(str(row["combined_text"]), provider)

                    # Normalize the raw dict; multi_complaint_categories comes back as a list
                    label = complete_label(raw_label)

                    # Write each label key back into the DataFrame
                    for key, value in label.items():
                        # Convert list to comma-separated string for CSV storage
                        if key == "multi_complaint_categories" and isinstance(value, list):
                            value = ", ".join(value)
                        df.at[idx, key] = value

                    # Mark this row as LLM-classified
                    df.at[idx, "classifier_mode"] = "llm"
                    processed += 1

                except Exception:
                    # Per-row error: mark the row but keep going
                    df.at[idx, "classifier_mode"] = "error"
                    df.at[idx, "sentiment"] = "error"
                    errors += 1

            # After each chunk: persist the DataFrame and send a heartbeat
            save_classified(df, classified_path)
            write_status(
                status_path,
                {
                    "processed": processed,
                    "errors": errors,
                    "heartbeat_at": time.time(),
                },
            )

        # ------------------------------------------------------------------ #
        # 4. Write final completed status                                       #
        # ------------------------------------------------------------------ #
        write_status(
            status_path,
            {
                "state": "completed",
                "processed": processed,
                "total": total,
                "errors": errors,
                "completed_at": time.time(),
                "artifact_paths": [str(classified_path)],
            },
        )

    except Exception as exc:
        # Catastrophic failure (e.g., CSV unreadable, bad runtime_root)
        write_status(
            status_path,
            {
                "state": "failed",
                "error": str(exc),
                "completed_at": time.time(),
            },
        )
        sys.exit(1)


def main() -> None:
    """CLI entry point when the script is called as a subprocess."""
    parser = argparse.ArgumentParser(description="LLM classification worker")

    # Job identity
    parser.add_argument("--tag", required=True, help="Dataset tag")
    parser.add_argument("--job_id", required=True, help="Unique job identifier")
    parser.add_argument("--runtime_root", required=True, help="Root path for runtime artifacts")
    parser.add_argument("--classified_path", required=True, help="Path to the classified CSV")

    # Provider configuration
    parser.add_argument("--provider", default="openrouter", help="LLM provider name")
    parser.add_argument("--base_url", default="https://openrouter.ai/api/v1", help="Provider base URL")
    parser.add_argument("--model", default="gpt-oss-120b", help="Model identifier")
    parser.add_argument("--api_key_env", default="OPENROUTER_API_KEY", help="Env var holding the API key")

    # Optional row limit (0 = no limit)
    parser.add_argument("--limit", type=int, default=0, help="Max rows to classify (0 = all)")

    args = parser.parse_args()

    # Build the provider config; the actual key is resolved inside classify_with_llm
    provider = ProviderConfig(
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        api_key="",  # resolved from env inside classify_with_llm
    )

    run_classify_job(
        tag=args.tag,
        job_id=args.job_id,
        runtime_root=Path(args.runtime_root),
        classified_path=Path(args.classified_path),
        provider=provider,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
