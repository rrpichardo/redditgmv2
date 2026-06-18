"""KMeans clustering with FAISS centroid-nearest representative retrieval.

Pipeline: embed → KMeans → FAISS → label (LLM) → deterministic confidence

Artifacts saved under runtime/<tag>/trends/:
  clusters.json          — {cluster_id: [source_ids]}
  cluster_examples.json  — {cluster_id: context dict with rep examples}
  cluster_labels.json    — {cluster_id: structured label from LLM + confidence}
  faiss.index            — binary FAISS index (L2-normalized inner product)
  embedding_metadata.json — model, dim, doc count, created_at
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

# Optional heavy dependencies — guarded at call sites with clear error messages
try:
    import faiss as _faiss  # type: ignore[import]
    HAS_FAISS = True
except ImportError:
    _faiss = None
    HAS_FAISS = False

try:
    from sklearn.cluster import KMeans as _KMeans  # type: ignore[import]
    HAS_SKLEARN = True
except ImportError:
    _KMeans = None
    HAS_SKLEARN = False

from src.gm_insights import ProviderConfig, analyzed_frame

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536

VALID_THEME_TYPES = frozenset({"complaint", "delight", "mixed", "comparison", "question", "other"})
VALID_CONFIDENCE = frozenset({"high", "medium", "low"})
VALID_LABEL_RISK = frozenset({"low", "medium", "high"})

CLUSTER_LABEL_SYSTEM = (
    "You analyze clusters of Reddit comments about General Motors vehicles for product and strategy teams. "
    "Return valid JSON only — no markdown fences, no text outside the JSON object."
)

CLUSTER_LABEL_PROMPT = """\
Analyze this cluster of {cluster_size} Reddit comments about General Motors vehicles.

Cluster metadata:
- Sentiment mix: {sentiment_mix}
- Severity mix: {severity_mix}
- Top vehicles mentioned: {top_vehicles}
- Top complaint categories: {top_categories}

Most characteristic comments (centroid-nearest — these define the cluster):
{centroid_reps}

Highest-engagement comments:
{engagement_reps}

Most recent comments:
{recent_reps}

