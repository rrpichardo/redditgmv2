"""Tests for M4 Data Explorer: evidence table, date filter, and lazy heatmap endpoint."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app as app_module  # noqa: E402
from app import app  # noqa: E402
from src.gm_insights import evidence_table, filter_analyzed  # noqa: E402

# Each class that interacts with the filesystem saves/restores RUNTIME in setup/teardown
# so this module's tmp dir doesn't clobber RUNTIME for earlier test modules.
_tmp = tempfile.mkdtemp()

client = TestClient(app, raise_server_exceptions=True)

TAG = "m4_test"

# Minimal set of classified-CSV columns that normalize_reddit_frame() recognises.
_DEFAULTS: dict[str, object] = {
    "complaint": 1,
    "competitor_mention": 0,
    "dealer_experience": 0,
    "reliability_concern": 0,
    "software_tech_issue": 0,
    "purchase_intent": 0,
    "loyalty_signal": 0,
    "ev_topic": 0,
    "enthusiast_mod": 0,
    "classic_vintage": 0,
    "top_complaint_category": "transmission_torque_converter",
    "multi_complaint_categories": "",
    "vehicle_mentioned": "Silverado",
    "comment_type": "complaint_report",
    "competitor_brand": "none",
    "issue_severity": "high",
    "description": "test description",
    "classifier_mode": "llm",
    "sentiment": "negative",
    "skip_classification": False,
}


def _make_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{**_DEFAULTS, **r} for r in rows])


def _write_classified(df: pd.DataFrame, tag: str = TAG) -> None:
    path = Path(_tmp) / tag / "classified" / "classified_posts.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


# ---------------------------------------------------------------------------
# Evidence table unit tests
# ---------------------------------------------------------------------------

class TestEvidenceTable:
    def test_includes_target_text_column(self):
        """evidence_table() must include target_text so the frontend can show body content."""
        df = _make_df([
            {"source_id": "r1", "target_text": "Bad brakes", "score_norm": 0.9,
             "created_at_norm": "2024-01-10 12:00:00"},
        ])
        out = evidence_table(df)
        assert "target_text" in out.columns

    def test_sorted_by_score_norm_descending(self):
        """Rows must come back with highest score_norm first (relevance ranking)."""
        df = _make_df([
            {"source_id": "r1", "score_norm": 0.3, "target_text": "low score",
             "created_at_norm": "2024-01-10 12:00:00"},
            {"source_id": "r2", "score_norm": 0.9, "target_text": "high score",
             "created_at_norm": "2024-01-11 12:00:00"},
            {"source_id": "r3", "score_norm": 0.6, "target_text": "mid score",
             "created_at_norm": "2024-01-12 12:00:00"},
        ])
        out = evidence_table(df)
        scores = list(out["score_norm"])
        assert scores == sorted(scores, reverse=True), f"expected desc order, got {scores}"

    def test_limit_respected(self):
        """Default limit of 500 is enforced."""
        df = _make_df([
            {"source_id": f"r{i}", "score_norm": i / 1000, "target_text": "x",
             "created_at_norm": "2024-01-10 12:00:00"}
            for i in range(600)
        ])
        out = evidence_table(df)
        assert len(out) == 500

    def test_custom_limit(self):
        df = _make_df([
            {"source_id": f"r{i}", "score_norm": 0.5, "target_text": "y",
             "created_at_norm": "2024-01-10 12:00:00"}
            for i in range(50)
        ])
        out = evidence_table(df, limit=10)
        assert len(out) == 10

    def test_date_formatted_as_yyyy_mm_dd(self):
        df = _make_df([
            {"source_id": "r1", "created_at_norm": "2024-03-15 08:30:00",
             "target_text": "t", "score_norm": 0.5},
        ])
        out = evidence_table(df)
        assert out["created_at_norm"].iloc[0] == "2024-03-15"


# ---------------------------------------------------------------------------
# Date range filter tests
# ---------------------------------------------------------------------------

class TestDateRangeFilter:
    def _base_df(self) -> pd.DataFrame:
        return _make_df([
            {"source_id": "r1", "created_at_norm": "2024-01-05 12:00:00",
             "target_text": "early", "score_norm": 0.5},
            {"source_id": "r2", "created_at_norm": "2024-03-15 12:00:00",
             "target_text": "mid", "score_norm": 0.5},
            {"source_id": "r3", "created_at_norm": "2024-06-20 12:00:00",
             "target_text": "late", "score_norm": 0.5},
        ])

    def test_date_start_filters_out_earlier_rows(self):
        df = self._base_df()
        from datetime import date
        out = filter_analyzed(df, {"date_range": [date(2024, 3, 1), None]})
        dates = pd.to_datetime(out["created_at_norm"], errors="coerce").dt.date
        assert all(d >= date(2024, 3, 1) for d in dates), f"found early rows: {list(dates)}"

    def test_date_end_filters_out_later_rows(self):
        df = self._base_df()
        from datetime import date
        out = filter_analyzed(df, {"date_range": [None, date(2024, 3, 31)]})
        dates = pd.to_datetime(out["created_at_norm"], errors="coerce").dt.date
        assert all(d <= date(2024, 3, 31) for d in dates), f"found late rows: {list(dates)}"

    def test_date_range_both_bounds(self):
        df = self._base_df()
        from datetime import date
        out = filter_analyzed(df, {"date_range": [date(2024, 2, 1), date(2024, 4, 30)]})
        # Only r2 (March 15) should survive.
        assert len(out) == 1

    def test_no_date_range_returns_all(self):
        df = self._base_df()
        out = filter_analyzed(df, {})
        assert len(out) == len(df)


# ---------------------------------------------------------------------------
# API endpoint tests — date filter wired into /api/run
# ---------------------------------------------------------------------------

class TestApiRunDateFilter:
    def setup_method(self):
        self._orig_runtime = app_module.RUNTIME
        app_module.RUNTIME = Path(_tmp)
        df = _make_df([
            {"source_id": "r1", "created_at_norm": "2024-01-05 12:00:00",
             "target_text": "jan post", "score_norm": 0.5},
            {"source_id": "r2", "created_at_norm": "2024-06-15 12:00:00",
             "target_text": "june post", "score_norm": 0.5},
        ])
        _write_classified(df, tag=TAG + "_api")

    def teardown_method(self):
        app_module.RUNTIME = self._orig_runtime

    def test_date_start_filters_run_endpoint(self):
        r = client.get(f"/api/run?tag={TAG}_api&date_start=2024-04-01")
        assert r.status_code == 200
        data = r.json()
        # Only the June row should appear in evidence.
        for row in data.get("evidence", []):
            assert row.get("created_at_norm", "") >= "2024-04-01", f"unexpected row: {row}"

    def test_date_end_filters_run_endpoint(self):
        r = client.get(f"/api/run?tag={TAG}_api&date_end=2024-03-31")
        assert r.status_code == 200
        data = r.json()
        for row in data.get("evidence", []):
            assert row.get("created_at_norm", "") <= "2024-03-31", f"unexpected row: {row}"

    def test_invalid_date_string_does_not_crash(self):
        """Malformed date strings are silently ignored (no 500)."""
        r = client.get(f"/api/run?tag={TAG}_api&date_start=not-a-date")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# /api/charts/detail endpoint — lazy heatmaps
# ---------------------------------------------------------------------------

class TestChartsDetail:
    def setup_method(self):
        self._orig_runtime = app_module.RUNTIME
        app_module.RUNTIME = Path(_tmp)
        df = _make_df([
            {"source_id": f"r{i}", "target_text": "text", "score_norm": 0.5,
             "created_at_norm": "2024-01-10 12:00:00",
             "top_complaint_category": "engine_lifter_afm_dod" if i % 2 == 0 else "transmission_torque_converter",
             "vehicle_mentioned": "Silverado" if i % 2 == 0 else "Tahoe"}
            for i in range(20)
        ])
        _write_classified(df, tag=TAG + "_detail")

    def teardown_method(self):
        app_module.RUNTIME = self._orig_runtime

    def test_returns_chart_data_keys(self):
        """/api/charts/detail returns {category_by_model, cooccurrence} at the top level."""
        r = client.get(f"/api/charts/detail?tag={TAG}_detail")
        assert r.status_code == 200
        data = r.json()
        assert "category_by_model" in data
        assert "cooccurrence" in data

    def test_accepts_same_filter_params(self):
        """Ensure date_start / date_end are accepted without error."""
        r = client.get(
            f"/api/charts/detail?tag={TAG}_detail&sentiment=negative&date_start=2024-01-01"
        )
        assert r.status_code == 200

    def test_empty_tag_returns_empty_structure(self):
        r = client.get("/api/charts/detail?tag=nonexistent_m4_xyz")
        assert r.status_code == 200
        data = r.json()
        # Empty dataset still returns the correct schema.
        assert "category_by_model" in data
        assert "cooccurrence" in data

    def test_date_filter_narrows_heatmap(self):
        """A date range that excludes all rows returns empty-but-valid heatmap structures."""
        r = client.get(f"/api/charts/detail?tag={TAG}_detail&date_start=2030-01-01")
        assert r.status_code == 200
        data = r.json()
        cat = data["category_by_model"]
        # Filtered-empty frame → empty rows/columns/values lists.
        assert isinstance(cat, dict)
        assert cat.get("rows") == []


# ---------------------------------------------------------------------------
# Evidence in /api/run response
# ---------------------------------------------------------------------------

class TestEvidenceInRunResponse:
    def setup_method(self):
        self._orig_runtime = app_module.RUNTIME
        app_module.RUNTIME = Path(_tmp)
        df = _make_df([
            {"source_id": "r1", "target_text": "bad brakes on my truck",
             "score_norm": 0.8, "created_at_norm": "2024-01-10 12:00:00"},
            {"source_id": "r2", "target_text": "great engine performance",
             "score_norm": 0.3, "created_at_norm": "2024-01-11 12:00:00"},
        ])
        _write_classified(df, tag=TAG + "_ev")

    def teardown_method(self):
        app_module.RUNTIME = self._orig_runtime

    def test_evidence_contains_target_text(self):
        r = client.get(f"/api/run?tag={TAG}_ev")
        assert r.status_code == 200
        evidence = r.json().get("evidence", [])
        assert len(evidence) > 0
        for row in evidence:
            assert "target_text" in row, f"target_text missing from evidence row: {row.keys()}"

    def test_evidence_sorted_by_score_descending(self):
        r = client.get(f"/api/run?tag={TAG}_ev")
        assert r.status_code == 200
        evidence = r.json().get("evidence", [])
        scores = [row["score_norm"] for row in evidence if row.get("score_norm") is not None]
        assert scores == sorted(scores, reverse=True), f"evidence not sorted by score: {scores}"
