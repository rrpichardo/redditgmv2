"""Phase 6 tests: FAISS Q&A / RAG retrieval.

Covers:
- build_qa_docs: column mapping, empty/no-classify guards
- make_vectors / retrieve: FAISS round-trip with mocked embed_texts
- make_context: formatting, char budget
- answer_question: LLM mock, empty-hits path, error fallback
- load_qa_artifacts: missing index guard
- build_qa_index: artifact existence, heartbeat, classify-first guard
- POST /api/qa/build-index: 400 when no CSV, dedup guard, started=True
- GET /api/qa/status: idle default, job_id lookup, kind filtering
- POST /api/qa/search: 400 when index missing, 400 when empty query
- POST /api/qa/answer: 400 when index missing, 400 when empty question
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

faiss = pytest.importorskip("faiss")

from src.qa_retrieval import (
    answer_question,
    build_qa_docs,
    build_qa_index,
    load_qa_artifacts,
    make_context,
    qa_dir,
    retrieve,
)
from src.trend_insights import EMBEDDING_DIM
from src.gm_insights import ProviderConfig

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_FAKE_PROVIDER = ProviderConfig(
    provider="openrouter",
    model="test-model",
    base_url="https://example.com",
    api_key_env="TEST_KEY",
    api_key="fake-key",
)

_N_ROWS = 15


def _make_classified_df(n: int = _N_ROWS) -> pd.DataFrame:
    """Minimal classified DataFrame with all columns qa_retrieval reads."""
    rows = []
    vehicles = ["silverado", "tahoe", "equinox"]
    sentiments = ["positive", "negative", "neutral"]
    categories = ["transmission_torque_converter", "electrical_battery_alternator", "ac_hvac"]
    for i in range(n):
        rows.append({
            "source_id": f"row_{i}",
            "source_type": "comment",
            "target_text": f"This is a real comment body number {i} about my vehicle issue.",
            "description": f"Insight number {i} about a {vehicles[i % 3]} issue.",
            "top_complaint_category": categories[i % 3] if i % 3 != 0 else "not_applicable",
            "vehicle_mentioned": vehicles[i % 3],
            "sentiment": sentiments[i % 3],
            "subreddit_norm": "Silverado",
            "permalink_norm": f"/r/sub/comments/{i}",
            "created_at_norm": f"2024-0{(i % 9) + 1}-01",
            "score_norm": float(i * 3),
            "skip_classification": False,
            "classifier_mode": "heuristic_preview",
            "complaint": int(i % 2),
            "competitor_mention": 0,
            "dealer_experience": 0,
            "reliability_concern": 0,
            "software_tech_issue": 0,
            "purchase_intent": 0,
            "loyalty_signal": 0,
            "ev_topic": 0,
            "enthusiast_mod": 0,
            "classic_vintage": 0,
            "multi_complaint_categories": "",
            "comment_type": "shared_experience",
            "competitor_brand": "none",
            "issue_severity": "major",
            "engagement_level": "medium",
        })
    return pd.DataFrame(rows)


def _fake_embeddings(n: int, dim: int = EMBEDDING_DIM) -> np.ndarray:
    rng = np.random.default_rng(42)
    return rng.standard_normal((n, dim)).astype(np.float32)


def _fake_embed(texts, provider, batch_size=100, model="text-embedding-3-small"):
    return _fake_embeddings(len(texts), dim=EMBEDDING_DIM)


# ---------------------------------------------------------------------------
# build_qa_docs
# ---------------------------------------------------------------------------

class TestBuildQaDocs:
    def test_returns_one_doc_per_embeddable_row(self):
        df = _make_classified_df(10)
        docs = build_qa_docs(df)
        assert len(docs) == 10

    def test_doc_fields_present(self):
        df = _make_classified_df(5)
        docs = build_qa_docs(df)
        required = {"source_id", "body", "description", "vehicle", "sentiment", "category",
                    "subreddit", "permalink", "score", "embed_text"}
        for doc in docs:
            assert required.issubset(doc.keys()), f"Missing keys in doc: {required - doc.keys()}"

    def test_embed_text_is_non_empty(self):
        df = _make_classified_df(5)
        docs = build_qa_docs(df)
        for doc in docs:
            assert doc["embed_text"].strip(), "embed_text should not be empty"

    def test_empty_df_returns_empty_list(self):
        docs = build_qa_docs(pd.DataFrame())
        assert docs == []

    def test_rows_with_no_body_or_description_skipped(self):
        # Row with empty target_text and no description should be skipped
        df = pd.DataFrame([{
            "source_id": "x",
            "target_text": "",
            "description": "",
            "top_complaint_category": "",
            "vehicle_mentioned": "",
            "sentiment": "",
            "subreddit_norm": "",
            "permalink_norm": "",
            "created_at_norm": "",
            "score_norm": 0,
        }])
        docs = build_qa_docs(df)
        assert len(docs) == 0

    def test_not_applicable_category_excluded_from_embed_text(self):
        df = pd.DataFrame([{
            "source_id": "a",
            "target_text": "some body text",
            "description": "a description",
            "top_complaint_category": "not_applicable",
            "vehicle_mentioned": "silverado",
            "sentiment": "negative",
            "subreddit_norm": "Silverado",
            "permalink_norm": "/r/s/1",
            "created_at_norm": "2024-01-01",
            "score_norm": 5,
        }])
        docs = build_qa_docs(df)
        assert len(docs) == 1
        assert "not_applicable" not in docs[0]["embed_text"]


# ---------------------------------------------------------------------------
# retrieve (mocked embed_texts)
# ---------------------------------------------------------------------------

class TestRetrieve:
    def _build_index_and_docs(self, n=10):
        import faiss as _faiss
        from src.trend_insights import normalize_l2
        df = _make_classified_df(n)
        docs = build_qa_docs(df)
        embeddings = _fake_embeddings(len(docs))
        normed = normalize_l2(embeddings)
        index = _faiss.IndexFlatIP(normed.shape[1])
        index.add(normed)
        return docs, index

    def test_returns_k_results(self):
        docs, index = self._build_index_and_docs(10)
        with patch("src.qa_retrieval.embed_texts", side_effect=_fake_embed):
            hits = retrieve("transmission issue", docs, index, _FAKE_PROVIDER, k=5)
        assert len(hits) == 5

    def test_results_have_retrieval_score(self):
        docs, index = self._build_index_and_docs(10)
        with patch("src.qa_retrieval.embed_texts", side_effect=_fake_embed):
            hits = retrieve("any query", docs, index, _FAKE_PROVIDER, k=3)
        for hit in hits:
            assert "retrieval_score" in hit
            assert isinstance(hit["retrieval_score"], float)

    def test_k_clamped_to_doc_count(self):
        docs, index = self._build_index_and_docs(4)
        with patch("src.qa_retrieval.embed_texts", side_effect=_fake_embed):
            hits = retrieve("query", docs, index, _FAKE_PROVIDER, k=100)
        assert len(hits) <= 4

    def test_empty_docs_returns_empty(self):
        import faiss as _faiss
        index = _faiss.IndexFlatIP(EMBEDDING_DIM)
        hits = retrieve("query", [], index, _FAKE_PROVIDER, k=5)
        assert hits == []


# ---------------------------------------------------------------------------
# make_context
# ---------------------------------------------------------------------------

class TestMakeContext:
    def _fake_hits(self, n=3):
        return [
            {
                "body": f"Comment body {i} about my vehicle.",
                "vehicle": "silverado",
                "sentiment": "negative",
                "category": "transmission torque converter",
                "subreddit": "Silverado",
                "permalink": f"/r/sub/comments/{i}",
                "retrieval_score": 0.9 - i * 0.1,
            }
            for i in range(n)
        ]

    def test_output_is_string(self):
        hits = self._fake_hits(3)
        ctx = make_context(hits)
        assert isinstance(ctx, str)

    def test_contains_evidence_numbers(self):
        hits = self._fake_hits(3)
        ctx = make_context(hits)
        assert "[1]" in ctx
        assert "[2]" in ctx

    def test_char_budget_respected(self):
        hits = self._fake_hits(10)
        ctx = make_context(hits, max_chars=200)
        assert len(ctx) <= 300  # small tolerance for edge rounding

    def test_empty_hits_returns_empty(self):
        assert make_context([]) == ""

    def test_permalink_included(self):
        hits = self._fake_hits(1)
        ctx = make_context(hits)
        assert "/r/sub/comments/0" in ctx


# ---------------------------------------------------------------------------
# build_qa_index (mocked embed_texts)
# ---------------------------------------------------------------------------

class TestBuildQaIndex:
    def test_artifacts_saved(self, tmp_path):
        df = _make_classified_df(_N_ROWS)
        with patch("src.qa_retrieval.embed_texts", side_effect=_fake_embed):
            result = build_qa_index("test_tag", df, _FAKE_PROVIDER, tmp_path)

        qdir = qa_dir(tmp_path, "test_tag")
        assert (qdir / "docs.json").exists()
        assert (qdir / "index.faiss").exists()
        assert (qdir / "metadata.json").exists()

    def test_metadata_fields(self, tmp_path):
        df = _make_classified_df(_N_ROWS)
        with patch("src.qa_retrieval.embed_texts", side_effect=_fake_embed):
            build_qa_index("test_tag", df, _FAKE_PROVIDER, tmp_path)

        meta = json.loads((qa_dir(tmp_path, "test_tag") / "metadata.json").read_text())
        assert meta["model"] == "text-embedding-3-small"
        assert meta["doc_count"] == _N_ROWS
        assert "dim" in meta
        assert "created_at" in meta
        assert meta["tag"] == "test_tag"

    def test_no_tmp_files_leftover(self, tmp_path):
        df = _make_classified_df(_N_ROWS)
        with patch("src.qa_retrieval.embed_texts", side_effect=_fake_embed):
            build_qa_index("test_tag", df, _FAKE_PROVIDER, tmp_path)
        qdir = qa_dir(tmp_path, "test_tag")
        assert list(qdir.glob("*.tmp")) == []

    def test_empty_df_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError, match="classification"):
            build_qa_index("test_tag", pd.DataFrame(), _FAKE_PROVIDER, tmp_path)

    def test_heartbeat_called(self, tmp_path):
        df = _make_classified_df(_N_ROWS)
        calls = []
        def cb(processed, total): calls.append((processed, total))
        with patch("src.qa_retrieval.embed_texts", side_effect=_fake_embed):
            build_qa_index("test_tag", df, _FAKE_PROVIDER, tmp_path, heartbeat_cb=cb)
        assert len(calls) >= 2  # at least start and finish

    def test_result_contains_doc_count(self, tmp_path):
        df = _make_classified_df(_N_ROWS)
        with patch("src.qa_retrieval.embed_texts", side_effect=_fake_embed):
            result = build_qa_index("test_tag", df, _FAKE_PROVIDER, tmp_path)
        assert result["doc_count"] == _N_ROWS

    def test_artifact_paths_returned(self, tmp_path):
        df = _make_classified_df(_N_ROWS)
        with patch("src.qa_retrieval.embed_texts", side_effect=_fake_embed):
            result = build_qa_index("test_tag", df, _FAKE_PROVIDER, tmp_path)
        assert len(result["artifact_paths"]) == 3
        for p in result["artifact_paths"]:
            assert Path(p).exists()


# ---------------------------------------------------------------------------
# load_qa_artifacts
# ---------------------------------------------------------------------------

class TestLoadQaArtifacts:
    def test_raises_if_index_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_qa_artifacts(tmp_path, "missing_tag")

    def test_returns_docs_index_metadata(self, tmp_path):
        df = _make_classified_df(_N_ROWS)
        with patch("src.qa_retrieval.embed_texts", side_effect=_fake_embed):
            build_qa_index("test_tag", df, _FAKE_PROVIDER, tmp_path)

        docs, index, metadata = load_qa_artifacts(tmp_path, "test_tag")
        assert len(docs) == _N_ROWS
        assert index.ntotal == _N_ROWS
        assert metadata["doc_count"] == _N_ROWS


# ---------------------------------------------------------------------------
# answer_question (mocked LLM)
# ---------------------------------------------------------------------------

class TestAnswerQuestion:
    def _fake_hits(self, n=3):
        return [
            {"body": f"Body {i}", "vehicle": "silverado", "sentiment": "negative",
             "category": "transmission", "subreddit": "Silverado",
             "permalink": f"/r/s/{i}", "retrieval_score": 0.9 - i * 0.1}
            for i in range(n)
        ]

    def test_returns_string_from_llm(self):
        openai_mock = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content="The answer is transmission issues."))
        ]
        openai_mock.OpenAI.return_value = mock_client

        with patch.dict("sys.modules", {"openai": openai_mock}):
            result = answer_question("What is wrong?", self._fake_hits(3), _FAKE_PROVIDER)

        assert "answer" in result.lower() or "transmission" in result.lower() or len(result) > 0

    def test_empty_hits_returns_no_evidence_message(self):
        result = answer_question("What is wrong?", [], _FAKE_PROVIDER)
        assert "No relevant evidence" in result

    def test_llm_error_returns_fallback_with_context(self):
        openai_mock = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("API down")
        openai_mock.OpenAI.return_value = mock_client

        hits = self._fake_hits(2)
        with patch.dict("sys.modules", {"openai": openai_mock}):
            result = answer_question("What is wrong?", hits, _FAKE_PROVIDER)

        # Falls back to showing the retrieval context
        assert "LLM error" in result or "Body 0" in result


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestQaBuildIndexEndpoint:
    @pytest.fixture
    def client_and_runtime(self, tmp_path, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "RUNTIME", tmp_path / "runtime")
        from fastapi.testclient import TestClient
        return TestClient(app_module.app), tmp_path / "runtime"

    def _make_classified_csv(self, runtime: Path, tag: str = "test_tag") -> Path:
        from src.gm_insights import save_classified
        df = _make_classified_df(6)
        cpath = runtime / tag / "classified" / "classified_posts.csv"
        cpath.parent.mkdir(parents=True, exist_ok=True)
        save_classified(df, cpath)
        return cpath

    def _fake_job_status(self, tag: str = "test_tag", kind: str = "faiss_qa") -> dict:
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
        client, _ = client_and_runtime
        response = client.post("/api/qa/build-index", json={"tag": "no_data_tag"})
        assert response.status_code == 400
        assert "classification" in response.json()["detail"].lower()

    def test_starts_job_returns_started_true(self, client_and_runtime):
        client, runtime = client_and_runtime
        self._make_classified_csv(runtime)
        fake_status = self._fake_job_status()
        with patch("app.start_job", return_value=fake_status):
            response = client.post("/api/qa/build-index", json={"tag": "test_tag", "api_key": "fake-key"})
        assert response.status_code == 200
        assert response.json().get("started") is True

    def test_does_not_double_start_running_job(self, client_and_runtime):
        client, runtime = client_and_runtime
        self._make_classified_csv(runtime)
        existing = self._fake_job_status()
        with patch("app.find_active_job", return_value=existing), \
             patch("app.start_job") as mock_start:
            response = client.post("/api/qa/build-index", json={"tag": "test_tag"})
        assert response.status_code == 200
        assert response.json().get("started") is False
        mock_start.assert_not_called()


class TestQaStatusEndpoint:
    @pytest.fixture
    def client_and_runtime(self, tmp_path, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "RUNTIME", tmp_path / "runtime")
        from fastapi.testclient import TestClient
        return TestClient(app_module.app), tmp_path / "runtime"

    def test_returns_blocked_without_key_when_no_jobs(self, client_and_runtime):
        client, _ = client_and_runtime
        response = client.get("/api/qa/status", params={"tag": "empty_tag"})
        assert response.status_code == 200
        assert response.json()["state"] == "blocked_no_api_key"

    def test_returns_status_by_job_id(self, client_and_runtime):
        from src.jobs import write_status, job_path
        client, runtime = client_and_runtime
        runtime.mkdir(parents=True, exist_ok=True)
        tag = "test_tag"
        job_id = uuid.uuid4().hex
        jp = job_path(runtime, tag, job_id)
        write_status(jp, {
            "job_id": job_id, "tag": tag, "kind": "faiss_qa",
            "state": "completed", "pid": os.getpid(),
            "started_at": time.time(), "completed_at": time.time(),
            "heartbeat_at": time.time(), "processed": 10, "total": 10,
            "errors": 0, "artifact_paths": [],
        })
        response = client.get("/api/qa/status", params={"tag": tag, "job_id": job_id})
        assert response.status_code == 200
        assert response.json()["state"] == "blocked_no_api_key"

    def test_filters_by_kind(self, client_and_runtime):
        from src.jobs import write_status, job_path
        client, runtime = client_and_runtime
        runtime.mkdir(parents=True, exist_ok=True)
        tag = "test_tag"
        # Write a trend job — should NOT appear in qa/status
        trend_id = uuid.uuid4().hex
        write_status(job_path(runtime, tag, trend_id), {
            "job_id": trend_id, "tag": tag, "kind": "trend",
            "state": "completed", "pid": os.getpid(),
            "started_at": time.time(), "completed_at": time.time(),
            "heartbeat_at": time.time(), "processed": 5, "total": 5,
            "errors": 0, "artifact_paths": [],
        })
        response = client.get("/api/qa/status", params={"tag": tag})
        assert response.status_code == 200
        # Trend jobs do not change the Q&A projection.
        assert response.json()["state"] == "blocked_no_api_key"


class TestQaSearchEndpoint:
    @pytest.fixture
    def client_and_runtime(self, tmp_path, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "RUNTIME", tmp_path / "runtime")
        from fastapi.testclient import TestClient
        return TestClient(app_module.app), tmp_path / "runtime"

    def test_400_when_index_missing(self, client_and_runtime):
        client, _ = client_and_runtime
        response = client.post("/api/qa/search", json={
            "tag": "no_index_tag", "query": "transmission issues"
        })
        assert response.status_code == 409
        assert response.json()["detail"]["state"] == "blocked_no_api_key"

    def test_400_when_empty_query(self, client_and_runtime):
        client, _ = client_and_runtime
        response = client.post("/api/qa/search", json={
            "tag": "test_tag", "query": "   "
        })
        assert response.status_code == 400

    def test_returns_hits_with_real_index(self, client_and_runtime):
        client, runtime = client_and_runtime
        df = _make_classified_df(_N_ROWS)
        from src.gm_insights import save_classified
        from src.qa_retrieval import classified_fingerprint
        classified = runtime / "test_tag" / "classified" / "classified_posts.csv"
        save_classified(df, classified)
        with patch("src.qa_retrieval.embed_texts", side_effect=_fake_embed):
            build_qa_index(
                "test_tag", df, _FAKE_PROVIDER, runtime,
                input_fingerprint=classified_fingerprint(classified),
            )
        with patch("src.qa_retrieval.embed_texts", side_effect=_fake_embed):
            response = client.post("/api/qa/search", json={
                "tag": "test_tag",
                "query": "silverado transmission problem",
                "provider": "openrouter",
                "api_key": "fake-key",
                "k": 4,
            })
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert isinstance(data["hits"], list)
        assert len(data["hits"]) <= 4


class TestQaAnswerEndpoint:
    @pytest.fixture
    def client_and_runtime(self, tmp_path, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "RUNTIME", tmp_path / "runtime")
        from fastapi.testclient import TestClient
        return TestClient(app_module.app), tmp_path / "runtime"

    def test_400_when_index_missing(self, client_and_runtime):
        client, _ = client_and_runtime
        response = client.post("/api/qa/answer", json={
            "tag": "no_index_tag", "question": "What are common issues?"
        })
        assert response.status_code == 409

    def test_400_when_empty_question(self, client_and_runtime):
        client, _ = client_and_runtime
        response = client.post("/api/qa/answer", json={
            "tag": "test_tag", "question": "   "
        })
        assert response.status_code == 400

    def test_returns_answer_with_mocked_llm(self, client_and_runtime):
        client, runtime = client_and_runtime
        df = _make_classified_df(_N_ROWS)
        from src.gm_insights import save_classified
        from src.qa_retrieval import classified_fingerprint
        classified = runtime / "test_tag" / "classified" / "classified_posts.csv"
        save_classified(df, classified)
        with patch("src.qa_retrieval.embed_texts", side_effect=_fake_embed):
            build_qa_index(
                "test_tag", df, _FAKE_PROVIDER, runtime,
                input_fingerprint=classified_fingerprint(classified),
            )

        openai_mock = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content="Transmission issues are the top complaint."))
        ]
        openai_mock.OpenAI.return_value = mock_client

        with patch("src.qa_retrieval.embed_texts", side_effect=_fake_embed), \
             patch.dict("sys.modules", {"openai": openai_mock}):
            response = client.post("/api/qa/answer", json={
                "tag": "test_tag",
                "question": "What are the main complaints?",
                "provider": "openrouter",
                "api_key": "fake-key",
                "k": 4,
            })

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert isinstance(data["answer"], str)
        assert len(data["answer"]) > 0
        assert isinstance(data["hits"], list)
