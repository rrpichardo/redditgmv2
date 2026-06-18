"""Chart contract: declarative specs (rendering rules) + data builders.

chart_specs  — one entry per chart id, describes HOW to render (type, sort,
               color_map, value_format, minimum_rows, fallback, etc.).
chart_data   — one entry per chart id, the actual records/matrix to render.

Heavy matrices (category_by_model, cooccurrence) are marked lazy=True and
are served from /api/charts/detail rather than /api/run to keep the main
response light.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from src.gm_insights import (
    MIN_CELL,
    all_complaint_mentions,
    analyzed_frame,
    category_heatmap,
    cooccurrence,
    comment_type_dist,
    complaint_by_model_table,
    complaint_summary,
    competitor_breakdown_detail,
    engagement_breakdown,
    engagement_weighted_themes,
    ev_comparison,
    flag_summary,
    priority_matrix,
    sentiment_by_model_table,
    severity_by_model,
    subreddit_breakdown,
    value_counts_df,
    vehicle_breakdown,
)


# ---------------------------------------------------------------------------
# Chart specs — one entry per logical chart
# ---------------------------------------------------------------------------
# Each spec describes presentation rules consumed by the browser renderer
# and (later) the matplotlib/PDF export layer.
#
# Fields:
#   type          — bar | grouped_bar | stacked_bar | stacked_bar_100 |
#                   scatter | heatmap
#   sort          — value_desc | label | total_desc | negative_desc | (omit = no sort)
#   normalize     — True  → normalize rows to 100% before rendering
#                   "row" → same but named for heatmap context
#   series_order  — preferred category order for stacked/grouped charts
#   color_map     — {label: hex} for deterministic coloring
#   value_format  — count | pct | count_pct | score | correlation | mixed
#   minimum_rows  — hide the chart (show fallback) if data has fewer rows
#   fallback      — message shown when minimum_rows not met
#   quality_note  — inline note about suppressed cells / small-n warning
#   lazy          — True → data not included in /api/run; use endpoint below
#   endpoint      — URL to fetch lazy data from
#   x_field / y_field / label_field / color_field — scatter-specific keys

CHART_SPECS: dict[str, dict[str, Any]] = {
    # --- High-level KPIs (no MIN_CELL — always show) ---
    "sentiment": {
        "type": "bar",
        "x_field": "sentiment",
        "y_field": "count",
        "sort": "value_desc",
        "color_map": {
            "positive": "#22c55e",
            "negative": "#ef4444",
            "neutral": "#94a3b8",
        },
        "value_format": "count",
        "minimum_rows": 1,
        "fallback": "No sentiment data for this filter.",
    },
    "flags": {
        "type": "bar",
        "x_field": "flag",
        "y_field": "count",
        "sort": "value_desc",
        "value_format": "count",
        "minimum_rows": 1,
        "fallback": "No signal flags yet.",
    },
    "severity_summary": {
        "type": "bar",
        "x_field": "severity",
        "y_field": "count",
        "sort": "value_desc",
        "series_order": ["critical", "major", "minor", "none"],
        "color_map": {
            "critical": "#dc2626",
            "major": "#f59e0b",
            "minor": "#3b82f6",
            "none": "#94a3b8",
        },
        "value_format": "count",
        "minimum_rows": 1,
        "fallback": "No severity data yet.",
    },
    "comment_type": {
        "type": "bar",
        "x_field": "comment_type",
        "y_field": "count",
        "sort": "value_desc",
        "value_format": "count_pct",
        "minimum_rows": 1,
        "fallback": "No comment type data yet.",
    },
    "engagement": {
        "type": "bar",
        "x_field": "level",
        "y_field": "count",
        "sort": "value_desc",
        "series_order": ["high", "medium", "low", "unknown"],
        "color_map": {
            "high": "#1e40af",
            "medium": "#3b82f6",
            "low": "#93c5fd",
            "unknown": "#cbd5e1",
        },
        "value_format": "count_pct",
        "minimum_rows": 1,
        "fallback": "No engagement data yet.",
    },
    # --- Complaint themes ---
    "complaints": {
        "type": "bar",
        "x_field": "theme",
        "y_field": "count",
        "sort": "value_desc",
        "value_format": "count",
        "minimum_rows": 1,
        "fallback": "No complaint themes yet.",
    },
    "all_complaint_mentions": {
        "type": "bar",
        "x_field": "theme",
        "y_field": "count",
        "sort": "value_desc",
        "value_format": "count",
        "minimum_rows": 1,
        "fallback": "No complaint mentions yet.",
    },
    "engagement_weighted_themes": {
        "type": "bar",
        "x_field": "theme",
        "y_field": "weighted_score",
        "sort": "value_desc",
        "value_format": "score",
        "minimum_rows": 1,
        "fallback": "No engagement-weighted theme data yet.",
    },
    # --- EV / competitor ---
    "ev": {
        "type": "grouped_bar",
        "x_field": "powertrain",
        "series_keys": ["complaint_rate_pct", "reliability_concern_pct", "software_tech_issue_pct"],
        "series_order": ["EV topic", "Non-EV topic"],
        "value_format": "pct",
        "minimum_rows": 1,
        "fallback": "No EV comparison data yet.",
    },
    "competitor_breakdown": {
        "type": "grouped_bar",
        "x_field": "competitor_brand",
        "series_keys": ["count", "complaint_rate_pct", "negative_pct"],
        "sort": "value_desc",
        "value_format": "count",
        "minimum_rows": 1,
        "fallback": "No competitor data yet.",
    },
    # --- Subreddit ---
    "subreddit": {
        "type": "bar",
        "x_field": "subreddit_norm",
        "y_field": "comment_count",
        "sort": "value_desc",
        "value_format": "count",
        "minimum_rows": MIN_CELL,
        "fallback": f"No subreddits with ≥{MIN_CELL} analyzed rows.",
        "quality_note": f"Subreddits with fewer than {MIN_CELL} rows are hidden.",
    },
    # --- Priority scatter ---
    "priority": {
        "type": "scatter",
        "x_field": "volume",
        "y_field": "pct_negative",
        "label_field": "theme",
        "color_field": "priority",
        "color_map": {
            "Fix now": "#c2413b",
            "Monitor": "#d97706",
            "Watch": "#1e40af",
            "Low priority": "#6b7280",
        },
        "value_format": "mixed",
        "minimum_rows": 1,
        "fallback": "No priority matrix yet.",
    },
    # --- Vehicle-level detail (MIN_CELL applied) ---
    "vehicles": {
        "type": "bar",
        "x_field": "vehicle_mentioned",
        "y_field": "complaint_rate_pct",
        "sort": "value_desc",
        "value_format": "pct",
        "minimum_rows": MIN_CELL,
        "fallback": f"No vehicles with ≥{MIN_CELL} analyzed rows.",
        "quality_note": f"Vehicles with fewer than {MIN_CELL} analyzed rows are hidden.",
    },
    "severity_by_model": {
        "type": "stacked_bar",
        # long-format: vehicle_mentioned × issue_severity × count
        "x_field": "vehicle_mentioned",
        "series_field": "issue_severity",
        "value_field": "count",
        "sort": "total_desc",
        "series_order": ["critical", "major", "minor", "none"],
        "color_map": {
            "critical": "#dc2626",
            "major": "#f59e0b",
            "minor": "#3b82f6",
            "none": "#94a3b8",
        },
        "value_format": "count",
        "minimum_rows": MIN_CELL,
        "fallback": f"Fewer than {MIN_CELL} complaints per vehicle — chart suppressed.",
        "quality_note": f"Only vehicles with ≥{MIN_CELL} complaints shown.",
    },
    "complaint_by_model": {
        "type": "stacked_bar_100",
        # wide-format: vehicle col + one col per complaint category
        "x_field": "vehicle",
        "sort": "total_desc",
        "normalize": True,
        "value_format": "pct",
        "minimum_rows": MIN_CELL,
        "fallback": (
            f"Fewer than {MIN_CELL} complaints per vehicle — "
            "chart suppressed to avoid misleading rates."
        ),
        "quality_note": (
            f"Only vehicles with ≥{MIN_CELL} complaints shown. "
            f"Cells with fewer than {MIN_CELL} complaints are zeroed."
        ),
    },
    "sentiment_by_model": {
        "type": "stacked_bar_100",
        # wide-format: vehicle col + one col per sentiment value
        "x_field": "vehicle",
        "sort": "negative_desc",
        "normalize": True,
        "series_order": ["negative", "neutral", "positive"],
        "color_map": {
            "positive": "#22c55e",
            "negative": "#ef4444",
            "neutral": "#94a3b8",
        },
        "value_format": "pct",
        "minimum_rows": MIN_CELL,
        "fallback": (
            f"Fewer than {MIN_CELL} rows per vehicle — "
            "chart suppressed to avoid misleading rates."
        ),
        "quality_note": f"Only vehicles with ≥{MIN_CELL} rows shown.",
    },
    # --- Lazy heavy matrices ---
    "category_by_model": {
        "type": "heatmap",
        "normalize": "row",
        "value_format": "count",
        "minimum_rows": MIN_CELL,
        "fallback": (
            f"Insufficient data for category-by-model heatmap "
            f"(need ≥{MIN_CELL} complaints per vehicle)."
        ),
        "quality_note": f"Cells with fewer than {MIN_CELL} complaints are hidden.",
        "lazy": True,
        "endpoint": "/api/charts/detail",
    },
    "cooccurrence": {
        "type": "heatmap",
        "value_format": "correlation",
        "minimum_rows": 1,
        "fallback": "No co-occurrence data yet.",
        "lazy": True,
        "endpoint": "/api/charts/detail",
    },
}


# ---------------------------------------------------------------------------
# JSON-safe record helpers
# ---------------------------------------------------------------------------

def _clean(value: Any) -> Any:
    """Replace float NaN / inf with None so JSON serialization succeeds."""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert a DataFrame to a list of JSON-safe dicts."""
    if df.empty:
        return []
    return [{k: _clean(v) for k, v in row.items()} for row in df.to_dict(orient="records")]


