"""Canonical trend-report model and Markdown renderer.

The model is the single semantic boundary for every trend briefing format.
Renderers may differ in layout, but they consume the same ordered clusters,
coverage statement, and generation provenance.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_DIRECTION_ORDER = {"rising": 0, "falling": 1, "stable": 2, "insufficient_data": 3}
_CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}
_DIRECTION_LABEL = {
    "rising": "Rising",
    "falling": "Falling",
    "stable": "Stable",
    "insufficient_data": "Insufficient data",
}


@dataclass(frozen=True)
class TrendClusterReport:
    cluster_id: str
    label: dict[str, Any]
    context: dict[str, Any]
    signal: dict[str, Any]

    @property
    def short_label(self) -> str:
        return str(self.label.get("short_label") or f"Cluster {self.cluster_id}")


@dataclass(frozen=True)
class TrendReportModel:
    tag: str
    generation_id: str
    run_id: str
    generated_at: float | None
    has_timestamps: bool
    data_span_days: int
    clusters: tuple[TrendClusterReport, ...]

    @classmethod
    def from_artifacts(
        cls,
        labels: dict[str, Any],
        examples: dict[str, Any],
        signals: dict[str, Any],
        *,
        tag: str = "",
        generation_id: str = "",
        run_id: str = "",
    ) -> "TrendReportModel":
        cluster_signals = signals.get("signals") if isinstance(signals.get("signals"), dict) else {}

        def sort_key(cluster_id: str) -> tuple[int, int, int, str]:
            signal = cluster_signals.get(cluster_id, {})
            direction = signal.get("velocity", {}).get("direction", "insufficient_data")
            confidence = signal.get("confidence_banner", "low")
            try:
                numeric_id = int(cluster_id)
            except (TypeError, ValueError):
                numeric_id = 2**31 - 1
            return (
                _DIRECTION_ORDER.get(direction, 3),
                _CONFIDENCE_ORDER.get(confidence, 2),
                numeric_id,
                str(cluster_id),
            )

        ordered = tuple(
            TrendClusterReport(
                cluster_id=str(cluster_id),
                label=copy.deepcopy(value if isinstance(value, dict) else {"short_label": value}),
                context=copy.deepcopy(examples.get(str(cluster_id), {})),
                signal=copy.deepcopy(cluster_signals.get(str(cluster_id), {})),
            )
            for cluster_id, value in sorted(labels.items(), key=lambda item: sort_key(str(item[0])))
        )
        computed_at = signals.get("computed_at")
        try:
            generated_at = float(computed_at) if computed_at is not None else None
        except (TypeError, ValueError):
            generated_at = None
        try:
            data_span_days = max(0, int(signals.get("data_span_days", 0) or 0))
        except (TypeError, ValueError):
            data_span_days = 0
        return cls(
            tag=str(tag),
            generation_id=str(generation_id),
            run_id=str(run_id),
            generated_at=generated_at,
            has_timestamps=bool(signals.get("has_timestamps", False)),
            data_span_days=data_span_days,
            clusters=ordered,
        )

    def to_artifacts(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        labels = {cluster.cluster_id: copy.deepcopy(cluster.label) for cluster in self.clusters}
        examples = {cluster.cluster_id: copy.deepcopy(cluster.context) for cluster in self.clusters}
        signals: dict[str, Any] = {
            "has_timestamps": self.has_timestamps,
            "data_span_days": self.data_span_days,
            "signals": {cluster.cluster_id: copy.deepcopy(cluster.signal) for cluster in self.clusters},
        }
        if self.generated_at is not None:
            signals["computed_at"] = self.generated_at
        return labels, examples, signals


def _generated_label(timestamp: float | None) -> str:
    if timestamp is None:
        return "Not recorded"
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _cell(value: Any) -> str:
    return str(value or "—").replace("|", "\\|").replace("\n", " ")


def render_trend_markdown(model: TrendReportModel) -> str:
    """Render a decision-readable Markdown briefing from the canonical model."""
    lines = [
        "# GM Reddit — Trend Briefing",
        "",
        "## Report provenance",
        "",
        f"- Analysis tag: {model.tag or 'not recorded'}",
        f"- Generation: {model.generation_id or 'legacy'}",
        f"- Run: {model.run_id or 'not recorded'}",
        f"- Trend signals computed: {_generated_label(model.generated_at)}",
        f"- Clusters analyzed: {len(model.clusters)}",
        "",
        "## Data coverage",
        "",
    ]
    if model.has_timestamps:
        lines.append(f"Usable timestamps span **{model.data_span_days} days**. Trend signals are estimates from dated records and cluster assignments.")
    else:
        lines.append("**No usable timestamps were found.** Trend direction is unavailable; cluster labels describe themes only.")

    lines.extend([
        "",
        "## Cluster summary",
        "",
        "| Cluster | Theme | Direction | Confidence | Records |",
        "|---:|---|---|---|---:|",
    ])
    for cluster in model.clusters:
        direction = cluster.signal.get("velocity", {}).get("direction", "insufficient_data")
        confidence = cluster.signal.get("confidence_banner", "low" if cluster.signal else "unavailable")
        lines.append(
            f"| {_cell(cluster.cluster_id)} | {_cell(cluster.short_label)} | "
            f"{_cell(_DIRECTION_LABEL.get(direction, direction))} | {_cell(confidence)} | "
            f"{int(cluster.context.get('cluster_size', 0) or 0)} |"
        )

    for cluster in model.clusters:
        lines.extend(["", f"## Cluster {cluster.cluster_id}: {cluster.short_label}", ""])
        detailed = str(cluster.label.get("detailed_label") or "").strip()
        if detailed:
            lines.extend([detailed, ""])
        signal = cluster.signal
        if signal:
            velocity = signal.get("velocity", {})
            zscore = signal.get("zscore", {})
            lines.extend([
                f"- Confidence: {signal.get('confidence_banner', 'low')}",
                f"- Velocity: {_DIRECTION_LABEL.get(velocity.get('direction'), velocity.get('direction', 'unavailable'))} — {velocity.get('data_note', 'No detail recorded.')}",
                f"- Z-score: {_DIRECTION_LABEL.get(zscore.get('direction'), zscore.get('direction', 'unavailable'))} — {zscore.get('data_note', 'No detail recorded.')}",
                f"- Signal agreement: {signal.get('agreement', 'not recorded')}",
            ])
        else:
            lines.append("- Trend direction is unavailable for this cluster.")
        vehicles = cluster.context.get("top_vehicles") or []
        categories = cluster.context.get("top_categories") or []
        if vehicles:
            lines.append(f"- Top vehicles: {', '.join(str(value) for value in vehicles[:3])}")
        if categories:
            lines.append(f"- Top categories: {', '.join(str(value).replace('_', ' ') for value in categories[:3])}")
        examples_text = str(cluster.context.get("centroid_reps_text") or "").strip()
        if examples_text:
            lines.extend(["", "Representative records:", "", "```text", examples_text, "```"])

    return "\n".join(lines).rstrip() + "\n"


def write_trend_markdown(model: TrendReportModel, path: Path) -> Path:
    """Write Markdown atomically so readers never observe a partial report."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(render_trend_markdown(model), encoding="utf-8")
    os.replace(temporary, path)
    return path
