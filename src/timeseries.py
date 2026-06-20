"""Truthful UTC time-series aggregation for analyzed Reddit rows."""

from __future__ import annotations

import math
from typing import Any

import pandas as pd


def _unavailable(
    reason_code: str,
    detail: str,
    *,
    valid_count: int,
    invalid_count: int,
    data_span_days: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "reason_code": reason_code,
        "detail": detail,
        "valid_timestamp_count": valid_count,
        "invalid_timestamp_count": invalid_count,
    }
    if data_span_days is not None:
        payload["data_span_days"] = data_span_days
    return payload


def _cluster_id(value: Any) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        return None
    return int(number)


def build_timeseries(frame: pd.DataFrame) -> dict[str, Any]:
    """Build an adaptive, zero-filled UTC sentiment time series.

    Daily buckets are used for spans shorter than fourteen days. Longer spans
    use Monday-based weekly buckets. The primary measure is negative share, so
    collecting more rows does not masquerade as rising prevalence.
    """
    if frame.empty or "created_at_norm" not in frame:
        return _unavailable(
            "no_valid_timestamps",
            "No analyzed rows have valid timestamps.",
            valid_count=0,
            invalid_count=len(frame),
        )

    timestamps = pd.to_datetime(frame["created_at_norm"], errors="coerce", utc=True)
    valid_mask = timestamps.notna()
    valid_count = int(valid_mask.sum())
    invalid_count = int((~valid_mask).sum())
    if not valid_count:
        return _unavailable(
            "no_valid_timestamps",
            "No analyzed rows have valid timestamps.",
            valid_count=0,
            invalid_count=invalid_count,
        )

    valid = frame.loc[valid_mask].copy()
    valid["_ts"] = timestamps.loc[valid_mask]
    utc_days = valid["_ts"].dt.floor("D").dt.tz_localize(None)
    span_days = int((utc_days.max() - utc_days.min()).days)
    if span_days == 0:
        return _unavailable(
            "single_day_no_timeseries",
            "All valid timestamps fall on one UTC calendar day, so a trend line would be misleading.",
            valid_count=valid_count,
            invalid_count=invalid_count,
            data_span_days=0,
        )

    granularity = "day" if span_days < 14 else "week"
    if granularity == "day":
        valid["_bucket"] = utc_days
        bucket_index = pd.date_range(utc_days.min(), utc_days.max(), freq="D")
    else:
        week_starts = utc_days - pd.to_timedelta(utc_days.dt.weekday, unit="D")
        valid["_bucket"] = week_starts
        bucket_index = pd.date_range(week_starts.min(), week_starts.max(), freq="7D")

    total = valid.groupby("_bucket").size().reindex(bucket_index, fill_value=0).astype(int)
    nonempty_bucket_count = int((total > 0).sum())
    if nonempty_bucket_count < 2:
        return _unavailable(
            "insufficient_nonempty_buckets",
            "At least two UTC time buckets with data are required to draw a trend.",
            valid_count=valid_count,
            invalid_count=invalid_count,
            data_span_days=span_days,
        )

    sentiment = valid.get("sentiment", pd.Series("neutral", index=valid.index)).fillna("neutral").astype(str)
    sentiment_counts = (
        valid.assign(_sentiment=sentiment)
        .groupby(["_bucket", "_sentiment"])
        .size()
        .unstack(fill_value=0)
        .reindex(bucket_index, fill_value=0)
    )

    def counts(label: str) -> list[int]:
        if label not in sentiment_counts:
            return [0] * len(bucket_index)
        return [int(value) for value in sentiment_counts[label].tolist()]

    negative = counts("negative")
    positive = counts("positive")
    neutral = counts("neutral")
    total_values = [int(value) for value in total.tolist()]
    negative_share = [
        round(negative[index] / value * 100, 1) if value else 0.0
        for index, value in enumerate(total_values)
    ]

    by_cluster: dict[str, list[int]] = {}
    if "cluster_id" in valid:
        valid["_cluster"] = valid["cluster_id"].map(_cluster_id)
        clustered = valid.dropna(subset=["_cluster"]).copy()
        if not clustered.empty:
            clustered["_cluster"] = clustered["_cluster"].astype(int)
            cluster_counts = (
                clustered.groupby(["_bucket", "_cluster"])
                .size()
                .unstack(fill_value=0)
                .reindex(bucket_index, fill_value=0)
            )
            for cluster in sorted(cluster_counts.columns):
                by_cluster[str(int(cluster))] = [
                    int(value) for value in cluster_counts[cluster].tolist()
                ]

    return {
        "ok": True,
        "reason_code": None,
        "detail": "Negative share by UTC day." if granularity == "day" else "Negative share by UTC week.",
        "granularity": granularity,
        "granularity_label": "daily" if granularity == "day" else "weekly",
        "unit": "negative_share_pct",
        "unit_label": "negative share",
        "collection_volume_caveat": "Rates reduce collection-volume bias; inspect bucket totals alongside shares.",
        "valid_timestamp_count": valid_count,
        "invalid_timestamp_count": invalid_count,
        "data_span_days": span_days,
        "nonempty_bucket_count": nonempty_bucket_count,
        "buckets": [value.strftime("%Y-%m-%d") for value in bucket_index],
        "total": total_values,
        "negative_share_pct": negative_share,
        "negative": negative,
        "positive": positive,
        "neutral": neutral,
        "by_cluster": by_cluster,
    }
