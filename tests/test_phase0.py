"""Phase 0 correctness tests.

Covers:
- analyzed_frame exclusion: skip_classification, sentiment=skipped/error, classifier_mode empty
- Golden fixture metrics parity with notebook-derived expected values
- JSON sanitization: NaN, inf, -inf become None
- Atomic artifact writes: temp file + os.replace
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pandas as pd
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(**kwargs) -> dict:
    """Minimal analyzed row with all required fields."""
    defaults = {
        "source_id": "r1",
        "source_type": "comment",
        "post_id_norm": "p1",
        "subreddit_norm": "Silverado",
        "title_norm": "Good title",
        "target_text": "This is a long enough comment to pass junk check",
        "combined_text": "context text",
        "score_norm": 10,
        "created_at_norm": "2024-01-01",
        "permalink_norm": "/r/Silverado/comments/abc",
        "skip_classification": False,
        "classifier_mode": "heuristic_preview",
        "sentiment": "neutral",
        "complaint": 0,
        "competitor_mention": 0,
        "dealer_experience": 0,
        "reliability_concern": 0,
        "software_tech_issue": 0,
        "purchase_intent": 0,
        "loyalty_signal": 0,
        "ev_topic": 0,
        "enthusiast_mod": 0,
        "classic_vintage": 0,
        "top_complaint_category": "not_applicable",
        "multi_complaint_categories": "",
        "vehicle_mentioned": "silverado",
        "comment_type": "shared_experience",
        "competitor_brand": "none",
        "issue_severity": "none",
        "description": "A neutral comment about Silverado.",
        "engagement_level": "medium",
    }
    defaults.update(kwargs)
    return defaults


def _df(*rows) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


# ---------------------------------------------------------------------------
# analyzed_frame exclusion tests
# ---------------------------------------------------------------------------

class TestAnalyzedFrameExclusion:
    """Verify that analyzed_frame drops exactly the rows that should be dropped."""

    def test_skip_classification_true_excluded(self):
        from src.gm_insights import analyzed_frame

        good = _make_row(source_id="good")
        junk = _make_row(source_id="junk", skip_classification=True)
        df = _df(good, junk)
        result = analyzed_frame(df)

        assert "good" in result["source_id"].values
        assert "junk" not in result["source_id"].values

    def test_sentiment_skipped_excluded(self):
        from src.gm_insights import analyzed_frame

        good = _make_row(source_id="good")
        skipped = _make_row(source_id="skipped", sentiment="skipped", classifier_mode="llm")
        df = _df(good, skipped)
        result = analyzed_frame(df)

        assert "good" in result["source_id"].values
        assert "skipped" not in result["source_id"].values

    def test_sentiment_error_excluded(self):
        from src.gm_insights import analyzed_frame

        good = _make_row(source_id="good")
        errored = _make_row(source_id="errored", sentiment="error", classifier_mode="llm")
        df = _df(good, errored)
        result = analyzed_frame(df)

        assert "good" in result["source_id"].values
        assert "errored" not in result["source_id"].values

    def test_empty_classifier_mode_excluded(self):
        from src.gm_insights import analyzed_frame

        good = _make_row(source_id="good")
        unclassified = _make_row(source_id="unclassified", classifier_mode="")
        df = _df(good, unclassified)
        result = analyzed_frame(df)

        assert "good" in result["source_id"].values
        assert "unclassified" not in result["source_id"].values

    def test_empty_dataframe_returns_empty(self):
        from src.gm_insights import analyzed_frame

        result = analyzed_frame(pd.DataFrame())
        assert result.empty

    def test_all_bad_rows_returns_empty(self):
        from src.gm_insights import analyzed_frame

        junk1 = _make_row(source_id="j1", skip_classification=True)
        junk2 = _make_row(source_id="j2", sentiment="skipped", classifier_mode="llm")
        junk3 = _make_row(source_id="j3", sentiment="error", classifier_mode="llm")
        df = _df(junk1, junk2, junk3)

        result = analyzed_frame(df)
        assert result.empty


# ---------------------------------------------------------------------------
# Golden fixture parity tests
# ---------------------------------------------------------------------------

class TestGoldenFixtureParity:
    """Metrics from the golden CSV must match notebook-derived expected values."""

    @pytest.fixture(scope="class")
    def fixture_df(self):
        from src.gm_insights import load_classified
        return load_classified(FIXTURES / "golden_classified.csv")

    @pytest.fixture(scope="class")
    def expected_metrics(self):
        return json.loads((FIXTURES / "golden_metrics.json").read_text())

    def test_total_rows(self, fixture_df, expected_metrics):
        assert len(fixture_df) == expected_metrics["total_rows"]

    def test_analyzed_rows(self, fixture_df, expected_metrics):
        from src.gm_insights import analyzed_frame
        analyzed = analyzed_frame(fixture_df)
        assert len(analyzed) == expected_metrics["analyzed_rows"]

    def test_skipped_rows_count(self, fixture_df, expected_metrics):
        from src.gm_insights import summary_metrics
        metrics = summary_metrics(fixture_df)
        assert metrics["skipped_rows"] == expected_metrics["skipped_rows"]

    def test_complaint_rate(self, fixture_df, expected_metrics):
        from src.gm_insights import summary_metrics
        metrics = summary_metrics(fixture_df)
        assert metrics["complaint_rate"] == expected_metrics["complaint_rate"]

    def test_negative_rate(self, fixture_df, expected_metrics):
        from src.gm_insights import summary_metrics
        metrics = summary_metrics(fixture_df)
        assert metrics["negative_rate"] == expected_metrics["negative_rate"]

    def test_competitor_rate(self, fixture_df, expected_metrics):
        from src.gm_insights import summary_metrics
        metrics = summary_metrics(fixture_df)
        assert metrics["competitor_rate"] == expected_metrics["competitor_rate"]

    def test_ev_rate(self, fixture_df, expected_metrics):
        from src.gm_insights import summary_metrics
        metrics = summary_metrics(fixture_df)
        assert metrics["ev_rate"] == expected_metrics["ev_rate"]

    def test_skip_classification_rows_not_in_analyzed(self, fixture_df):
        from src.gm_insights import analyzed_frame
        analyzed = analyzed_frame(fixture_df)
        # rows 3 and 4 are skip_classification=True in the golden fixture
        assert "row_3" not in analyzed["source_id"].values
        assert "row_4" not in analyzed["source_id"].values

    def test_skipped_sentiment_not_in_analyzed(self, fixture_df):
        from src.gm_insights import analyzed_frame
        analyzed = analyzed_frame(fixture_df)
        # row 5 has sentiment=skipped, row 6 has sentiment=error
        assert "row_5" not in analyzed["source_id"].values
        assert "row_6" not in analyzed["source_id"].values


# ---------------------------------------------------------------------------
# JSON sanitization tests
# ---------------------------------------------------------------------------

class TestJsonSanitization:
    """_sanitize must convert NaN, inf, -inf to None at any nesting depth."""

    @pytest.fixture(autouse=True)
    def import_sanitize(self):
        # Import via app module since _sanitize lives there
        import importlib
        import sys
        # Guard: if app imports fail (e.g. missing fastapi), skip gracefully
        try:
            import app as app_module
            self.sanitize = app_module._sanitize
        except Exception:
            pytest.skip("app.py could not be imported (missing dependency)")

    def test_nan_becomes_none(self):
        assert self.sanitize(float("nan")) is None

    def test_inf_becomes_none(self):
        assert self.sanitize(float("inf")) is None

    def test_neg_inf_becomes_none(self):
        assert self.sanitize(float("-inf")) is None

    def test_normal_float_unchanged(self):
        assert self.sanitize(3.14) == pytest.approx(3.14)

    def test_dict_nested_nan(self):
        result = self.sanitize({"a": float("nan"), "b": 1})
        assert result["a"] is None
        assert result["b"] == 1

    def test_list_nested_nan(self):
        result = self.sanitize([float("nan"), 2, float("inf")])
        assert result[0] is None
        assert result[1] == 2
        assert result[2] is None

    def test_deeply_nested(self):
        data = {"rows": [{"value": float("nan")}, {"value": 42.0}]}
        result = self.sanitize(data)
        assert result["rows"][0]["value"] is None
        assert result["rows"][1]["value"] == 42.0

    def test_non_float_types_pass_through(self):
        assert self.sanitize("hello") == "hello"
        assert self.sanitize(42) == 42
        assert self.sanitize(None) is None
        assert self.sanitize(True) is True

    def test_sanitized_output_is_json_serializable(self):
        data = {"value": float("nan"), "nested": {"inf": math.inf}}
        sanitized = self.sanitize(data)
        # Must not raise
        serialized = json.dumps(sanitized)
        parsed = json.loads(serialized)
        assert parsed["value"] is None
        assert parsed["nested"]["inf"] is None


# ---------------------------------------------------------------------------
# Atomic write tests
# ---------------------------------------------------------------------------

class TestAtomicSaveClassified:
    """save_classified must write via a temp file and atomically replace."""

    def test_atomic_write_produces_correct_file(self, tmp_path):
        from src.gm_insights import save_classified

        df = _df(_make_row())
        dest = tmp_path / "classified" / "classified_posts.csv"
        result = save_classified(pd.DataFrame([_make_row()]), dest)

        assert result == dest
        assert dest.exists()
        # No leftover temp file
        assert not dest.with_suffix(".tmp").exists()

    def test_written_csv_is_readable(self, tmp_path):
        from src.gm_insights import save_classified

        row = _make_row(source_id="atomic_test", sentiment="positive")
        dest = tmp_path / "out.csv"
        save_classified(pd.DataFrame([row]), dest)

        loaded = pd.read_csv(dest, dtype=str)
        assert loaded.iloc[0]["source_id"] == "atomic_test"
        assert loaded.iloc[0]["sentiment"] == "positive"

    def test_parent_dirs_created(self, tmp_path):
        from src.gm_insights import save_classified

        dest = tmp_path / "deep" / "nested" / "dir" / "classified.csv"
        save_classified(pd.DataFrame([_make_row()]), dest)
        assert dest.exists()
