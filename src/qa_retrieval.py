"""FAISS-backed Q&A retrieval for classified Reddit comment data.

Pipeline:
  build_qa_docs  → make_vectors → FAISS index → persist
  query time: embed query → faiss search → make_context → LLM answer

Artifacts stored under runtime/<tag>/qa/:
  docs.json          — list of serializable doc dicts
  index.faiss        — binary FAISS IndexFlatIP (L2-normalized cosine)
  metadata.json      — {model, dim, doc_count, created_at, tag}
"""

from __future__ import annotations

import json
import hashlib
import os
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

# Optional heavy dependency — guarded at call sites
try:
    import faiss as _faiss  # type: ignore[import]
    HAS_FAISS = True
except ImportError:
    _faiss = None
    HAS_FAISS = False

from src.gm_insights import ProviderConfig
from src.run_store import latest_output_root
from src.trend_insights import (
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    embed_texts,
    normalize_l2,
)

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def qa_dir(runtime_root: Path, tag: str) -> Path:
    return latest_output_root(runtime_root, tag) / "qa"


def classified_fingerprint(path: Path) -> str:
    """Return a stable SHA-256 for the exact classified input bytes."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def published_generation_identity(runtime_root: Path, tag: str) -> tuple[str, str, Path]:
    output = latest_output_root(runtime_root, tag)
    tag_root = Path(runtime_root) / tag
    generation_id = ""
    try:
        resolved = output.resolve()
        if resolved != tag_root.resolve() and resolved.parent.name == "generations":
            generation_id = resolved.name
    except OSError:
        pass
    run_id = ""
    for marker in (output / "qa" / ".run_id", output / "classified" / ".run_id"):
        if marker.exists():
            run_id = marker.read_text(encoding="utf-8").strip()
            if run_id:
                break
    return generation_id, run_id, output


def qa_status_payload(
    runtime_root: Path,
    tag: str,
    *,
    api_key_available: bool,
    job_status: dict[str, Any] | None = None,
    analysis_running: bool = False,
) -> dict[str, Any]:
    """Project artifacts, jobs, provenance, and credentials into one Q&A state."""
    generation_id, current_run_id, output = published_generation_identity(runtime_root, tag)
    classified = output / "classified" / "classified_posts.csv"
    current_fingerprint = classified_fingerprint(classified) if classified.exists() else ""
    base: dict[str, Any] = {
        "tag": tag,
        "generation_id": generation_id,
        "run_id": current_run_id,
        "current_input_fingerprint": current_fingerprint,
        "api_key_available": api_key_available,
    }

    if job_status and job_status.get("state") in {"pending", "running"}:
        return {
            **base,
            **job_status,
            "state": "building",
            "reason_code": "index_build_running",
            "detail": "The Q&A index is building for this analysis.",
            "recovery_action": None,
        }

    qdir = output / "qa"
    docs_path, index_path, metadata_path = qdir / "docs.json", qdir / "index.faiss", qdir / "metadata.json"
    metadata: dict[str, Any] = {}
    artifacts_exist = docs_path.exists() and index_path.exists() and metadata_path.exists()
    if artifacts_exist:
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                **base,
                "state": "failed",
                "reason_code": "invalid_metadata",
                "detail": f"Q&A metadata could not be read: {exc}",
                "recovery_action": "retry",
            }

        artifact_fingerprint = str(metadata.get("input_fingerprint", ""))
        identity = {
            "generation_id": str(metadata.get("generation_id") or generation_id),
            "run_id": str(metadata.get("run_id") or current_run_id),
            "input_fingerprint": artifact_fingerprint,
            "doc_count": metadata.get("doc_count", 0),
            "metadata": metadata,
        }
        if current_fingerprint and artifact_fingerprint == current_fingerprint:
            if not api_key_available:
                return {
                    **base,
                    **identity,
                    "state": "blocked_no_api_key",
                    "reason_code": "api_key_required",
                    "detail": "The current index is built, but Q&A needs an API key for query embeddings.",
                    "recovery_action": "configure_api_key",
                }
            return {
                **base,
                **identity,
                "state": "ready",
                "reason_code": None,
                "detail": "Q&A index matches the current classified input.",
                "recovery_action": None,
            }
        return {
            **base,
            **identity,
            "state": "stale",
            "reason_code": "input_fingerprint_mismatch",
            "detail": "The Q&A index belongs to an older classified input.",
            "recovery_action": "rebuild",
        }

    if job_status and job_status.get("state") in {"failed", "interrupted"}:
        return {
            **base,
            **job_status,
            "state": "failed",
            "reason_code": "index_build_failed",
            "detail": str(job_status.get("error") or "The previous Q&A index build failed."),
            "recovery_action": "retry",
        }
    if analysis_running:
        return {
            **base,
            "state": "building",
            "reason_code": "analysis_running",
            "detail": "Analysis is running; Q&A will become available after its index step publishes.",
            "recovery_action": None,
        }
    if not api_key_available:
        return {
            **base,
            "state": "blocked_no_api_key",
            "reason_code": "api_key_required",
            "detail": "Add an API key in Settings before building the Q&A index.",
            "recovery_action": "configure_api_key",
        }
    return {
        **base,
        "state": "missing",
        "reason_code": "index_missing" if current_fingerprint else "classified_input_missing",
        "detail": "No Q&A index exists for the current classified input." if current_fingerprint else "Classify data before building Q&A.",
        "recovery_action": "build" if current_fingerprint else None,
    }


# ---------------------------------------------------------------------------
# Doc construction
# ---------------------------------------------------------------------------

def build_qa_docs(df: pd.DataFrame) -> list[dict]:
    """Build rich Q&A documents from a classified DataFrame.

    Each doc bundles the post context plus classification signals so retrieval
    finds semantically relevant evidence, not just keyword matches.
    """
    docs: list[dict] = []
    for _, row in df.iterrows():
        # Build the text the embedding model will see for this doc
        parts: list[str] = []

        # Primary content: the actual comment/post text
        body = str(row.get("target_text", "")).strip()
        if body:
            parts.append(body[:600])

        # Classification signals enrich semantic search
        desc = str(row.get("description", "")).strip()
        if desc and desc not in ("", "nan", "None"):
            parts.append(desc[:300])

        category = str(row.get("top_complaint_category", "")).replace("_", " ").strip()
        if category and category not in ("not_applicable", "unknown", "nan", "None", ""):
            parts.append(f"category: {category}")

        vehicle = str(row.get("vehicle_mentioned", "")).replace("_", " ").strip()
        if vehicle and vehicle not in ("unknown", "nan", "None", ""):
            parts.append(f"vehicle: {vehicle}")

        sentiment = str(row.get("sentiment", "")).strip()
        if sentiment and sentiment not in ("skipped", "error", "nan", "None", ""):
            parts.append(f"sentiment: {sentiment}")

        embed_text = " | ".join(p for p in parts if p)
        if not embed_text:
            continue

        docs.append({
            "source_id": str(row.get("source_id", "")),
            "subreddit": str(row.get("subreddit_norm", "")),
            "vehicle": vehicle,
            "sentiment": sentiment,
            "category": category,
            "description": desc,
            "body": body[:600],
            "score": float(row.get("score_norm", 0) or 0),
            "permalink": str(row.get("permalink_norm", "")),
            "created_at": str(row.get("created_at_norm", "")),
            "embed_text": embed_text,
        })
    return docs


# ---------------------------------------------------------------------------
# Index build
# ---------------------------------------------------------------------------

def make_vectors(
    docs: list[dict],
    provider: ProviderConfig,
    model: str = EMBEDDING_MODEL,
) -> np.ndarray:
    """Embed docs using the `embed_text` field and return a float32 matrix."""
    texts = [d["embed_text"] for d in docs]
    return embed_texts(texts, provider, model=model)


def _save_index(index: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _faiss.write_index(index, str(path))


def _atomic_json(path: Path, obj: Any) -> None:
    """Write JSON atomically via a .tmp sibling file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def build_qa_index(
    tag: str,
    df: pd.DataFrame,
    provider: ProviderConfig,
    runtime_root: Path,
    embedding_model: str = EMBEDDING_MODEL,
    heartbeat_cb: Callable[[int, int], None] | None = None,
    input_fingerprint: str = "",
    generation_id: str = "",
    run_id: str = "",
) -> dict:
    """Full pipeline: classify → embed → FAISS → persist artifacts.

    Returns a result dict with doc_count, dim, and artifact_paths.
    Raises ValueError if no docs can be built (classify first).
    """
    if not HAS_FAISS:
        raise RuntimeError("faiss-cpu is required. pip install faiss-cpu")

    docs = build_qa_docs(df)
    if not docs:
        raise ValueError(
            "No embeddable rows found. Run classification first so each row has "
            "a description and complaint category before building the Q&A index."
        )

    n = len(docs)
    if heartbeat_cb:
        heartbeat_cb(0, n)

    # Embed all docs
    embeddings = make_vectors(docs, provider, model=embedding_model)

    if heartbeat_cb:
        heartbeat_cb(n, n)

    # Build FAISS index (L2-normalized → inner product = cosine)
    normed = normalize_l2(embeddings)
    dim = normed.shape[1]
    index = _faiss.IndexFlatIP(dim)
    index.add(normed)

    # Persist artifacts
    qdir = qa_dir(runtime_root, tag)
    docs_path = qdir / "docs.json"
    index_path = qdir / "index.faiss"
    meta_path = qdir / "metadata.json"

    _atomic_json(docs_path, docs)
    _save_index(index, index_path)
    _atomic_json(meta_path, {
        "model": embedding_model,
        "dim": dim,
        "doc_count": n,
        "created_at": time.time(),
        "tag": tag,
        "input_fingerprint": input_fingerprint,
        "generation_id": generation_id,
        "run_id": run_id,
    })

    return {
        "doc_count": n,
        "dim": dim,
        "artifact_paths": [str(docs_path), str(index_path), str(meta_path)],
    }


