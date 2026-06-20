"""Chart PNG rendering (matplotlib) and PDF export (fpdf2).

Two export modes:
  "charts"   -- ZIP of PNG files, one per chart_id
  "briefing" -- PDF with KPI cover page + chart pages + narrative text

Triggered by scripts/pdf_export_job.py.
"""

from __future__ import annotations

import math
import os
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    import matplotlib
    matplotlib.use("Agg")  # non-interactive backend, safe for subprocesses
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.colors import LinearSegmentedColormap
    _HAS_MATPLOTLIB = True
except ImportError:  # pragma: no cover
    _HAS_MATPLOTLIB = False

try:
    from fpdf import FPDF  # type: ignore[import]
    _HAS_FPDF = True
except ImportError:  # pragma: no cover
    _HAS_FPDF = False


def _register_dejavu_fonts(pdf: "FPDF") -> str:
    """Register DejaVu Sans (shipped with Matplotlib) for Unicode support.

    Returns the font family name to pass to set_font().
    Raises RuntimeError if Matplotlib is not installed or fonts are missing.
    """
    if not _HAS_MATPLOTLIB:
        raise RuntimeError("matplotlib is required for Unicode PDF export")
    from matplotlib.font_manager import FontProperties, findfont

    styles = {
        "": FontProperties(family="DejaVu Sans", style="normal", weight="normal"),
        "B": FontProperties(family="DejaVu Sans", style="normal", weight="bold"),
        "I": FontProperties(family="DejaVu Sans", style="italic", weight="normal"),
    }
    for style, props in styles.items():
        path = findfont(props, fallback_to_default=False)
        if not Path(path).exists():
            raise RuntimeError(f"DejaVu Sans font file not found: {path}")
        pdf.add_font("DejaVu", style, path)
    return "DejaVu"


# ---------------------------------------------------------------------------
# Colour constants (mirrors CSS design tokens)
# ---------------------------------------------------------------------------

_PALETTE = {
    "blue":   "#1448a0",
    "green":  "#166044",
    "red":    "#9d2d25",
    "amber":  "#ac5410",
    "violet": "#5a4682",
    "teal":   "#0d6e6e",
}

_DEFAULT_COLORS = [
    "#1448a0", "#0d6e6e", "#ac5410", "#9d2d25", "#5a4682", "#166044",
]


# ---------------------------------------------------------------------------
# Tiny helpers
# ---------------------------------------------------------------------------

def _label(value: Any) -> str:
    return str(value or "").replace("_", " ")