Respond with a JSON object:
{{
  "short_label": "<3-6 word theme>",
  "detailed_label": "<1-2 sentence description of this cluster>",
  "theme_type": "<complaint | delight | mixed | comparison | question | other>",
  "confidence": "<high | medium | low>",
  "confidence_score": <0.0 to 1.0>,
  "rationale": "<1-2 sentences: why this label fits these examples>",
  "label_risk": "<low | medium | high — high means examples are heterogeneous or the theme is ambiguous>"
}}"""


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def trends_dir(runtime_root: Path, tag: str) -> Path:
    return runtime_root / tag / "trends"


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------

def _text_for_embedding(row: pd.Series) -> str:
    """Build a short, classification-rich string to embed per row.

    Using the post-classification description + category + vehicle + sentiment
    gives embeddings that cluster on thematic content rather than raw text noise.
    """
    SKIP_VALUES = frozenset({"not_applicable", "unknown", "none", ""})
    parts = [
        str(row.get("description", "")).strip()[:400],
        str(row.get("top_complaint_category", "")).replace("_", " "),
        str(row.get("vehicle_mentioned", "")).replace("_", " "),
        str(row.get("sentiment", "")),
    ]
    return " | ".join(p for p in parts if p and p not in SKIP_VALUES)


def _embed_batch(texts: list[str], client: Any, model: str) -> np.ndarray:
    response = client.embeddings.create(model=model, input=texts)
    return np.array([e.embedding for e in response.data], dtype=np.float32)


def embed_texts(
    texts: list[str],
    provider: ProviderConfig,
    batch_size: int = 100,
    model: str = EMBEDDING_MODEL,
) -> np.ndarray:
    """Embed all texts in batches via an OpenAI-compatible embeddings endpoint."""
    from openai import OpenAI

    api_key = provider.api_key or os.getenv(provider.api_key_env, "")
    if not api_key:
        raise RuntimeError(
            f"API key required for embeddings. Set {provider.api_key_env} or provide api_key."
        )
    client = OpenAI(api_key=api_key, base_url=provider.base_url)

    batches: list[np.ndarray] = []
    for i in range(0, len(texts), batch_size):
        batches.append(_embed_batch(texts[i : i + batch_size], client, model))

    return np.vstack(batches) if batches else np.empty((0, EMBEDDING_DIM), dtype=np.float32)


# ---------------------------------------------------------------------------
# Vector math
# ---------------------------------------------------------------------------

def normalize_l2(vecs: np.ndarray) -> np.ndarray:
    """L2-normalize each row. Zero vectors are left as zero."""
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return (vecs / norms).astype(np.float32)


# ---------------------------------------------------------------------------
# FAISS index
# ---------------------------------------------------------------------------

def build_faiss_index(embeddings: np.ndarray) -> Any:
    """Build a FAISS IndexFlatIP over L2-normalized embeddings (cosine similarity)."""
    if not HAS_FAISS:
        raise RuntimeError("faiss-cpu is required for clustering. pip install faiss-cpu")
    normed = normalize_l2(embeddings.astype(np.float32))
    index = _faiss.IndexFlatIP(normed.shape[1])
    index.add(normed)
    return index


def _save_faiss_index(index: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _faiss.write_index(index, str(path))


# ---------------------------------------------------------------------------
# KMeans
# ---------------------------------------------------------------------------

def kmeans_cluster(embeddings: np.ndarray, n_clusters: int, seed: int = 42) -> np.ndarray:
    """Run KMeans and return a cluster ID for each embedding."""
    if not HAS_SKLEARN:
        raise RuntimeError("scikit-learn is required for clustering. pip install scikit-learn")
    actual_k = max(2, min(n_clusters, len(embeddings)))
    km = _KMeans(n_clusters=actual_k, random_state=seed, n_init="auto")
    return km.fit_predict(embeddings).astype(int)


# ---------------------------------------------------------------------------
# FAISS centroid-nearest representatives
# ---------------------------------------------------------------------------

def faiss_centroid_representatives(
    member_positions: list[int],
    all_embeddings: np.ndarray,
    faiss_index: Any,
    k: int = 10,
) -> list[int]:
    """Return the k positions (into all_embeddings) nearest to the cluster centroid.

    Searches the FAISS index, then filters results to only same-cluster members.
    member_positions are positions into all_embeddings (not DataFrame indices).
    """
    if not member_positions:
        return []

    member_vecs = all_embeddings[member_positions].astype(np.float32)
    centroid = member_vecs.mean(axis=0, keepdims=True)
    centroid_normed = normalize_l2(centroid)

    k_search = min(k * 3, faiss_index.ntotal)
    _, raw_positions = faiss_index.search(centroid_normed, k_search)

    member_set = set(member_positions)
    reps: list[int] = []
    for pos in raw_positions[0]:
        p = int(pos)
        if p in member_set and p not in reps:
            reps.append(p)
        if len(reps) >= k:
            break
    return reps


# ---------------------------------------------------------------------------
# Cluster coherence
# ---------------------------------------------------------------------------

def cluster_coherence(member_positions: list[int], embeddings: np.ndarray) -> float:
    """Average cosine similarity of cluster members to their centroid (0–1).

    Higher values mean a tighter, more coherent cluster.
    """
    if len(member_positions) < 2:
        return 1.0
    vecs = normalize_l2(embeddings[member_positions].astype(np.float32))
    centroid = vecs.mean(axis=0)
    centroid_norm = centroid / (float(np.linalg.norm(centroid)) + 1e-9)
    sims = vecs @ centroid_norm
    return float(np.clip(sims.mean(), 0.0, 1.0))


# ---------------------------------------------------------------------------
# Cluster context builder
# ---------------------------------------------------------------------------

def _format_examples(df_indices: list[int], df: pd.DataFrame, max_chars: int = 280) -> str:
    lines = []
    for i, idx in enumerate(df_indices, 1):
        row = df.loc[idx]
        text = str(row.get("target_text", "")).strip()[:max_chars]
        vehicle = str(row.get("vehicle_mentioned", "unknown"))
        cat = str(row.get("top_complaint_category", "?")).replace("_", " ")
        score = row.get("score_norm", "?")
        lines.append(f"{i}. [{vehicle} | {cat} | score={score}] {text}")
    return "\n".join(lines) or "(no examples)"


def build_cluster_context(
    cluster_id: int,
    df: pd.DataFrame,
    member_df_indices: list[int],
    centroid_rep_df_indices: list[int],
) -> dict[str, Any]:
    """Assemble context dict for LLM labeling and artifact storage.

    Includes 3 sets of representative examples:
      - centroid_reps: most characteristic (FAISS nearest to centroid, up to 7)
      - engagement_reps: highest Reddit score (up to 4)
      - recent_reps: most recent by timestamp (up to 4)
    """
    members = df.loc[member_df_indices]

    sentiment_mix = members["sentiment"].value_counts().to_dict() if "sentiment" in members else {}
    severity_mix = (
        members["issue_severity"].value_counts().to_dict() if "issue_severity" in members else {}
    )
    top_vehicles = (
        members["vehicle_mentioned"].value_counts().head(3).index.tolist()
        if "vehicle_mentioned" in members
        else []
    )

    # Top categories from complaint rows only, fall back to all rows
    if "complaint" in members.columns and "top_complaint_category" in members.columns:
        complaint_rows = members[members["complaint"].astype(int) == 1]
        top_categories = (
            complaint_rows["top_complaint_category"].value_counts().head(3).index.tolist()
            if not complaint_rows.empty
            else members["top_complaint_category"].value_counts().head(3).index.tolist()
        )
        # Category dominance = top category share of all complaint members
        if not complaint_rows.empty:
            top_cat_count = int(complaint_rows["top_complaint_category"].value_counts().iloc[0])
            category_dominance = top_cat_count / len(complaint_rows)
        else:
            category_dominance = 0.0
    else:
        top_categories = []
        category_dominance = 0.0

    # High-engagement examples
    n_eng = min(4, len(member_df_indices))
    if "score_norm" in members.columns:
        eng_indices = members.nlargest(n_eng, "score_norm").index.tolist()
    else:
        eng_indices = member_df_indices[:n_eng]

    # Recent examples
    n_rec = min(4, len(member_df_indices))
    recent_indices: list[int] = []
    if "created_at_norm" in members.columns:
        dated = members[pd.to_datetime(members["created_at_norm"], errors="coerce").notna()]
        if not dated.empty:
            recent_indices = (
                dated.assign(_dt=pd.to_datetime(dated["created_at_norm"], errors="coerce"))
                .sort_values("_dt", ascending=False)
                .head(n_rec)
                .index.tolist()
            )
    if not recent_indices:
        recent_indices = member_df_indices[:n_rec]

    centroid_indices = centroid_rep_df_indices[:7]

    return {
        "cluster_id": cluster_id,
        "cluster_size": len(member_df_indices),
        "sentiment_mix": sentiment_mix,
        "severity_mix": severity_mix,
        "top_vehicles": top_vehicles,
        "top_categories": top_categories,
        "category_dominance": round(category_dominance, 3),
        "centroid_rep_indices": centroid_indices,
        "engagement_rep_indices": eng_indices,
        "recent_rep_indices": recent_indices,
        # Pre-formatted text for the LLM prompt
        "centroid_reps_text": _format_examples(centroid_indices, df),
        "engagement_reps_text": _format_examples(eng_indices, df),
        "recent_reps_text": _format_examples(recent_indices, df),
    }


# ---------------------------------------------------------------------------
# LLM cluster labeling
# ---------------------------------------------------------------------------

def label_cluster_with_llm(context: dict[str, Any], provider: ProviderConfig) -> dict[str, Any]:
    """Call the LLM to generate a structured cluster label."""
    from openai import OpenAI

    api_key = provider.api_key or os.getenv(provider.api_key_env, "")
    client = OpenAI(api_key=api_key, base_url=provider.base_url)

    prompt = CLUSTER_LABEL_PROMPT.format(
        cluster_size=context["cluster_size"],
        sentiment_mix=context["sentiment_mix"],
        severity_mix=context["severity_mix"],
        top_vehicles=context["top_vehicles"],
        top_categories=context["top_categories"],
        centroid_reps=context["centroid_reps_text"],
        engagement_reps=context["engagement_reps_text"],
        recent_reps=context["recent_reps_text"],
    )

    try:
        response = client.chat.completions.create(
            model=provider.model,
            messages=[
                {"role": "system", "content": CLUSTER_LABEL_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        parsed = json.loads(response.choices[0].message.content or "{}")
    except Exception as exc:
        # Graceful fallback: label survives LLM errors
        return {
            "short_label": f"Cluster {context['cluster_id']}",
            "detailed_label": "LLM labeling failed.",
            "theme_type": "other",
            "confidence": "low",
            "confidence_score": 0.1,
            "rationale": str(exc)[:200],
            "label_risk": "high",
        }

    return {
        "short_label": str(parsed.get("short_label", f"Cluster {context['cluster_id']}"))[:80],
        "detailed_label": str(parsed.get("detailed_label", ""))[:400],
        "theme_type": (
            parsed.get("theme_type", "other")
            if parsed.get("theme_type") in VALID_THEME_TYPES
            else "other"
        ),
        "confidence": (
            parsed.get("confidence", "medium")
            if parsed.get("confidence") in VALID_CONFIDENCE
            else "medium"
        ),
        "confidence_score": float(parsed.get("confidence_score", 0.5)),
        "rationale": str(parsed.get("rationale", ""))[:400],
        "label_risk": (
            parsed.get("label_risk", "medium")
            if parsed.get("label_risk") in VALID_LABEL_RISK
            else "medium"
        ),
    }


# ---------------------------------------------------------------------------
# Deterministic confidence
# ---------------------------------------------------------------------------

def _deterministic_confidence(
    label: dict[str, Any],
    context: dict[str, Any],
    coherence: float,
) -> dict[str, Any]:
    """Downgrade overconfident LLM labels using objective quality signals.

    Score = weighted average of:
      - cluster_coherence (avg cosine sim to centroid)
      - category_dominance (top category share of complaint members)
      - size_score (penalizes very small clusters, caps at ≥20 members)

    Confidence cap rules:
      - cluster_size < 5 → always "low"
      - det_score < 0.35 → "low"
      - det_score < 0.55 and model said "high" → downgrade to "medium"
    """
    cluster_size = context["cluster_size"]
    category_dominance = float(context.get("category_dominance", 0.0))

    size_score = min(1.0, max(0.0, (cluster_size - 5) / 15.0))
    det_score = 0.4 * float(coherence) + 0.3 * category_dominance + 0.3 * size_score

    model_conf = str(label.get("confidence", "medium")).lower()
    adj_conf = model_conf

    if cluster_size < 5:
        adj_conf = "low"
    elif det_score < 0.35:
        adj_conf = "low"
    elif det_score < 0.55 and model_conf == "high":
        adj_conf = "medium"

    return {
        **label,
        "confidence": adj_conf,
        "confidence_score": round(min(float(label.get("confidence_score", 0.5)), det_score + 0.1), 3),
        "deterministic_score": round(det_score, 3),
        "coherence": round(float(coherence), 3),
        "category_dominance": round(category_dominance, 3),
    }


# ---------------------------------------------------------------------------
# Atomic JSON helper
# ---------------------------------------------------------------------------

def _atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, default=str), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Main public entry point
# ---------------------------------------------------------------------------

def run_clustering(
    tag: str,
    df: pd.DataFrame,
    provider: ProviderConfig,
    runtime_root: Path,
    n_clusters: int = 10,
    embedding_model: str = EMBEDDING_MODEL,
    heartbeat_cb: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Run the full Phase 4 clustering pipeline and save all artifacts.

    Steps:
      1. Filter df to analyzed (classified) rows
      2. Build embedding text per row
      3. Embed via OpenAI-compatible API (batched)
      4. Build FAISS index (L2-normalized inner product = cosine)
      5. Run KMeans to assign cluster IDs
      6. Per cluster: find FAISS centroid reps, build context, label with LLM,
         adjust confidence deterministically
      7. Save five artifacts under runtime/<tag>/trends/

    heartbeat_cb(processed, total) is called after embedding and after each
    cluster label to allow the caller (trend_job.py) to write status updates.

    Returns a summary dict with artifact paths and the final cluster_labels.
    """
    if not HAS_FAISS:
        raise RuntimeError("faiss-cpu is required. pip install faiss-cpu")
    if not HAS_SKLEARN:
        raise RuntimeError("scikit-learn is required. pip install scikit-learn")

    analyzed = analyzed_frame(df)
    if analyzed.empty:
        raise ValueError("No analyzed rows found. Classify the data before running clustering.")

    all_df_indices = analyzed.index.tolist()
    # pos → df_index mapping (and reverse) used throughout
    idx_to_pos = {df_idx: pos for pos, df_idx in enumerate(all_df_indices)}

    # Step 1: Build embedding texts
    texts = [_text_for_embedding(analyzed.loc[idx]) for idx in all_df_indices]

    if heartbeat_cb:
        heartbeat_cb(0, len(texts))

    # Step 2: Embed
    embeddings = embed_texts(texts, provider, model=embedding_model)

    if heartbeat_cb:
        heartbeat_cb(len(texts), len(texts))

    # Step 3: FAISS index
    faiss_index = build_faiss_index(embeddings)

    # Step 4: KMeans
    actual_k = max(2, min(n_clusters, len(all_df_indices) // 2, len(all_df_indices)))
    cluster_assignments = kmeans_cluster(embeddings, actual_k)

    # Build cluster_id → list of df indices
    clusters: dict[int, list[int]] = {}
    for pos, cid in enumerate(cluster_assignments):
        clusters.setdefault(int(cid), []).append(all_df_indices[pos])

    # Step 5: Per-cluster processing
    clusters_out: dict[str, list[str]] = {}
    cluster_examples: dict[str, Any] = {}
    cluster_labels: dict[str, Any] = {}

    for cluster_id, member_df_indices in sorted(clusters.items()):
        cid_str = str(cluster_id)

        # Map df indices → embedding positions for FAISS
        member_positions = [idx_to_pos[df_idx] for df_idx in member_df_indices]

        # FAISS centroid-nearest representatives (positions → df indices)
        rep_positions = faiss_centroid_representatives(
            member_positions, embeddings, faiss_index, k=10
        )
        rep_df_indices = [all_df_indices[p] for p in rep_positions]

        # Build rich context for LLM
        ctx = build_cluster_context(cluster_id, analyzed, member_df_indices, rep_df_indices)

        # Cluster coherence for deterministic confidence
        coh = cluster_coherence(member_positions, embeddings)

        # LLM label
        raw_label = label_cluster_with_llm(ctx, provider)

        # Adjust overconfident model labels
        final_label = _deterministic_confidence(raw_label, ctx, coh)
        final_label["cluster_id"] = cluster_id

        # Source IDs for clusters.json
        source_ids = (
            analyzed.loc[member_df_indices, "source_id"].tolist()
            if "source_id" in analyzed.columns
            else [str(i) for i in member_df_indices]
        )
        clusters_out[cid_str] = source_ids
        cluster_examples[cid_str] = ctx
        cluster_labels[cid_str] = final_label

        if heartbeat_cb:
            heartbeat_cb(cluster_id + 1, actual_k)

    # Step 6: Save artifacts atomically
    tdir = trends_dir(runtime_root, tag)

    _atomic_json(tdir / "clusters.json", clusters_out)
    _atomic_json(tdir / "cluster_examples.json", cluster_examples)
    _atomic_json(tdir / "cluster_labels.json", cluster_labels)
    _save_faiss_index(faiss_index, tdir / "faiss.index")
    _atomic_json(tdir / "embedding_metadata.json", {
        "model": embedding_model,
        "dim": int(embeddings.shape[1]),
        "doc_count": len(all_df_indices),
        "n_clusters": actual_k,
        "created_at": time.time(),
        "tag": tag,
    })

    artifact_paths = [
        str(tdir / "clusters.json"),
        str(tdir / "cluster_examples.json"),
        str(tdir / "cluster_labels.json"),
        str(tdir / "faiss.index"),
        str(tdir / "embedding_metadata.json"),
    ]

    return {
        "n_clusters": actual_k,
        "n_docs": len(all_df_indices),
        "artifact_dir": str(tdir),
        "artifact_paths": artifact_paths,
        "cluster_labels": cluster_labels,
    }