# ---------------------------------------------------------------------------
# Query-time retrieval
# ---------------------------------------------------------------------------

def retrieve(
    query: str,
    docs: list[dict],
    index: Any,
    provider: ProviderConfig,
    model: str = EMBEDDING_MODEL,
    k: int = 8,
) -> list[dict]:
    """Embed `query`, search FAISS, return top-k doc dicts with a `score` field.

    The returned list is sorted by similarity (highest first).
    `k` is clamped to the number of available docs.
    """
    if not HAS_FAISS:
        raise RuntimeError("faiss-cpu is required. pip install faiss-cpu")
    if not docs or index is None:
        return []

    k_clamped = min(k, len(docs))
    q_vec = embed_texts([query], provider, model=model)
    q_normed = normalize_l2(q_vec)
    scores, positions = index.search(q_normed, k_clamped)

    hits: list[dict] = []
    for score, pos in zip(scores[0], positions[0]):
        p = int(pos)
        if p < 0 or p >= len(docs):
            continue
        hits.append({**docs[p], "retrieval_score": float(score)})
    return hits


def make_context(hits: list[dict], max_chars: int = 3000) -> str:
    """Format retrieved hits into a concise LLM context block.

    Each hit shows: body text, vehicle, sentiment, category, subreddit, permalink.
    Total context is capped at `max_chars` to stay within LLM prompt budgets.
    """
    lines: list[str] = []
    budget = max_chars

    for i, hit in enumerate(hits, 1):
        vehicle = hit.get("vehicle") or "unknown"
        sentiment = hit.get("sentiment") or "unknown"
        category = hit.get("category") or ""
        subreddit = hit.get("subreddit") or ""
        body = (hit.get("body") or "").strip()
        permalink = hit.get("permalink") or ""
        score = hit.get("retrieval_score", 0)

        meta = f"[{i}] Vehicle={vehicle} | Sentiment={sentiment}"
        if category:
            meta += f" | Category={category}"
        if subreddit:
            meta += f" | r/{subreddit}"
        if permalink:
            meta += f" | {permalink}"
        meta += f" | similarity={score:.3f}"

        snippet = f"{meta}\n{body}"
        if len(snippet) + 2 > budget:
            snippet = snippet[: budget - 5] + "..."
            lines.append(snippet)
            break
        lines.append(snippet)
        budget -= len(snippet) + 2

    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# LLM answer generation