def _num(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _empty_png(title: str, out_path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(6, 2.5))
    ax.text(0.5, 0.5, "No data", ha="center", va="center",
            fontsize=12, color="#9c9588", transform=ax.transAxes)
    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.axis("off")
    fig.savefig(out_path, dpi=120, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


# ---------------------------------------------------------------------------
# PNG renderers
# ---------------------------------------------------------------------------

def _bar_png(
    title: str,
    rows: list[dict[str, Any]],
    x_field: str,
    y_field: str,
    color_map: dict[str, str] | None,
    out_path: Path,
    max_rows: int = 18,
) -> Path:
    rows = rows[:max_rows]
    if not rows:
        return _empty_png(title, out_path)
    labels = [_label(r.get(x_field, "")) for r in rows]
    values = [_num(r.get(y_field)) for r in rows]
    colors = [
        (color_map or {}).get(str(r.get(x_field, "")), _PALETTE["blue"])
        for r in rows
    ]
    fig, ax = plt.subplots(figsize=(9, max(2.5, len(rows) * 0.42)))
    y_pos = list(range(len(labels)))
    ax.barh(y_pos, values, color=colors, height=0.65, linewidth=0)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_title(title, fontsize=11, fontweight="bold", pad=8)
    ax.tick_params(axis="x", labelsize=8)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.set_axisbelow(True)
    ax.xaxis.grid(True, alpha=0.35, linewidth=0.6)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


def _stacked_bar_long_png(
    title: str,
    rows: list[dict[str, Any]],
    x_field: str,
    series_field: str,
    value_field: str,
    series_order: list[str],
    color_map: dict[str, str] | None,
    out_path: Path,
) -> Path:
    """Stacked bar from long-format data (e.g. vehicle × severity × count)."""
    if not rows:
        return _empty_png(title, out_path)
    df = pd.DataFrame(rows)
    if df.empty:
        return _empty_png(title, out_path)
    pivot = df.pivot_table(
        index=x_field, columns=series_field, values=value_field,
        aggfunc="sum", fill_value=0,
    )
    ordered = [c for c in series_order if c in pivot.columns]
    ordered += [c for c in pivot.columns if c not in series_order]
    pivot = pivot[ordered]
    return _stacked_bar_wide_png(title, pivot, ordered, color_map, out_path, normalize=False)


def _stacked_bar_wide_png(
    title: str,
    pivot_df: pd.DataFrame,
    series_order: list[str],
    color_map: dict[str, str] | None,
    out_path: Path,
    normalize: bool = False,
) -> Path:
    """Stacked bar from wide-format DataFrame (index = labels, cols = series)."""
    if pivot_df.empty:
        return _empty_png(title, out_path)
    ordered = [c for c in series_order if c in pivot_df.columns]
    ordered += [c for c in pivot_df.columns if c not in series_order]
    data = pivot_df[ordered].astype(float)
    if normalize:
        row_sums = data.sum(axis=1).replace(0, 1)
        data = data.div(row_sums, axis=0) * 100

    n_rows = len(data)
    fig, ax = plt.subplots(figsize=(9, max(2.5, n_rows * 0.55 + 1.5)))
    left = np.zeros(n_rows)
    y_pos = list(range(n_rows))
    patches = []
    for i, col in enumerate(ordered):
        if col not in data.columns:
            continue
        vals = data[col].values
        color = (color_map or {}).get(col, _DEFAULT_COLORS[i % len(_DEFAULT_COLORS)])
        ax.barh(y_pos, vals, left=left, color=color, height=0.65, linewidth=0)
        patches.append(mpatches.Patch(color=color, label=_label(col)))
        left += vals

    ax.set_yticks(y_pos)
    ax.set_yticklabels([_label(v) for v in data.index], fontsize=9)
    ax.invert_yaxis()
    ax.set_title(title, fontsize=11, fontweight="bold", pad=8)
    ax.tick_params(axis="x", labelsize=8)
    ax.spines[["top", "right", "left"]].set_visible(False)
    if patches:
        ax.legend(handles=patches, loc="lower right", fontsize=7, framealpha=0.7)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


def _grouped_bar_png(
    title: str,
    rows: list[dict[str, Any]],
    x_field: str,
    series_keys: list[str],
    out_path: Path,
) -> Path:
    if not rows or not series_keys:
        return _empty_png(title, out_path)
    labels = [_label(r.get(x_field, "")) for r in rows]
    n_groups = len(labels)
    n_series = len(series_keys)
    x = np.arange(n_groups)
    width = 0.7 / max(n_series, 1)

    fig, ax = plt.subplots(figsize=(max(5, n_groups * 1.3), 4.5))
    for i, key in enumerate(series_keys):
        vals = [_num(r.get(key)) for r in rows]
        offset = (i - n_series / 2 + 0.5) * width
        color = _DEFAULT_COLORS[i % len(_DEFAULT_COLORS)]
        ax.bar(x + offset, vals, width=width * 0.92, color=color, label=_label(key))

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9, rotation=20, ha="right")
    ax.set_title(title, fontsize=11, fontweight="bold", pad=8)
    ax.tick_params(axis="y", labelsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_axisbelow(True)
    ax.yaxis.grid(True, alpha=0.35, linewidth=0.6)
    ax.legend(fontsize=8, framealpha=0.7)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


def _scatter_png(
    title: str,
    rows: list[dict[str, Any]],
    x_field: str,
    y_field: str,
    label_field: str,
    color_field: str,
    color_map: dict[str, str] | None,
    out_path: Path,
) -> Path:
    if not rows:
        return _empty_png(title, out_path)
    fig, ax = plt.subplots(figsize=(9, 5))
    for row in rows[:15]:
        x_val = _num(row.get(x_field))
        y_val = _num(row.get(y_field))
        color = (color_map or {}).get(str(row.get(color_field, "")), _PALETTE["blue"])
        label_text = _label(row.get(label_field, ""))
        size = max(40, min(300, x_val * 20))
        ax.scatter(x_val, y_val, s=size, c=color, alpha=0.75, zorder=3)
        ax.annotate(
            label_text[:22], (x_val, y_val),
            fontsize=7, ha="center", va="bottom",
            xytext=(0, 5), textcoords="offset points",
        )
    ax.set_xlabel(_label(x_field), fontsize=9)
    ax.set_ylabel(_label(y_field), fontsize=9)
    ax.set_title(title, fontsize=11, fontweight="bold", pad=8)
    ax.tick_params(labelsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_axisbelow(True)
    ax.grid(True, alpha=0.25, linewidth=0.6)
    if color_map:
        patches = [mpatches.Patch(color=c, label=k) for k, c in color_map.items()]
        ax.legend(handles=patches, fontsize=8, loc="upper left", framealpha=0.75)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


def _heatmap_png(
    title: str,
    data: dict[str, Any],
    out_path: Path,
) -> Path:
    rows_list = data.get("rows", [])
    cols_list = data.get("columns", [])
    values = data.get("values", [])
    if not rows_list or not cols_list or not values:
        return _empty_png(title, out_path)
    arr = np.array(
        [[(_num(v) if v is not None else 0) for v in row] for row in values],
        dtype=float,
    )
    fig, ax = plt.subplots(
        figsize=(max(6, len(cols_list) * 0.7), max(3, len(rows_list) * 0.5 + 1.5))
    )
    cmap = LinearSegmentedColormap.from_list("redwhite", ["white", "#9d2d25"])
    im = ax.imshow(arr, cmap=cmap, aspect="auto", interpolation="nearest")
    ax.set_xticks(range(len(cols_list)))
    ax.set_xticklabels(
        [_label(c)[:14] for c in cols_list], rotation=40, ha="right", fontsize=7,
    )
    ax.set_yticks(range(len(rows_list)))
    ax.set_yticklabels([_label(r) for r in rows_list], fontsize=8)
    max_val = arr.max() or 1
    for i in range(len(rows_list)):
        for j in range(len(cols_list)):
            val = arr[i, j]
            if val > 0:
                text_color = "white" if val > max_val * 0.55 else "black"
                ax.text(j, i, int(val), ha="center", va="center",
                        fontsize=6, color=text_color)
    ax.set_title(title, fontsize=11, fontweight="bold", pad=8)
    plt.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


# ---------------------------------------------------------------------------
# Dispatch: one chart → one PNG
# ---------------------------------------------------------------------------

def render_chart_png(
    chart_id: str,
    spec: dict[str, Any],
    data: dict[str, Any],
    out_dir: Path,
) -> Path | None:
    """Render a single chart to a PNG. Returns None if matplotlib unavailable."""
    if not _HAS_MATPLOTLIB:
        return None

    out_path = out_dir / f"{chart_id}.png"
    chart_data = data.get(chart_id)
    chart_type = spec.get("type", "bar")
    title = chart_id.replace("_", " ").title()
    color_map = spec.get("color_map")

    if not chart_data or (isinstance(chart_data, list) and not chart_data):
        return _empty_png(title, out_path)

    if chart_type == "bar":
        return _bar_png(
            title, chart_data,
            spec.get("x_field", "label"),
            spec.get("y_field", "count"),
            color_map, out_path,
        )

    if chart_type == "stacked_bar":
        return _stacked_bar_long_png(
            title, chart_data,
            spec.get("x_field", "vehicle_mentioned"),
            spec.get("series_field", "issue_severity"),
            spec.get("value_field", "count"),
            spec.get("series_order", []),
            color_map, out_path,
        )

    if chart_type == "stacked_bar_100":
        if not chart_data:
            return _empty_png(title, out_path)
        x_field = spec.get("x_field", "vehicle")
        df = pd.DataFrame(chart_data)
        pivot = df.set_index(x_field) if x_field in df.columns else df
        return _stacked_bar_wide_png(
            title, pivot, spec.get("series_order", []), color_map, out_path, normalize=True,
        )

    if chart_type == "grouped_bar":
        return _grouped_bar_png(
            title, chart_data,
            spec.get("x_field", "label"),
            spec.get("series_keys", []),
            out_path,
        )

    if chart_type == "scatter":
        return _scatter_png(
            title, chart_data,
            spec.get("x_field", "volume"),
            spec.get("y_field", "pct_negative"),
            spec.get("label_field", "theme"),
            spec.get("color_field", "priority"),
            color_map, out_path,
        )

    if chart_type == "heatmap":
        if isinstance(chart_data, dict) and "rows" in chart_data:
            return _heatmap_png(title, chart_data, out_path)
        # cooccurrence: dict-of-dicts
        if isinstance(chart_data, dict) and chart_data:
            cols = list(chart_data.keys())
            values = [[chart_data.get(r, {}).get(c, 0) for c in cols] for r in cols]
            return _heatmap_png(
                title, {"rows": cols, "columns": cols, "values": values}, out_path,
            )
        return _empty_png(title, out_path)

    return _empty_png(title, out_path)


# ---------------------------------------------------------------------------
# Batch: render all non-lazy charts
# ---------------------------------------------------------------------------

def render_all_charts(
    chart_specs: dict[str, Any],
    chart_data: dict[str, Any],
    out_dir: Path,
) -> dict[str, Path]:
    """Render every non-lazy chart and return chart_id → png_path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    png_paths: dict[str, Path] = {}
    for chart_id, spec in chart_specs.items():
        if spec.get("lazy"):
            continue
        path = render_chart_png(chart_id, spec, chart_data, out_dir)
        if path is not None:
            png_paths[chart_id] = path
    return png_paths


# ---------------------------------------------------------------------------
# ZIP export
# ---------------------------------------------------------------------------

def build_charts_zip(png_paths: dict[str, Path], zip_path: Path) -> Path:
    """Bundle rendered PNGs into a ZIP file."""
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = zip_path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for chart_id, png_path in png_paths.items():
            if png_path.exists():
                archive.write(png_path, f"{chart_id}.png")
    os.replace(tmp, zip_path)
    return zip_path


# ---------------------------------------------------------------------------
# PDF export
# ---------------------------------------------------------------------------

# Preferred chart order in the briefing PDF
_PDF_CHART_ORDER = [
    "sentiment", "flags", "complaints", "vehicles",
    "priority", "severity_by_model", "complaint_by_model",
    "sentiment_by_model", "ev", "competitor_breakdown",
    "engagement", "engagement_weighted_themes",
    "subreddit", "comment_type", "all_complaint_mentions",
]


def build_briefing_pdf(
    png_paths: dict[str, Path],
    summary: dict[str, Any],
    report_text: str,
    pdf_path: Path,
) -> Path:
    """Write a briefing PDF: KPI cover page + chart pages + narrative text."""
    if not _HAS_FPDF:
        raise ImportError("fpdf2 is required. pip install fpdf2")

    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.set_margins(14, 12, 14)

    # --- Cover page ---
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(0, 12, "GM Reddit Signal Briefing", ln=True)
    pdf.set_font("Helvetica", "", 11)
    metrics = summary.get("metrics", {})
    for line in [
        f"Total rows: {metrics.get('total_rows', 0):,}",
        f"Analyzed rows: {metrics.get('analyzed_rows', 0):,}",
        f"Complaint rate: {metrics.get('complaint_rate', 0):.1f}%",
        f"Negative sentiment: {metrics.get('negative_rate', 0):.1f}%",
        f"Competitor signal: {metrics.get('competitor_rate', 0):.1f}%",
        f"EV topic mix: {metrics.get('ev_rate', 0):.1f}%",
    ]:
        pdf.ln(2)
        pdf.cell(0, 7, line, ln=True)

    # --- Chart pages ---
    for chart_id in _PDF_CHART_ORDER:
        png_path = png_paths.get(chart_id)
        if png_path is None or not png_path.exists():
            continue
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, chart_id.replace("_", " ").title(), ln=True)
        pdf.image(str(png_path), x=14, y=22, w=260)

    # --- Narrative page(s) ---
    if report_text:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 8, "Narrative Briefing", ln=True)
        pdf.ln(2)
        pdf.set_font("Courier", "", 9)
        for line in report_text.splitlines():
            # Wrap long lines to avoid overflow
            if len(line) > 110:
                for i in range(0, len(line), 110):
                    pdf.cell(0, 4.5, line[i : i + 110], ln=True)
            else:
                pdf.cell(0, 4.5, line if line else " ", ln=True)

    tmp = pdf_path.with_suffix(".tmp")
    pdf.output(str(tmp))
    os.replace(tmp, pdf_path)
    return pdf_path


# ---------------------------------------------------------------------------
# Trend briefing PDF (Phase 5)
# ---------------------------------------------------------------------------

_CONFIDENCE_EMOJI = {"high": "✓", "medium": "~", "low": "!"}
_DIRECTION_LABEL = {
    "rising": "Rising",
    "falling": "Falling",
    "stable": "Stable",
    "insufficient_data": "Insufficient data",
}


def build_trend_briefing_pdf(
    labels: dict[str, Any],
    examples: dict[str, Any],
    signals: dict[str, Any],
    pdf_path: Path,
) -> Path:
    """Write a trend briefing PDF: cover page + one page per cluster with signals.

    labels  — cluster_labels.json content  {cid_str: label dict}
    examples — cluster_examples.json content {cid_str: context dict}
    signals — trend_signals.json content (may be {} if trend analysis not run)
    pdf_path — output file path (written atomically)
    """
    if not _HAS_FPDF:
        raise ImportError("fpdf2 is required. pip install fpdf2")

    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.set_margins(14, 12, 14)
    _fam = _register_dejavu_fonts(pdf)

    cluster_signals: dict[str, Any] = signals.get("signals", {})
    data_span = signals.get("data_span_days", 0)
    has_timestamps = signals.get("has_timestamps", False)
    computed_at = signals.get("computed_at")

    # Sort clusters by rising-first, then falling, then stable, then no-data
    _dir_order = {"rising": 0, "falling": 1, "stable": 2, "insufficient_data": 3}

    def _cluster_sort_key(cid_str: str) -> tuple:
        sig = cluster_signals.get(cid_str, {})
        vel_dir = sig.get("velocity", {}).get("direction", "insufficient_data")
        conf = sig.get("confidence_banner", "low")
        conf_order = {"high": 0, "medium": 1, "low": 2}
        return (_dir_order.get(vel_dir, 3), conf_order.get(conf, 2))

    sorted_cids = sorted(labels.keys(), key=_cluster_sort_key)

    # ---- Cover page ----
    pdf.add_page()
    pdf.set_font(_fam, "B", 20)
    pdf.cell(0, 12, "GM Reddit — Trend Briefing", ln=True)
    pdf.set_font(_fam, "", 11)
    pdf.ln(2)

    if computed_at:
        import datetime
        dt = datetime.datetime.fromtimestamp(float(computed_at)).strftime("%Y-%m-%d %H:%M UTC")
        pdf.cell(0, 7, f"Generated: {dt}", ln=True)
    pdf.cell(0, 7, f"Clusters analyzed: {len(labels)}", ln=True)
    if has_timestamps:
        pdf.cell(0, 7, f"Data span: {data_span} days", ln=True)
    else:
        pdf.cell(0, 7, "Note: no timestamps in dataset — trend signals unavailable.", ln=True)
        pdf.cell(0, 7, "Cluster labels reflect thematic content only.", ln=True)

    pdf.ln(4)
    pdf.set_font(_fam, "B", 12)
    pdf.cell(0, 8, "Cluster Summary", ln=True)
    pdf.set_font(_fam, "", 10)

    for cid_str in sorted_cids:
        label_info = labels.get(cid_str, {})
        sig = cluster_signals.get(cid_str, {})
        short = str(label_info.get("short_label", f"Cluster {cid_str}"))[:60]
        conf = sig.get("confidence_banner", "")
        conf_mark = _CONFIDENCE_EMOJI.get(conf, "")
        vel_dir = _DIRECTION_LABEL.get(
            sig.get("velocity", {}).get("direction", ""), ""
        )
        row_text = f"  Cluster {cid_str}: {short}"
        if vel_dir:
            row_text += f"  [{vel_dir}]"
        if conf_mark:
            row_text += f"  {conf_mark}"
        pdf.cell(0, 6, row_text, ln=True)

    # ---- Per-cluster pages ----
    for cid_str in sorted_cids:
        label_info = labels.get(cid_str, {})
        ctx = examples.get(cid_str, {})
        sig = cluster_signals.get(cid_str, {})

        pdf.add_page()
        pdf.set_font(_fam, "B", 14)
        short = str(label_info.get("short_label", f"Cluster {cid_str}"))
        pdf.cell(0, 9, f"Cluster {cid_str}: {short}", ln=True)

        pdf.set_font(_fam, "", 10)
        detailed = str(label_info.get("detailed_label", ""))
        if detailed:
            pdf.multi_cell(0, 6, detailed)
            pdf.ln(2)

        # Trend signals section
        if sig:
            conf = sig.get("confidence_banner", "low")
            conf_note = sig.get("confidence_note", "")
            pdf.set_font(_fam, "B", 11)
            pdf.cell(0, 7, f"Trend Signals  [{conf.upper()} confidence]", ln=True)
            pdf.set_font(_fam, "", 10)

            if conf_note:
                pdf.multi_cell(0, 6, conf_note)
                pdf.ln(1)

            vel = sig.get("velocity", {})
            zsc = sig.get("zscore", {})

            vel_dir = _DIRECTION_LABEL.get(vel.get("direction", ""), "N/A")
            pdf.cell(0, 6, f"  Velocity: {vel_dir}  —  {vel.get('data_note', '')}", ln=True)

            zsc_dir = _DIRECTION_LABEL.get(zsc.get("direction", ""), "N/A")
            pdf.cell(0, 6, f"  Z-score: {zsc_dir}  —  {zsc.get('data_note', '')}", ln=True)

            agreement = sig.get("agreement", "")
            if agreement:
                pdf.cell(0, 6, f"  Signal agreement: {agreement}", ln=True)
        else:
            pdf.set_font(_fam, "I", 10)
            pdf.cell(0, 6, "Trend signals not available (run /api/trends/run first).", ln=True)

        pdf.ln(3)

        # Cluster metadata
        pdf.set_font(_fam, "B", 11)
        pdf.cell(0, 7, "Cluster Metadata", ln=True)
        pdf.set_font(_fam, "", 10)
        pdf.cell(0, 6, f"  Size: {ctx.get('cluster_size', 0)} comments", ln=True)
        top_vehicles = ctx.get("top_vehicles", [])
        if top_vehicles:
            pdf.cell(0, 6, f"  Top vehicles: {', '.join(str(v) for v in top_vehicles[:3])}", ln=True)
        top_cats = ctx.get("top_categories", [])
        if top_cats:
            pdf.cell(0, 6, f"  Top categories: {', '.join(str(c).replace('_', ' ') for c in top_cats[:3])}", ln=True)
        sentiment = ctx.get("sentiment_mix", {})
        if sentiment:
            sent_parts = [f"{k}: {v}" for k, v in list(sentiment.items())[:3]]
            pdf.cell(0, 6, f"  Sentiment: {', '.join(sent_parts)}", ln=True)

        pdf.ln(3)

        # Representative examples
        centroid_text = ctx.get("centroid_reps_text", "")
        if centroid_text:
            pdf.set_font(_fam, "B", 11)
            pdf.cell(0, 7, "Representative Comments", ln=True)
            pdf.set_font(_fam, "", 8)
            for line in centroid_text.splitlines()[:6]:
                if len(line) > 120:
                    for chunk_start in range(0, len(line), 120):
                        pdf.cell(0, 4, line[chunk_start: chunk_start + 120], ln=True)
                else:
                    pdf.cell(0, 4, line if line else " ", ln=True)

    tmp = pdf_path.with_suffix(".tmp")
    pdf.output(str(tmp))
    os.replace(tmp, pdf_path)
    return pdf_path
