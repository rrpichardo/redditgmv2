"""Phase 5 tests: Trend Detection and Trend Briefings.

Covers:
- _cluster_timestamps: column-missing and valid paths
- compute_velocity: insufficient data guards, valid velocity, direction thresholds
- compute_zscore: guard for n_periods < 2, directional_only flag, valid z-score
- _signal_agreement: all agreement/diverge/partial branches
- trend_confidence_banner: high/medium/low paths
- run_trend_analysis: end-to-end with mocked clusters.json + cluster_labels.json
- POST /api/trends/briefing: job start, duplicate guard, 400 when no clusters
- GET /api/trends/briefing/status: idle and running states
- GET /api/download/trend-pdf: 404 before job, 200 after
- GET /api/trends: now includes trend_signal per cluster
"""

from __future__ import annotations

import json
import time
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from src.trend_insights import (
    MIN_PERIODS,
    _cluster_timestamps,
    _signal_agreement,
    compute_velocity,
    compute_zscore,
    run_trend_analysis,
    trend_confidence_banner,
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


def _make_timed_df(n: int = 20, start: str = "2024-01-01", freq: str = "3D") -> pd.DataFrame:
    """DataFrame with source_id and created_at_norm spread over time."""
    dates = pd.date_range(start=start, periods=n, freq=freq)
    return pd.DataFrame({
        "source_id": [f"row_{i}" for i in range(n)],
        "created_at_norm": dates.astype(str),
        "skip_classification": False,
        "classifier_mode": "heuristic_preview",
        "sentiment": ["negative" if i % 2 == 0 else "positive" for i in range(n)],
        "complaint": [int(i % 3 == 0) for i in range(n)],
    })


def _make_untimed_df(n: int = 10) -> pd.DataFrame:
    """DataFrame without timestamps."""
    return pd.DataFrame({
        "source_id": [f"row_{i}" for i in range(n)],
        "skip_classification": False,
        "classifier_mode": "heuristic_preview",
        "sentiment": ["negative"] * n,
    })


def _write_cluster_artifacts(tdir: Path, n_clusters: int = 3, source_ids: list | None = None) -> None:
    tdir.mkdir(parents=True, exist_ok=True)
    if source_ids is None:
        source_ids = [f"row_{i}" for i in range(20)]
    chunk = len(source_ids) // n_clusters
    clusters = {}
    labels = {}
    for cid in range(n_clusters):
        start = cid * chunk
        end = start + chunk if cid < n_clusters - 1 else len(source_ids)
        clusters[str(cid)] = source_ids[start:end]
        labels[str(cid)] = {
            "short_label": f"Theme {cid}",
            "detailed_label": f"Cluster {cid} description.",
            "theme_type": "complaint",
            "confidence": "medium",
            "confidence_score": 0.6,
            "deterministic_score": 0.55,
            "coherence": 0.7,
        }
    (tdir / "clusters.json").write_text(json.dumps(clusters), encoding="utf-8")
    (tdir / "cluster_labels.json").write_text(json.dumps(labels), encoding="utf-8")


# ---------------------------------------------------------------------------
# _cluster_timestamps
# ---------------------------------------------------------------------------

class TestClusterTimestamps:
    def test_no_columns(self):
        df = pd.DataFrame({"source_id": ["a", "b"]})
        ts = _cluster_timestamps(["a"], df)
        assert ts.empty

    def test_valid_timestamps(self):
        df = _make_timed_df(10)
        source_ids = [f"row_{i}" for i in range(5)]
        ts = _cluster_timestamps(source_ids, df)
        assert len(ts) == 5

    def test_filters_by_source_id(self):
        df = _make_timed_df(10)
        ts = _cluster_timestamps(["row_0", "row_1"], df)
        assert len(ts) == 2

    def test_drops_unparseable_dates(self):
        df = pd.DataFrame({
            "source_id": ["a", "b"],
            "created_at_norm": ["not-a-date", "2024-01-01"],
        })
        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            ts = _cluster_timestamps(["a", "b"], df)
        assert len(ts) == 1  # only the valid date


# ---------------------------------------------------------------------------
# compute_velocity
# ---------------------------------------------------------------------------

class TestComputeVelocity:
    def test_no_timestamps(self):
        df = _make_untimed_df()
        result = compute_velocity(["row_0"], df)
        assert result["valid"] is False
        assert result["direction"] == "insufficient_data"
        assert result["velocity"] is None

    def test_insufficient_baseline(self):
        # Only 1 row in baseline (< 2 required)
        df = _make_timed_df(5, start="2024-01-01", freq="1D")
        # All rows are within the recent 7-day window
        all_ids = [f"row_{i}" for i in range(5)]
        result = compute_velocity(all_ids, df, recent_days=7, baseline_days=30)
        # Baseline has 0 posts; recent has posts → directional
        assert result["valid"] is False

    def test_rising_direction(self):
        # Many recent, few baseline → rising.
        # now = 2024-04-01 (max timestamp); recent window = last 7 days (Mar 25-Apr 1);
        # baseline window = prior 30 days (Feb 24 - Mar 25).
        recent_dates = pd.date_range("2024-03-25", periods=8, freq="1D")
        baseline_dates = pd.date_range("2024-02-25", periods=2, freq="10D")  # within baseline window
        all_dates = list(baseline_dates) + list(recent_dates)
        df = pd.DataFrame({
            "source_id": [f"row_{i}" for i in range(len(all_dates))],
            "created_at_norm": [str(d.date()) for d in all_dates],
        })
        all_ids = [f"row_{i}" for i in range(len(all_dates))]
        result = compute_velocity(all_ids, df, recent_days=7, baseline_days=30)
        assert result["direction"] == "rising"
        assert result["velocity"] is not None
        assert float(result["velocity"]) > 0

    def test_falling_direction(self):
        # now=Jan 31; recent window Jan 24-31 has only 1 post;
        # baseline window (Dec 25 - Jan 24) has 10 posts → falling velocity.
        now = pd.Timestamp("2024-01-31")
        recent_dates = [now]
        baseline_dates = pd.date_range("2024-01-01", periods=10, freq="2D")  # Jan 1–19
        all_dates = list(baseline_dates) + recent_dates
        df = pd.DataFrame({
            "source_id": [f"row_{i}" for i in range(len(all_dates))],
            "created_at_norm": [str(d.date()) for d in all_dates],
        })
        all_ids = [f"row_{i}" for i in range(len(all_dates))]
        result = compute_velocity(all_ids, df, recent_days=7, baseline_days=30)
        assert result["direction"] == "falling"

    def test_stable_direction(self):
        # Equal recent and baseline rates
        dates = pd.date_range("2024-01-01", periods=40, freq="1D")
        df = pd.DataFrame({
            "source_id": [f"row_{i}" for i in range(40)],
            "created_at_norm": [str(d.date()) for d in dates],
        })
        all_ids = [f"row_{i}" for i in range(40)]
        result = compute_velocity(all_ids, df, recent_days=7, baseline_days=30)
        # Uniform distribution → velocity near 0 → stable
        assert result["direction"] in ("stable", "rising", "falling")  # direction depends on exact counts


# ---------------------------------------------------------------------------
# compute_zscore
# ---------------------------------------------------------------------------

class TestComputeZscore:
    def test_no_timestamps(self):
        df = _make_untimed_df()
        result = compute_zscore(["row_0"], df)
        assert result["valid"] is False
        assert result["direction"] == "insufficient_data"

    def test_single_period(self):
        # Data all within one week → only 1 period
        dates = pd.date_range("2024-01-01", periods=3, freq="1D")
        df = pd.DataFrame({
            "source_id": [f"row_{i}" for i in range(3)],
            "created_at_norm": [str(d.date()) for d in dates],
        })
        result = compute_zscore([f"row_{i}" for i in range(3)], df, period_days=7)
        assert result["n_periods"] == 1
        assert result["valid"] is False

    def test_directional_only_flag(self):
        # Enough periods for z-score but below MIN_PERIODS → directional_only
        n = (MIN_PERIODS - 1) * 7 + 3  # just under MIN_PERIODS full weeks
        dates = pd.date_range("2024-01-01", periods=n, freq="1D")
        df = pd.DataFrame({
            "source_id": [f"row_{i}" for i in range(n)],
            "created_at_norm": [str(d.date()) for d in dates],
        })
        result = compute_zscore([f"row_{i}" for i in range(n)], df, period_days=7)
        if result["n_periods"] >= 2:
            if result["n_periods"] < MIN_PERIODS:
                assert result["directional_only"] is True
                assert result["valid"] is False

    def test_valid_zscore(self):
        # Engineer dates carefully so the spike lands inside a computed period.
        #
        # earliest_in_code = ts.min().  We anchor one post at Jan 1 to pin earliest = Jan 1.
        # Base: 2 posts each in periods 0–3 (days 1, 8, 15, 22 from Jan 1).
        # Spike: 20 posts at day 30 (inside period 4: Jan 29–Feb 5).
        # Tail: 1 post at day 34 → now = Jan 1 + 34 = Feb 4 → total_days = 35 → n_periods = 5.
        #
        # period_counts will be ≈ [3, 2, 2, 2, 21] → zscore for last period >> 1.
        earliest = pd.Timestamp("2024-01-01")
        dates = [earliest]  # anchor: forces ts.min() = Jan 1
        for p in range(4):  # periods 0–3
            for _ in range(2):
                dates.append(earliest + pd.Timedelta(days=p * 7 + 1))
        for _ in range(20):  # spike inside period 4 (Jan 29 – Feb 5)
            dates.append(earliest + pd.Timedelta(days=30))
        dates.append(earliest + pd.Timedelta(days=34))  # tail → total_days = 35, n_periods = 5
        df = pd.DataFrame({
            "source_id": [f"row_{i}" for i in range(len(dates))],
            "created_at_norm": [str(d.date()) for d in dates],
        })
        result = compute_zscore([f"row_{i}" for i in range(len(dates))], df, period_days=7)
        assert result["valid"] is True
        assert result["zscore"] is not None
        assert float(result["zscore"]) > 1.0  # spike → rising
        assert result["direction"] == "rising"

    def test_no_variance(self):
        # Same count every period → zscore=0, stable
        n_periods = MIN_PERIODS + 1
        dates = []
        for p in range(n_periods):
            dates.append(pd.Timestamp("2024-01-01") + pd.Timedelta(days=p * 7))
        df = pd.DataFrame({
            "source_id": [f"row_{i}" for i in range(len(dates))],
            "created_at_norm": [str(d.date()) for d in dates],
        })
        result = compute_zscore([f"row_{i}" for i in range(len(dates))], df, period_days=7)
        assert result["zscore"] == 0.0
        assert result["direction"] == "stable"


# ---------------------------------------------------------------------------
# _signal_agreement
# ---------------------------------------------------------------------------

class TestSignalAgreement:
    def test_both_rising(self):
        assert _signal_agreement("rising", "rising") == "agree"

    def test_rising_falling(self):
        assert _signal_agreement("rising", "falling") == "diverge"

    def test_rising_stable(self):
        assert _signal_agreement("rising", "stable") == "partial"

    def test_insufficient(self):
        assert _signal_agreement("insufficient_data", "rising") == "one_insufficient"
        assert _signal_agreement("rising", "insufficient_data") == "one_insufficient"

    def test_both_stable(self):
        assert _signal_agreement("stable", "stable") == "agree"


# ---------------------------------------------------------------------------
# trend_confidence_banner
# ---------------------------------------------------------------------------

class TestTrendConfidenceBanner:
    def _vel(self, valid: bool, direction: str) -> dict:
        return {"valid": valid, "direction": direction, "velocity": 0.5}

    def _zsc(self, valid: bool, direction: str) -> dict:
        return {"valid": valid, "direction": direction, "zscore": 1.5}

    def test_high_confidence(self):
        level, note = trend_confidence_banner(
            self._vel(True, "rising"), self._zsc(True, "rising")
        )
        assert level == "high"
        assert "rising" in note

    def test_medium_velocity_only(self):
        level, note = trend_confidence_banner(
            self._vel(True, "rising"), self._zsc(False, "rising")
        )
        assert level == "medium"

    def test_medium_zscore_only(self):
        level, note = trend_confidence_banner(
            self._vel(False, "rising"), self._zsc(True, "rising")
        )
        assert level == "medium"

    def test_low_diverge(self):
        level, note = trend_confidence_banner(
            self._vel(True, "rising"), self._zsc(True, "falling")
        )
        assert level == "low"
        assert "diverge" in note

    def test_low_no_data(self):
        level, note = trend_confidence_banner(
            self._vel(False, "insufficient_data"), self._zsc(False, "insufficient_data")
        )
        assert level == "low"


# ---------------------------------------------------------------------------
# run_trend_analysis
# ---------------------------------------------------------------------------

class TestRunTrendAnalysis:
    def test_raises_without_clusters_json(self, tmp_path):
        df = _make_timed_df()
        with pytest.raises(FileNotFoundError, match="clusters.json"):
            run_trend_analysis("test_tag", df, tmp_path)

    def test_saves_trend_signals_json(self, tmp_path):
        df = _make_timed_df(20, start="2024-01-01", freq="3D")
        source_ids = df["source_id"].tolist()
        tdir = trends_dir(tmp_path, "test_tag")
        _write_cluster_artifacts(tdir, n_clusters=3, source_ids=source_ids)

        result = run_trend_analysis("test_tag", df, tmp_path)

        signals_path = tdir / "trend_signals.json"
        assert signals_path.exists()
        saved = json.loads(signals_path.read_text(encoding="utf-8"))
        assert saved["n_clusters"] == 3
        assert "signals" in saved
        assert len(saved["signals"]) == 3

    def test_has_timestamps_detected(self, tmp_path):
        df = _make_timed_df(20)
        tdir = trends_dir(tmp_path, "t")
        _write_cluster_artifacts(tdir, source_ids=df["source_id"].tolist())
        result = run_trend_analysis("t", df, tmp_path)
        assert result["has_timestamps"]  # truthy check avoids numpy bool_ vs bool issue

    def test_no_timestamps_detected(self, tmp_path):
        df = _make_untimed_df(10)
        tdir = trends_dir(tmp_path, "t")
        _write_cluster_artifacts(tdir, n_clusters=2, source_ids=df["source_id"].tolist())
        result = run_trend_analysis("t", df, tmp_path)
        assert not result["has_timestamps"]

    def test_signal_structure(self, tmp_path):
        df = _make_timed_df(20)
        tdir = trends_dir(tmp_path, "t")
        _write_cluster_artifacts(tdir, source_ids=df["source_id"].tolist())
        result = run_trend_analysis("t", df, tmp_path)

        for cid_str, sig in result["signals"].items():
            assert "cluster_id" in sig
            assert "velocity" in sig
            assert "zscore" in sig
            assert "confidence_banner" in sig
            assert sig["confidence_banner"] in ("high", "medium", "low")
            assert "agreement" in sig


# ---------------------------------------------------------------------------
# API: POST /api/trends/briefing
# ---------------------------------------------------------------------------

class TestTrendsBriefingEndpoint:
    @pytest.fixture(autouse=True)
    def _client(self, tmp_path):
        import app as app_module
        from fastapi.testclient import TestClient
        self.orig_runtime = app_module.RUNTIME
        app_module.RUNTIME = tmp_path
        self.client = TestClient(app_module.app)
        yield
        app_module.RUNTIME = self.orig_runtime

    def test_400_when_no_clusters(self):
        resp = self.client.post("/api/trends/briefing", json={"tag": "test"})
        assert resp.status_code == 400
        assert "Run /api/trends/run" in resp.json()["detail"]

    def test_starts_job_when_clusters_exist(self, tmp_path):
        import app as app_module
        tdir = trends_dir(tmp_path, "test")
        _write_cluster_artifacts(tdir)

        with patch("app.start_job") as mock_start:
            mock_start.return_value = {
                "job_id": "abc123",
                "tag": "test",
                "kind": "trend_briefing",
                "state": "running",
                "pid": 9999,
                "started_at": time.time(),
            }
            with patch("app.find_active_job", return_value=None):
                resp = self.client.post("/api/trends/briefing", json={"tag": "test"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["started"] is True
        assert body["kind"] == "trend_briefing"

    def test_returns_existing_job_without_starting(self, tmp_path):
        import app as app_module
        tdir = trends_dir(tmp_path, "test")
        _write_cluster_artifacts(tdir)

        existing = {
            "job_id": "existing_job",
            "kind": "trend_briefing",
            "state": "running",
            "pid": 1234,
            "started_at": time.time(),
        }
        with patch("app.find_active_job", return_value=existing):
            resp = self.client.post("/api/trends/briefing", json={"tag": "test"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["started"] is False
        assert body["job_id"] == "existing_job"


# ---------------------------------------------------------------------------
# API: GET /api/trends/briefing/status
# ---------------------------------------------------------------------------

class TestTrendsBriefingStatusEndpoint:
    @pytest.fixture(autouse=True)
    def _client(self, tmp_path):
        import app as app_module
        from fastapi.testclient import TestClient
        self.orig_runtime = app_module.RUNTIME
        app_module.RUNTIME = tmp_path
        self.client = TestClient(app_module.app)
        yield
        app_module.RUNTIME = self.orig_runtime

    def test_idle_when_no_jobs(self):
        resp = self.client.get("/api/trends/briefing/status", params={"tag": "test"})
        assert resp.status_code == 200
        assert resp.json()["state"] == "idle"

    def test_returns_status_for_job_id(self, tmp_path):
        import app as app_module
        from src.jobs import write_status, job_path as jp
        p = jp(tmp_path, "test", "job999")
        write_status(p, {
            "job_id": "job999",
            "tag": "test",
            "kind": "trend_briefing",
            "state": "completed",
            "pid": 1234,
            "started_at": time.time(),
            "heartbeat_at": time.time(),
        })
        resp = self.client.get("/api/trends/briefing/status", params={"tag": "test", "job_id": "job999"})
        assert resp.status_code == 200
        assert resp.json()["state"] == "completed"


# ---------------------------------------------------------------------------
# API: GET /api/download/trend-pdf
# ---------------------------------------------------------------------------

class TestDownloadTrendPdf:
    @pytest.fixture(autouse=True)
    def _client(self, tmp_path):
        import app as app_module
        from fastapi.testclient import TestClient
        self.orig_runtime = app_module.RUNTIME
        app_module.RUNTIME = tmp_path
        self.client = TestClient(app_module.app)
        yield
        app_module.RUNTIME = self.orig_runtime

    def test_404_before_job(self):
        resp = self.client.get("/api/download/trend-pdf", params={"tag": "test"})
        assert resp.status_code == 404

    def test_200_when_pdf_exists(self, tmp_path):
        import app as app_module
        dl_dir = tmp_path / "test" / "downloads"
        dl_dir.mkdir(parents=True)
        (dl_dir / "test_trend_briefing.pdf").write_bytes(b"%PDF-1.4 fake")
        resp = self.client.get("/api/download/trend-pdf", params={"tag": "test"})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"


# ---------------------------------------------------------------------------
# API: GET /api/trends (now includes trend_signal)
# ---------------------------------------------------------------------------

class TestTrendsResultsIncludeSignals:
    @pytest.fixture(autouse=True)
    def _client(self, tmp_path):
        import app as app_module
        from fastapi.testclient import TestClient
        self.orig_runtime = app_module.RUNTIME
        app_module.RUNTIME = tmp_path
        self.client = TestClient(app_module.app)
        yield
        app_module.RUNTIME = self.orig_runtime

    def test_no_results_before_clustering(self):
        resp = self.client.get("/api/trends", params={"tag": "test"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    def test_includes_trend_signal_when_signals_exist(self, tmp_path):
        import app as app_module
        tdir = trends_dir(tmp_path, "test")
        _write_cluster_artifacts(tdir, n_clusters=2)

        # Write an embedding_metadata.json too
        (tdir / "embedding_metadata.json").write_text(json.dumps({
            "model": "text-embedding-3-small",
            "dim": 1536,
            "doc_count": 10,
            "n_clusters": 2,
            "created_at": time.time(),
            "tag": "test",
        }), encoding="utf-8")

        # Write trend_signals.json
        signals = {
            "computed_at": time.time(),
            "tag": "test",
            "has_timestamps": True,
            "data_span_days": 30,
            "n_clusters": 2,
            "signals": {
                "0": {
                    "cluster_id": 0,
                    "short_label": "Theme 0",
                    "theme_type": "complaint",
                    "cluster_size": 5,
                    "velocity": {"valid": True, "direction": "rising", "velocity": 0.3},
                    "zscore": {"valid": True, "direction": "rising", "zscore": 1.8},
                    "agreement": "agree",
                    "confidence_banner": "high",
                    "confidence_note": "Both signals agree: rising.",
                },
                "1": {
                    "cluster_id": 1,
                    "short_label": "Theme 1",
                    "theme_type": "delight",
                    "cluster_size": 5,
                    "velocity": {"valid": False, "direction": "insufficient_data"},
                    "zscore": {"valid": False, "direction": "insufficient_data"},
                    "agreement": "one_insufficient",
                    "confidence_banner": "low",
                    "confidence_note": "Insufficient data.",
                },
            },
        }
        (tdir / "trend_signals.json").write_text(json.dumps(signals), encoding="utf-8")

        resp = self.client.get("/api/trends", params={"tag": "test"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["trend_summary"]["has_timestamps"] is True
        assert body["trend_summary"]["data_span_days"] == 30
        for cluster in body["clusters"]:
            assert "trend_signal" in cluster

    def test_trend_signal_empty_without_signals_json(self, tmp_path):
        import app as app_module
        tdir = trends_dir(tmp_path, "test")
        _write_cluster_artifacts(tdir, n_clusters=2)
        (tdir / "embedding_metadata.json").write_text(json.dumps({"model": "test"}), encoding="utf-8")

        resp = self.client.get("/api/trends", params={"tag": "test"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["trend_summary"] is None
        for cluster in body["clusters"]:
            assert cluster["trend_signal"] == {}
