# scripts/trend_job.py
"""Subprocess job: KMeans + FAISS clustering with LLM cluster labeling (Phase 4)."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.gm_insights import ProviderConfig, load_classified
from src.jobs import job_path, write_status
from src.trend_insights import run_clustering


def run_trend_job(
    tag: str,
    job_id: str,
    runtime_root: Path,
    classified_path: Path,
    provider: ProviderConfig,
    n_clusters: int = 10,
    embedding_model: str = "text-embedding-3-small",
) -> None:
    """Load classified data, run the clustering pipeline, and write job status."""
    status_path = job_path(runtime_root, tag, job_id)

    def heartbeat(processed: int = 0, total: int = 0) -> None:
        write_status(status_path, {
            "heartbeat_at": time.time(),
            "processed": processed,
            "total": total,
        })

    try:
        # Initial heartbeat so the API sees the job is alive
        heartbeat()

        df = load_classified(classified_path)
        result = run_clustering(
            tag=tag,
            df=df,
            provider=provider,
            runtime_root=runtime_root,
            n_clusters=n_clusters,
            embedding_model=embedding_model,
            heartbeat_cb=heartbeat,
        )

        write_status(status_path, {
            "state": "completed",
            "completed_at": time.time(),
            "n_clusters": result["n_clusters"],
            "n_docs": result["n_docs"],
            "artifact_paths": result["artifact_paths"],
        })

    except Exception as exc:
        write_status(status_path, {
            "state": "failed",
            "error": str(exc),
            "completed_at": time.time(),
        })
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 4 clustering worker")

    # Standard job identity args
    parser.add_argument("--tag", required=True)
    parser.add_argument("--job_id", required=True)
    parser.add_argument("--runtime_root", required=True)

    # Data
    parser.add_argument("--classified_path", required=True)

    # Clustering
    parser.add_argument("--n_clusters", type=int, default=10)
    parser.add_argument("--embedding_model", default="text-embedding-3-small")

    # LLM provider (for cluster labeling and embeddings)
    parser.add_argument("--provider", default="openrouter")
    parser.add_argument("--base_url", default="https://openrouter.ai/api/v1")
    parser.add_argument("--model", default="gpt-oss-120b")
    parser.add_argument("--api_key_env", default="OPENROUTER_API_KEY")

    args = parser.parse_args()

    provider = ProviderConfig(
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        api_key="",  # resolved from env inside embed_texts / label_cluster_with_llm
    )

    run_trend_job(
        tag=args.tag,
        job_id=args.job_id,
        runtime_root=Path(args.runtime_root),
        classified_path=Path(args.classified_path),
        provider=provider,
        n_clusters=args.n_clusters,
        embedding_model=args.embedding_model,
    )


if __name__ == "__main__":
    main()
