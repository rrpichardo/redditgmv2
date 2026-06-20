"""Plan B B4/B5: complaint presentation metadata and UI lookback removal."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.charts import build_chart_payload
from src.gm_insights import complaint_chart_presentation, complaint_summary


ROOT = Path(__file__).parents[1]


def _complaint_frame() -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {
                "source_id": "a", "target_text": "A complaint with enough detail", "classifier_mode": "imported",
                "skip_classification": False, "sentiment": "negative", "complaint": 1,
                "top_complaint_category": "not_applicable",
                "multi_complaint_categories": "engine_lifter_afm_dod, not_applicable",
            },
            {
                "source_id": "b", "target_text": "Another complaint with detail", "classifier_mode": "imported",
                "skip_classification": False, "sentiment": "negative", "complaint": 1,
                "top_complaint_category": "other", "multi_complaint_categories": "none",
            },
            {
                "source_id": "c", "target_text": "Third complaint with enough detail", "classifier_mode": "imported",
                "skip_classification": False, "sentiment": "negative", "complaint": 1,
                "top_complaint_category": "", "multi_complaint_categories": "unknown",
            },
            {
                "source_id": "d", "target_text": "Fourth complaint with enough detail", "classifier_mode": "imported",
                "skip_classification": False, "sentiment": "negative", "complaint": 1,
                "top_complaint_category": "not_applicable", "multi_complaint_categories": "",
            },
        ]
    )
    frame["created_at_norm"] = "2026-01-01"
    frame["score_norm"] = 0
    return frame


def test_complaint_presentation_excludes_primary_and_secondary_sentinels() -> None:
    result = complaint_chart_presentation(_complaint_frame())

    assert result["items"] == [
        {"theme": "engine_lifter_afm_dod", "count": 1, "pct": 50.0},
        {"theme": "other", "count": 1, "pct": 50.0},
    ]
    assert result["counts"] == {
        "total_complaints": 4,
        "applicable": 2,
        "excluded": 2,
    }


def test_raw_complaint_analytics_remain_unchanged() -> None:
    raw = complaint_summary(_complaint_frame())

    assert "not_applicable" in raw["theme"].tolist()
    assert int(raw.loc[raw["theme"] == "not_applicable", "count"].iloc[0]) == 2


def test_chart_payload_exposes_filtered_rows_and_counts() -> None:
    payload = build_chart_payload(_complaint_frame())

    assert payload["chart_data"]["complaints"] == [
        {"theme": "engine_lifter_afm_dod", "count": 1, "pct": 50.0},
        {"theme": "other", "count": 1, "pct": 50.0},
    ]
    assert payload["chart_meta"]["complaints"] == {
        "total_complaints": 4,
        "applicable": 2,
        "excluded": 2,
    }


def test_chart_payload_has_honest_empty_state_when_all_complaints_are_excluded() -> None:
    frame = _complaint_frame().iloc[[3]].copy()
    payload = build_chart_payload(frame)

    assert payload["chart_data"]["complaints"] == []
    assert payload["chart_meta"]["complaints"]["excluded"] == 1
    assert payload["chart_specs"]["complaints"]["fallback"] == "No applicable complaint themes in this view."


def test_since_days_is_absent_from_browser_controls_and_requests() -> None:
    collect = (ROOT / "web/js/views/collect.js").read_text(encoding="utf-8")
    settings = (ROOT / "web/js/views/settings.js").read_text(encoding="utf-8")

    for source in (collect, settings):
        assert "since_days" not in source
        assert "sinceDays" not in source
        assert "Since days" not in source
        assert "Lookback window" not in source


def test_backend_documents_dormant_legacy_since_days_support() -> None:
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert '"--since-days"' in app_source
    assert "legacy compatibility" in app_source.lower()
    assert "since_days" in readme
    assert "legacy" in readme.lower()
