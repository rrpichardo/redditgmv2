"""Plan B B0: truthful UTC time-series and trend availability contracts."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app as app_module
from src.timeseries import build_timeseries


client = TestClient(app_module.app, raise_server_exceptions=True)


def _frame(
    timestamps: list[object],
    *,
    sentiments: list[str] | None = None,
    clusters: list[object] | None = None,
) -> pd.DataFrame:
    size = len(timestamps)
    return pd.DataFrame(
        {
            "source_id": [f"row_{index}" for index in range(size)],
            "created_at_norm": timestamps,
            "sentiment": sentiments or ["negative"] * size,
            "cluster_id": clusters or [0] * size,
            "classifier_mode": ["imported"] * size,
            "skip_classification": [False] * size,
            "complaint": [1] * size,
        }
    )


def test_no_valid_timestamps_returns_stable_reason() -> None:
    result = build_timeseries(_frame([None, "not-a-date"]))

    assert result["ok"] is False
    assert result["reason_code"] == "no_valid_timestamps"
    assert result["valid_timestamp_count"] == 0
    assert result["invalid_timestamp_count"] == 2
    assert result["detail"] == "No analyzed rows have valid timestamps."


def test_one_utc_day_returns_single_day_reason() -> None:
    result = build_timeseries(
        _frame(["2026-01-01T01:00:00+00:00", "2025-12-31T22:00:00-04:00"])
    )

    assert result["ok"] is False
    assert result["reason_code"] == "single_day_no_timeseries"
    assert result["data_span_days"] == 0
    assert "one UTC calendar day" in result["detail"]


def test_two_days_use_daily_buckets_and_zero_fill_missing_day() -> None:
    result = build_timeseries(
        _frame(
            ["2026-01-01T12:00:00-04:00", "2026-01-03T00:30:00+00:00"],
            sentiments=["negative", "positive"],
        )
    )

    assert result["ok"] is True
    assert result["granularity"] == "day"
    assert result["unit"] == "negative_share_pct"
    assert result["buckets"] == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert result["total"] == [1, 0, 1]
    assert result["negative_share_pct"] == [100.0, 0.0, 0.0]
    assert result["negative"] == [1, 0, 0]
    assert result["positive"] == [0, 0, 1]


def test_fourteen_day_span_uses_monday_week_buckets() -> None:
    result = build_timeseries(
        _frame(["2026-01-01T12:00:00Z", "2026-01-15T12:00:00Z"])
    )

    assert result["ok"] is True
    assert result["granularity"] == "week"
    assert result["buckets"] == ["2025-12-29", "2026-01-05", "2026-01-12"]
    assert result["total"] == [1, 0, 1]


def test_sparse_months_zero_fill_every_week() -> None:
    result = build_timeseries(
        _frame(["2026-01-05T00:00:00Z", "2026-03-02T00:00:00Z"])
    )

    assert result["ok"] is True
    assert result["granularity"] == "week"
    assert result["buckets"][0] == "2026-01-05"
    assert result["buckets"][-1] == "2026-03-02"
    assert len(result["buckets"]) == 9
    assert sum(value > 0 for value in result["total"]) == 2


def test_mixed_offsets_normalize_across_utc_boundary() -> None:
    result = build_timeseries(
        _frame(["2026-01-01T23:30:00-05:00", "2026-01-03T00:30:00+00:00"])
    )

    assert result["ok"] is True
    assert result["buckets"] == ["2026-01-02", "2026-01-03"]


def test_malformed_cluster_ids_are_ignored_without_failing() -> None:
    result = build_timeseries(
        _frame(
            ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"],
            clusters=[None, "bad", "2", 2.5, 3],
        )
    )

    assert result["ok"] is True
    assert set(result["by_cluster"]) == {"2", "3"}
    assert result["by_cluster"]["2"] == [0, 0, 1, 0, 0]
    assert result["by_cluster"]["3"] == [0, 0, 0, 0, 1]


def test_two_distinct_timestamps_in_one_bucket_are_not_enough() -> None:
    result = build_timeseries(
        _frame(["2026-01-01T01:00:00Z", "2026-01-01T22:00:00Z"])
    )

    assert result["ok"] is False
    assert result["reason_code"] == "single_day_no_timeseries"


def test_timeseries_endpoint_returns_no_classified_data_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_module, "RUNTIME", tmp_path / "runtime")

    response = client.get("/api/trends/timeseries?tag=missing")

    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "reason_code": "no_classified_data",
        "detail": "No classified data found for this analysis.",
        "valid_timestamp_count": 0,
        "invalid_timestamp_count": 0,
    }


def test_dashboard_uses_reason_detail_granularity_and_rate_contract() -> None:
    source = (Path(__file__).parents[1] / "web" / "js" / "views" / "dashboard.js").read_text(
        encoding="utf-8"
    )

    assert "Date data required to render this chart" not in source
    assert "tsd?.detail" in source
    assert "granularity_label" in source
    assert "negative_share_pct" in source
    assert "trendEmptyState" in source


def test_dashboard_explains_empty_quadrant_and_leaderboard() -> None:
    source = (Path(__file__).parents[1] / "web" / "js" / "views" / "dashboard.js").read_text(
        encoding="utf-8"
    )

    assert "validQuadrantClusters" in source
    assert "validLeaderboardClusters" in source
    assert "Velocity needs at least 2 recent and 3 baseline records" in source
    assert "Z-score needs at least 4 periods" in source
