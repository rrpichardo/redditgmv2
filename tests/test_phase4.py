"""Phase 4 tests: KMeans clustering with FAISS centroid-nearest representatives.

Covers:
- normalize_l2
- build_faiss_index / faiss_centroid_representatives
- kmeans_cluster
- cluster_coherence
- build_cluster_context
- _deterministic_confidence
- label_cluster_with_llm (LLM mocked)
- run_clustering (embeddings + LLM both mocked)
- POST /api/trends/run
- GET /api/trends/status
- GET /api/trends
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# Skip the entire module if heavy dependencies are absent so CI doesn't hard-fail
# before they're installed.
faiss = pytest.importorskip("faiss")
pytest.importorskip("sklearn")

from src.trend_insights import (
    EMBEDDING_DIM,
    _deterministic_confidence,
    _text_for_embedding,
    build_cluster_context,
    build_faiss_index,
    cluster_coherence,
    faiss_centroid_representatives,
    kmeans_cluster,
    label_cluster_with_llm,
    normalize_l2,
    run_clustering,
    trends_dir,
)
from src.gm_insights import ProviderConfig

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_FAKE_PROVIDER = ProviderConfig(
    provider="openrouter",
    model="test-model",
    base_url="https://example.com",
    api_key_env="TEST_KEY",
    api_key="fake-key",
)

_N_ROWS = 20
_N_CLUSTERS = 3


def _make_analyzed_df(n: int = _N_ROWS) -> pd.DataFrame:
    """Build a minimal analyzed DataFrame with all required columns."""
    rows = []
    sentiments = ["positive", "negative", "neutral"]
    severities = ["critical", "major", "minor", "none"]
    categories = ["transmission_torque_converter", "electrical_battery_alternator", "ac_hvac"]
    vehicles = ["silverado", "tahoe", "equinox"]
    for i in range(n):
        rows.append({
            "source_id": f"row_{i}",
            "source_type": "comment",
            "post_id_norm": f"post_{i % 5}",
            "subreddit_norm": "Silverado",
            "title_norm": f"Title {i}",
            "target_text": f"This comment is about a vehicle issue number {i} and is long enough to pass junk check",
            "combined_text": f"context {i}",
            "score_norm": float(i * 2),
            "created_at_norm": f"2024-0{(i % 9) + 1}-01",
            "permalink_norm": f"/r/sub/comments/{i}",
            "skip_classification": False,
            "classifier_mode": "heuristic_preview",
            "sentiment": sentiments[i % 3],
            "complaint": int(i % 3 == 1),
            "competitor_mention": 0,
            "dealer_experience": 0,
            "reliability_concern": 0,
            "software_tech_issue": 0,
            "purchase_intent": 0,
            "loyalty_signal": 0,
            "ev_topic": 0,
            "enthusiast_mod": 0,
            "classic_vintage": 0,
            "top_complaint_category": categories[i % 3] if i % 3 == 1 else "not_applicable",
            "multi_complaint_categories": "",
            "vehicle_mentioned": vehicles[i % 3],
            "comment_type": "shared_experience",
            "competitor_brand": "none",
            "issue_severity": severities[i % 4],
            "description": f"A test comment insight number {i} about {vehicles[i % 3]}.",
            "engagement_level": "medium",
        })
    return pd.DataFrame(rows)


def _fake_embeddings(n: int, dim: int = EMBEDDING_DIM) -> np.ndarray:
    rng = np.random.default_rng(42)
    return rng.standard_normal((n, dim)).astype(np.float32)


def _fake_label(cluster_id: int = 0) -> dict:
    return {
        "short_label": f"Test Theme {cluster_id}",
        "detailed_label": "A test cluster about vehicle complaints.",
        "theme_type": "complaint",
        "confidence": "high",
        "confidence_score": 0.9,
        "rationale": "Strong thematic coherence.",
        "label_risk": "low",
    }


# ---------------------------------------------------------------------------
# normalize_l2
# ---------------------------------------------------------------------------

class TestNormalizeL2:
    def test_unit_vectors(self):
        vecs = np.array([[3.0, 4.0], [0.0, 2.0]], dtype=np.float32)
        normed = normalize_l2(vecs)
        norms = np.linalg.norm(normed, axis=1)
        np.testing.assert_allclose(norms, [1.0, 1.0], atol=1e-6)

    def test_zero_vector_stays_zero(self):
        vecs = np.array([[0.0, 0.0]], dtype=np.float32)
        normed = normalize_l2(vecs)
        # Zero vector should not produce NaN
        assert not np.isnan(normed).any()

    def test_output_dtype(self):
        vecs = np.ones((5, 4), dtype=np.float64)
        normed = normalize_l2(vecs)
        assert normed.dtype == np.float32


# ---------------------------------------------------------------------------
# build_faiss_index
# ---------------------------------------------------------------------------

class TestBuildFaissIndex:
    def test_index_size(self):
        vecs = _fake_embeddings(10, dim=8)
        index = build_faiss_index(vecs)
        assert index.ntotal == 10

    def test_search_returns_nearest(self):
        # One clear cluster around [1, 0, ...] and another around [-1, 0, ...]
        dim = 16
        pos_vecs = np.ones((5, dim), dtype=np.float32)
        neg_vecs = -np.ones((5, dim), dtype=np.float32)
        vecs = np.vstack([pos_vecs, neg_vecs])
        index = build_faiss_index(vecs)
        query = normalize_l2(np.ones((1, dim), dtype=np.float32))
        _, positions = index.search(query, 5)
        # All top-5 results should be from the positive cluster (positions 0-4)
        assert all(p < 5 for p in positions[0])


# ---------------------------------------------------------------------------
# kmeans_cluster
# ---------------------------------------------------------------------------

class TestKmeansCluster:
    def test_output_shape(self):
        vecs = _fake_embeddings(15, dim=8)
        labels = kmeans_cluster(vecs, n_clusters=3)
        assert labels.shape == (15,)

    def test_output_dtype_int(self):
        vecs = _fake_embeddings(10, dim=8)
        labels = kmeans_cluster(vecs, n_clusters=2)
        assert labels.dtype == int or np.issubdtype(labels.dtype, np.integer)

    def test_n_clusters_capped_at_n_rows(self):
        # Requesting more clusters than rows should not raise
        vecs = _fake_embeddings(4, dim=8)
        labels = kmeans_cluster(vecs, n_clusters=100)
        # Should have at most n_rows distinct labels
        assert len(set(labels.tolist())) <= 4

    def test_deterministic_with_seed(self):
        vecs = _fake_embeddings(20, dim=8)
        labels_a = kmeans_cluster(vecs, n_clusters=3, seed=0)
        labels_b = kmeans_cluster(vecs, n_clusters=3, seed=0)
        np.testing.assert_array_equal(labels_a, labels_b)


# ---------------------------------------------------------------------------
# faiss_centroid_representatives
# ---------------------------------------------------------------------------

class TestFaissCentroidRepresentatives:
    def test_returns_only_cluster_members(self):
        # Build two clear clusters; ask for reps from cluster 0 only
        dim = 16
        cluster0 = np.ones((5, dim), dtype=np.float32)
        cluster1 = -np.ones((5, dim), dtype=np.float32)
        vecs = np.vstack([cluster0, cluster1])
        index = build_faiss_index(vecs)

        member_positions = [0, 1, 2, 3, 4]  # cluster 0
        reps = faiss_centroid_representatives(member_positions, vecs, index, k=5)
        assert all(r in member_positions for r in reps)

    def test_empty_members_returns_empty(self):
        vecs = _fake_embeddings(5, dim=8)
        index = build_faiss_index(vecs)
        assert faiss_centroid_representatives([], vecs, index, k=3) == []

    def test_k_limit_respected(self):
        vecs = _fake_embeddings(20, dim=8)
        index = build_faiss_index(vecs)
        positions = list(range(10))
        reps = faiss_centroid_representatives(positions, vecs, index, k=4)
        assert len(reps) <= 4


# ---------------------------------------------------------------------------
# cluster_coherence
# ---------------------------------------------------------------------------

class TestClusterCoherence:
    def test_tight_cluster_high_coherence(self):
        # Identical vectors → coherence should be 1.0
        vecs = np.ones((5, 8), dtype=np.float32)
        coh = cluster_coherence([0, 1, 2, 3, 4], vecs)
        assert coh > 0.99

    def test_opposite_vectors_low_coherence(self):
        # Two opposite vectors → centroid near zero → low coherence
        vecs = np.array([[1.0] * 8, [-1.0] * 8], dtype=np.float32)
        coh = cluster_coherence([0, 1], vecs)
        assert coh < 0.5

    def test_single_member(self):
        vecs = _fake_embeddings(3, dim=8)
        coh = cluster_coherence([0], vecs)
        assert coh == 1.0


# ---------------------------------------------------------------------------
# build_cluster_context
# ---------------------------------------------------------------------------

class TestBuildClusterContext:
    def test_required_keys_present(self):
        df = _make_analyzed_df(10)
        member_indices = df.index.tolist()[:8]
        centroid_reps = member_indices[:3]
        ctx = build_cluster_context(0, df, member_indices, centroid_reps)

        required = [
            "cluster_id", "cluster_size", "sentiment_mix", "severity_mix",
            "top_vehicles", "top_categories", "category_dominance",
            "centroid_rep_indices", "engagement_rep_indices", "recent_rep_indices",
            "centroid_reps_text", "engagement_reps_text", "recent_reps_text",
        ]
        for key in required:
            assert key in ctx, f"Missing key: {key}"

    def test_cluster_size_matches_members(self):
        df = _make_analyzed_df(8)
        member_indices = df.index.tolist()
        ctx = build_cluster_context(0, df, member_indices, member_indices[:3])
        assert ctx["cluster_size"] == len(member_indices)

    def test_centroid_reps_capped_at_7(self):
        df = _make_analyzed_df(10)
        member_indices = df.index.tolist()
        long_reps = member_indices  # 10 indices
        ctx = build_cluster_context(0, df, member_indices, long_reps)
        assert len(ctx["centroid_rep_indices"]) <= 7

    def test_category_dominance_range(self):
        df = _make_analyzed_df(12)
        ctx = build_cluster_context(0, df, df.index.tolist(), df.index.tolist()[:3])
        assert 0.0 <= ctx["category_dominance"] <= 1.0


# ---------------------------------------------------------------------------
# _deterministic_confidence
# ---------------------------------------------------------------------------

class TestDeterministicConfidence:
    def _base_label(self, confidence: str = "high") -> dict:
        return {
            "short_label": "Test",
            "detailed_label": "desc",
            "theme_type": "complaint",
            "confidence": confidence,
            "confidence_score": 0.9,
            "rationale": "r",
            "label_risk": "low",
        }

    def _base_ctx(self, cluster_size: int = 20, category_dominance: float = 0.8) -> dict:
        return {"cluster_size": cluster_size, "category_dominance": category_dominance}

    def test_small_cluster_forced_low(self):
        result = _deterministic_confidence(self._base_label("high"), self._base_ctx(3), 0.9)
        assert result["confidence"] == "low"

    def test_low_det_score_forces_low(self):
        # coherence=0, dominance=0, size=5 → det_score very low
        result = _deterministic_confidence(self._base_label("high"), self._base_ctx(5, 0.0), 0.0)
        assert result["confidence"] in ("low", "medium")

    def test_high_confidence_downgraded_on_medium_score(self):
        # det_score ~ 0.4 + 0.3*0.3 + 0.3*0.33 ≈ 0.49 → below 0.55 threshold
        result = _deterministic_confidence(self._base_label("high"), self._base_ctx(10, 0.3), 0.4)
        assert result["confidence"] in ("medium", "low")

    def test_high_confidence_preserved_on_high_score(self):
        result = _deterministic_confidence(self._base_label("high"), self._base_ctx(30, 0.9), 0.9)
        assert result["confidence"] == "high"

    def test_output_has_deterministic_score(self):
        result = _deterministic_confidence(self._base_label(), self._base_ctx(), 0.8)
        assert "deterministic_score" in result
        assert "coherence" in result
        assert "category_dominance" in result

    def test_does_not_mutate_input_label(self):
        label = self._base_label("high")
        _deterministic_confidence(label, self._base_ctx(3), 0.9)
        assert label["confidence"] == "high"


# ---------------------------------------------------------------------------
# label_cluster_with_llm
# ---------------------------------------------------------------------------

class TestLabelClusterWithLlm:
    def _make_context(self) -> dict:
        df = _make_analyzed_df(8)
        return build_cluster_context(0, df, df.index.tolist(), df.index.tolist()[:3])

    def _mock_openai(self, response_dict: dict) -> MagicMock:
        """Build a sys.modules-injectable openai mock that returns response_dict."""
        mock_module = MagicMock()
        mock_client = MagicMock()
        mock_response = MagicMock()
        # .choices[0].message.content is the path label_cluster_with_llm reads
        mock_response.choices.__getitem__.return_value.message.content = json.dumps(response_dict)
        mock_client.chat.completions.create.return_value = mock_response
        mock_module.OpenAI.return_value = mock_client
        return mock_module

    def test_returns_all_required_fields(self):
        ctx = self._make_context()
        fake_response = {
            "short_label": "Transmission Problems",
            "detailed_label": "Users report shudder and shift issues.",
            "theme_type": "complaint",
            "confidence": "high",
            "confidence_score": 0.85,
            "rationale": "Multiple examples mention transmission.",
            "label_risk": "low",
        }
        openai_mock = self._mock_openai(fake_response)
        with patch.dict("sys.modules", {"openai": openai_mock}):
            result = label_cluster_with_llm(ctx, _FAKE_PROVIDER)
        required = ["short_label", "detailed_label", "theme_type", "confidence", "confidence_score", "rationale", "label_risk"]
        for key in required:
            assert key in result, f"Missing key: {key}"

    def test_llm_error_returns_fallback(self):
        ctx = self._make_context()
        # Make the chat completion raise so we hit the fallback path
        mock_module = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("API down")
        mock_module.OpenAI.return_value = mock_client
        with patch.dict("sys.modules", {"openai": mock_module}):
            result = label_cluster_with_llm(ctx, _FAKE_PROVIDER)
        assert result["confidence"] == "low"
        assert result["theme_type"] == "other"
        assert result["label_risk"] == "high"


# ---------------------------------------------------------------------------
# run_clustering (end-to-end with mocked embeddings + LLM)
# ---------------------------------------------------------------------------

class TestRunClustering:
    def _fake_embed(self, texts, provider, batch_size=100, model="text-embedding-3-small"):
        return _fake_embeddings(len(texts), dim=EMBEDDING_DIM)

    def _fake_label(self, context, provider):
        return {
            "short_label": f"Theme {context['cluster_id']}",
            "detailed_label": "A test cluster.",
            "theme_type": "complaint",
            "confidence": "high",
            "confidence_score": 0.85,
            "rationale": "Good examples.",
            "label_risk": "low",
        }

    def test_artifacts_saved(self, tmp_path):
        df = _make_analyzed_df(_N_ROWS)
        runtime = tmp_path / "runtime"

        with patch("src.trend_insights.embed_texts", side_effect=self._fake_embed), \
             patch("src.trend_insights.label_cluster_with_llm", side_effect=self._fake_label):
            result = run_clustering("test_tag", df, _FAKE_PROVIDER, runtime, n_clusters=_N_CLUSTERS)

        tdir = trends_dir(runtime, "test_tag")
        assert (tdir / "clusters.json").exists()
        assert (tdir / "cluster_examples.json").exists()
        assert (tdir / "cluster_labels.json").exists()
        assert (tdir / "faiss.index").exists()
        assert (tdir / "embedding_metadata.json").exists()

    def test_cluster_labels_structure(self, tmp_path):
        df = _make_analyzed_df(_N_ROWS)
        runtime = tmp_path / "runtime"

        with patch("src.trend_insights.embed_texts", side_effect=self._fake_embed), \
             patch("src.trend_insights.label_cluster_with_llm", side_effect=self._fake_label):
            result = run_clustering("test_tag", df, _FAKE_PROVIDER, runtime, n_clusters=_N_CLUSTERS)

        labels = result["cluster_labels"]
        assert len(labels) > 0
        for cid_str, label in labels.items():
            assert "short_label" in label
            assert "theme_type" in label
            assert label["theme_type"] in ("complaint", "delight", "mixed", "comparison", "question", "other")
            assert "deterministic_score" in label

    def test_embedding_metadata_fields(self, tmp_path):
        df = _make_analyzed_df(_N_ROWS)
        runtime = tmp_path / "runtime"

        with patch("src.trend_insights.embed_texts", side_effect=self._fake_embed), \
             patch("src.trend_insights.label_cluster_with_llm", side_effect=self._fake_label):
            run_clustering("test_tag", df, _FAKE_PROVIDER, runtime, n_clusters=_N_CLUSTERS)

        meta = json.loads((trends_dir(runtime, "test_tag") / "embedding_metadata.json").read_text())
        assert meta["model"] == "text-embedding-3-small"
        assert meta["doc_count"] == _N_ROWS
        assert meta["n_clusters"] == _N_CLUSTERS
        assert "created_at" in meta

    def test_empty_df_raises(self, tmp_path):
        runtime = tmp_path / "runtime"
        with pytest.raises(ValueError, match="No analyzed rows"):
            run_clustering("test_tag", pd.DataFrame(), _FAKE_PROVIDER, runtime)

    def test_heartbeat_cb_called(self, tmp_path):
        df = _make_analyzed_df(_N_ROWS)
        runtime = tmp_path / "runtime"
        calls = []

        def cb(processed, total):
            calls.append((processed, total))

        with patch("src.trend_insights.embed_texts", side_effect=self._fake_embed), \
             patch("src.trend_insights.label_cluster_with_llm", side_effect=self._fake_label):
            run_clustering(
                "test_tag", df, _FAKE_PROVIDER, runtime,
                n_clusters=_N_CLUSTERS, heartbeat_cb=cb,
            )

        # Expect at least the pre-embedding call (0, n_texts) and post-embedding call
        assert len(calls) >= 2

    def test_atomic_writes_no_tmp_files(self, tmp_path):
        df = _make_analyzed_df(_N_ROWS)
        runtime = tmp_path / "runtime"

        with patch("src.trend_insights.embed_texts", side_effect=self._fake_embed), \
             patch("src.trend_insights.label_cluster_with_llm", side_effect=self._fake_label):
            run_clustering("test_tag", df, _FAKE_PROVIDER, runtime, n_clusters=_N_CLUSTERS)

        tdir = trends_dir(runtime, "test_tag")
        tmp_files = list(tdir.glob("*.tmp"))
        assert tmp_files == [], f"Leftover .tmp files: {tmp_files}"


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestTrendsRunEndpoint:
    @pytest.fixture
    def client_and_runtime(self, tmp_path, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "RUNTIME", tmp_path / "runtime")
        from fastapi.testclient import TestClient
        return TestClient(app_module.app), tmp_path / "runtime"

    def _make_classified_csv(self, runtime: Path, tag: str = "test_tag") -> Path:
        from src.gm_insights import save_classified
        df = _make_analyzed_df(6)
        cpath = runtime / tag / "classified" / "classified_posts.csv"
        cpath.parent.mkdir(parents=True, exist_ok=True)
        save_classified(df, cpath)
        return cpath

    def _fake_job_status(self, tag: str = "test_tag", kind: str = "trend") -> dict:
        return {
            "job_id": uuid.uuid4().hex,
            "tag": tag,
            "kind": kind,
            "state": "running",
            "pid": os.getpid(),
            "started_at": time.time(),
            "processed": 0,
            "total": 0,
            "errors": 0,
            "heartbeat_at": time.time(),
            "completed_at": None,
            "artifact_paths": [],
        }

    def test_returns_400_when_no_classified_csv(self, client_and_runtime):
        client, runtime = client_and_runtime
        response = client.post("/api/trends/run", json={"tag": "no_data_tag"})
        assert response.status_code == 400

    def test_starts_job_and_returns_started_true(self, client_and_runtime):
        client, runtime = client_and_runtime
        import app as app_module
        self._make_classified_csv(runtime)

        fake_status = self._fake_job_status()
        with patch("app.start_job", return_value=fake_status) as mock_start:
            response = client.post("/api/trends/run", json={"tag": "test_tag"})

        assert response.status_code == 200
        data = response.json()
        assert data.get("started") is True
        mock_start.assert_called_once()

    def test_does_not_double_start_running_job(self, client_and_runtime):
        client, runtime = client_and_runtime
        self._make_classified_csv(runtime)

        existing = self._fake_job_status()
        with patch("app.find_active_job", return_value=existing), \
             patch("app.start_job") as mock_start:
            response = client.post("/api/trends/run", json={"tag": "test_tag"})

        assert response.status_code == 200
        assert response.json().get("started") is False
        mock_start.assert_not_called()


class TestTrendsStatusEndpoint:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "RUNTIME", tmp_path / "runtime")
        from fastapi.testclient import TestClient
        return TestClient(app_module.app)

    def test_returns_idle_when_no_jobs(self, client):
        with patch("app.find_active_job", return_value=None):
            response = client.get("/api/trends/status", params={"tag": "test_tag"})
        assert response.status_code == 200
        assert response.json()["state"] == "idle"

    def test_returns_running_status(self, client):
        fake = {
            "job_id": "abc", "tag": "test_tag", "kind": "trend",
            "state": "running", "pid": os.getpid(),
            "started_at": time.time(), "heartbeat_at": time.time(),
        }
        with patch("app.find_active_job", return_value=fake):
            response = client.get("/api/trends/status", params={"tag": "test_tag"})
        assert response.status_code == 200
        assert response.json()["state"] == "running"


class TestTrendsResultsEndpoint:
    @pytest.fixture
    def client_and_runtime(self, tmp_path, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "RUNTIME", tmp_path / "runtime")
        from fastapi.testclient import TestClient
        return TestClient(app_module.app), tmp_path / "runtime"

    def _write_artifacts(self, runtime: Path, tag: str = "test_tag") -> None:
        from src.trend_insights import trends_dir
        tdir = trends_dir(runtime, tag)
        tdir.mkdir(parents=True, exist_ok=True)
        labels = {
            "0": {
                "cluster_id": 0, "short_label": "Transmission Issues",
                "theme_type": "complaint", "confidence": "high",
                "confidence_score": 0.8, "deterministic_score": 0.7,
            },
            "1": {
                "cluster_id": 1, "short_label": "EV Questions",
                "theme_type": "question", "confidence": "medium",
                "confidence_score": 0.55, "deterministic_score": 0.5,
            },
        }
        examples = {
            "0": {"cluster_size": 10, "sentiment_mix": {"negative": 8}, "severity_mix": {}, "top_vehicles": ["silverado"], "top_categories": []},
            "1": {"cluster_size": 5, "sentiment_mix": {"neutral": 5}, "severity_mix": {}, "top_vehicles": ["bolt"], "top_categories": []},
        }
        metadata = {"model": "text-embedding-3-small", "dim": 1536, "doc_count": 15, "n_clusters": 2}
        (tdir / "cluster_labels.json").write_text(json.dumps(labels))
        (tdir / "cluster_examples.json").write_text(json.dumps(examples))
        (tdir / "embedding_metadata.json").write_text(json.dumps(metadata))

    def test_returns_404_like_message_when_no_artifacts(self, client_and_runtime):
        client, runtime = client_and_runtime
        response = client.get("/api/trends", params={"tag": "empty_tag"})
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is False

    def test_returns_clusters_list(self, client_and_runtime):
        client, runtime = client_and_runtime
        self._write_artifacts(runtime)
        response = client.get("/api/trends", params={"tag": "test_tag"})
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert len(data["clusters"]) == 2

    def test_clusters_have_required_fields(self, client_and_runtime):
        client, runtime = client_and_runtime
        self._write_artifacts(runtime)
        response = client.get("/api/trends", params={"tag": "test_tag"})
        clusters = response.json()["clusters"]
        for cluster in clusters:
            assert "cluster_id" in cluster
            assert "label" in cluster
            assert "cluster_size" in cluster
            assert "sentiment_mix" in cluster
            assert "top_vehicles" in cluster

    def test_metadata_included(self, client_and_runtime):
        client, runtime = client_and_runtime
        self._write_artifacts(runtime)
        response = client.get("/api/trends", params={"tag": "test_tag"})
        data = response.json()
        assert "metadata" in data
        assert data["metadata"]["model"] == "text-embedding-3-small"
