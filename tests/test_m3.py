"""Tests for M3 Dashboard: /api/trends/timeseries endpoint."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

_tmp = tempfile.mkdtemp()

import app as app_module  # noqa: E402

app_module.RUNTIME = Path(_tmp)

from app import app  # noqa: E402

client = TestClient(app, raise_server_exceptions=True)

TAG = "m3_test"

# All columns required for classify_upload_kind() to return "classified" so that
# normalize_reddit_frame() picks up created_at_norm and target_text correctly.
_CLASSIFIED_DEFAULTS: dict[str, object] = {
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
    "vehicle_mentioned": "unknown",
    "comment_type": "off_topic_noise",
    "competitor_brand": "none",
    "issue_severity": "low",
    "description": "test row",
}


def _make_classified(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal but valid classified DataFrame from compact row dicts.

    Merges each row dict with _CLASSIFIED_DEFAULTS so all EXPECTED_KEYS are present.
    classify_upload_kind() then returns 'classified' and normalize_reddit_frame()
    correctly reads created_at_norm and target_text.
    """
    full_rows = [{**_CLASSIFIED_DEFAULTS, **r} for r in rows]
    return pd.DataFrame(full_rows)


def _write_classified(df: pd.DataFrame) -> None:
    path = Path(_tmp) / TAG / "classified" / "classified_posts.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def test_timeseries_returns_weekly_buckets():
    """Endpoint buckets rows by ISO week and counts sentiment correctly."""
    df = _make_classified([
        {"source_id": "r1", "created_at_norm": "2024-01-10 12:00:00", "sentiment": "negative",
         "classifier_mode": "llm", "target_text": "Serious recurring brake problem on the vehicle."},
        {"source_id": "r2", "created_at_norm": "2024-01-11 09:00:00", "sentiment": "positive",
         "classifier_mode": "llm", "target_text": "Really happy with the performance so far."},
        {"source_id": "r3", "created_at_norm": "2024-01-18 10:00:00", "sentiment": "negative",
         "classifier_mode": "llm", "target_text": "Another brake issue reported this week."},
        {"source_id": "r4", "created_at_norm": "2024-01-25 11:00:00", "sentiment": "neutral",
         "classifier_mode": "llm", "target_text": "No strong opinion on this model honestly."},
    ])
    _write_classified(df)
    r = client.get(f"/api/trends/timeseries?tag={TAG}")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert len(data["buckets"]) >= 3  # 2024-W02, 2024-W03, 2024-W04
    assert sum(data["negative"]) == 2
    assert sum(data["positive"]) == 1
    assert sum(data["neutral"]) == 1
    assert "by_cluster" in data


def test_timeseries_no_timestamp_column_returns_not_ok():
    """Returns ok=False when classified CSV has no usable timestamp data."""
    # Use a minimal CSV that has no created_at_norm column at all.
    # normalize_reddit_frame produces NaT for all timestamps → ok=False.
    df = pd.DataFrame({
        "source_id": ["r1", "r2"],
        "sentiment": ["negative", "positive"],
        "classifier_mode": ["llm", "llm"],
        "skip_classification": [False, False],
    })
    _write_classified(df)
    r = client.get(f"/api/trends/timeseries?tag={TAG}")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert "timestamp" in data["detail"].lower()


def test_timeseries_per_cluster_counts():
    """by_cluster contains weekly counts keyed by string cluster_id."""
    df = _make_classified([
        {"source_id": "r1", "created_at_norm": "2024-01-10 12:00:00", "sentiment": "negative",
         "classifier_mode": "llm", "cluster_id": 0,
         "target_text": "Brake pedal feels spongy when stopping hard."},
        {"source_id": "r2", "created_at_norm": "2024-01-10 13:00:00", "sentiment": "negative",
         "classifier_mode": "llm", "cluster_id": 1,
         "target_text": "Infotainment screen freezes randomly each day."},
        {"source_id": "r3", "created_at_norm": "2024-01-18 10:00:00", "sentiment": "positive",
         "classifier_mode": "llm", "cluster_id": 0,
         "target_text": "Smooth acceleration and quiet cabin overall."},
        {"source_id": "r4", "created_at_norm": "2024-01-18 11:00:00", "sentiment": "neutral",
         "classifier_mode": "llm", "cluster_id": 1,
         "target_text": "Neutral experience, nothing stands out here."},
    ])
    _write_classified(df)
    r = client.get(f"/api/trends/timeseries?tag={TAG}")
    data = r.json()
    assert data["ok"] is True
    bc = data["by_cluster"]
    assert "0" in bc and "1" in bc
    assert sum(bc["0"]) == 2
    assert sum(bc["1"]) == 2


def test_timeseries_single_week_returns_not_ok():
    """Returns ok=False when all rows fall within a single calendar week."""
    df = _make_classified([
        {"source_id": "r1", "created_at_norm": "2024-01-10 08:00:00", "sentiment": "negative"},  # all in 2024-W02
        {"source_id": "r2", "created_at_norm": "2024-01-11 09:00:00", "sentiment": "positive"},
        {"source_id": "r3", "created_at_norm": "2024-01-12 10:00:00", "sentiment": "neutral"},
    ])
    _write_classified(df)
    r = client.get(f"/api/trends/timeseries?tag={TAG}")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False


def test_timeseries_missing_classified_returns_not_ok():
    """Returns ok=False when no classified file exists for the tag."""
    r = client.get("/api/trends/timeseries?tag=nonexistent_tag_xyz")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