# ---------------------------------------------------------------------------
# Data builders
# ---------------------------------------------------------------------------

def build_chart_data(df: pd.DataFrame) -> dict[str, Any]:
    """Build all non-lazy chart data from an analyzed (filtered) dataframe.

    Pass the already-filtered frame from filter_analyzed(); the aggregation
    functions will call analyzed_frame() internally for a clean, idempotent pass.
    Lazy charts (category_by_model, cooccurrence) are intentionally absent here.
    """
    if df.empty:
        empty: list[dict[str, Any]] = []
        return {
            key: ([] if CHART_SPECS[key]["type"] != "heatmap" else {"rows": [], "columns": [], "values": []})
            for key in CHART_SPECS
            if not CHART_SPECS[key].get("lazy")
        }

    analyzed = analyzed_frame(df)
    complaints_df = analyzed[analyzed["complaint"] == 1] if not analyzed.empty else pd.DataFrame()

    return {
        # KPIs
        "sentiment": _records(value_counts_df(analyzed, "sentiment", "sentiment")),
        "flags": _records(flag_summary(analyzed)),
        "severity_summary": _records(
            value_counts_df(complaints_df, "issue_severity", "severity")
            if not complaints_df.empty else pd.DataFrame(columns=["severity", "count", "pct"])
        ),
        "comment_type": _records(comment_type_dist(analyzed)),
        "engagement": _records(engagement_breakdown(analyzed)),
        # Complaint themes
        "complaints": _records(complaint_summary(analyzed)),
        "all_complaint_mentions": _records(all_complaint_mentions(analyzed)),
        "engagement_weighted_themes": _records(engagement_weighted_themes(analyzed)),
        # EV / competitor
        "ev": _records(ev_comparison(analyzed)),
        "competitor_breakdown": _records(competitor_breakdown_detail(analyzed)),
        # Subreddit
        "subreddit": _records(subreddit_breakdown(analyzed, min_rows=MIN_CELL)),
        # Priority
        "priority": _records(priority_matrix(analyzed, min_complaints=2)),
        # Vehicle-level detail
        "vehicles": _records(vehicle_breakdown(analyzed, min_rows=MIN_CELL)),
        "severity_by_model": _records(severity_by_model(analyzed, min_complaints=MIN_CELL)),
        "complaint_by_model": complaint_by_model_table(analyzed, min_cell=MIN_CELL),
        "sentiment_by_model": sentiment_by_model_table(analyzed, min_cell=MIN_CELL),
    }


def build_detail_data(df: pd.DataFrame) -> dict[str, Any]:
    """Build lazy (heavy) chart data — served from /api/charts/detail.

    Kept out of /api/run so the main endpoint stays fast even on large datasets.
    """
    if df.empty:
        return {
            "category_by_model": {"rows": [], "columns": [], "values": []},
            "cooccurrence": {},
        }
    analyzed = analyzed_frame(df)
    corr = cooccurrence(analyzed)
    corr_dict = (
        corr.where(pd.notna(corr), None).to_dict()
        if not corr.empty
        else {}
    )
    return {
        "category_by_model": category_heatmap(analyzed, min_cell=MIN_CELL),
        "cooccurrence": corr_dict,
    }


def build_chart_payload(df: pd.DataFrame) -> dict[str, Any]:
    """Return chart_specs + chart_data for the /api/run response."""
    return {
        "chart_specs": CHART_SPECS,
        "chart_data": build_chart_data(df),
    }