# ---------------------------------------------------------------------------

_QA_SYSTEM = (
    "You are a product intelligence analyst for General Motors. "
    "Answer the user's question using ONLY the Reddit evidence provided. "
    "Cite evidence numbers like [1], [2]. "
    "If the evidence doesn't answer the question, say so clearly."
)


def answer_question(
    question: str,
    hits: list[dict],
    provider: ProviderConfig,
) -> str:
    """Generate an LLM answer grounded in retrieved evidence."""
    if not hits:
        return "No relevant evidence found. Try rebuilding the index or rephrasing your question."

    from openai import OpenAI

    api_key = provider.api_key or os.getenv(provider.api_key_env, "")
    if not api_key:
        raise RuntimeError(
            f"API key required. Set {provider.api_key_env} or provide api_key."
        )

    context = make_context(hits)
    prompt = f"Evidence from Reddit comments:\n\n{context}\n\nQuestion: {question}"

    client = OpenAI(api_key=api_key, base_url=provider.base_url)
    try:
        response = client.chat.completions.create(
            model=provider.model,
            messages=[
                {"role": "system", "content": _QA_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            max_tokens=512,
            temperature=0.2,
        )
        return response.choices[0].message.content or ""
    except Exception as exc:
        return f"[LLM error — showing retrieval results only]\n\n{make_context(hits)}\n\nError: {exc}"


# ---------------------------------------------------------------------------
# Artifact loaders (used by API endpoints)
# ---------------------------------------------------------------------------

def load_qa_artifacts(
    runtime_root: Path, tag: str
) -> tuple[list[dict], Any, dict]:
    """Load docs, FAISS index, and metadata from disk.

    Returns (docs, index, metadata). Raises FileNotFoundError if not built yet.
    """
    if not HAS_FAISS:
        raise RuntimeError("faiss-cpu is required. pip install faiss-cpu")
    qdir = qa_dir(runtime_root, tag)
    docs_path = qdir / "docs.json"
    index_path = qdir / "index.faiss"
    meta_path = qdir / "metadata.json"

    if not docs_path.exists() or not index_path.exists():
        raise FileNotFoundError(
            f"Q&A index not found for tag '{tag}'. "
            "Run POST /api/qa/build-index first."
        )

    docs = json.loads(docs_path.read_text(encoding="utf-8"))
    index = _faiss.read_index(str(index_path))
    metadata = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return docs, index, metadata
