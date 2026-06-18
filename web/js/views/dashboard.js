// dashboard.js — landing view: metric deck, run focus card, and chart panels.
import { state, esc, fmt, humanLabel, topItem } from "../state.js";
import { panel, chartPanel, metricGrid, emptyState } from "../components.js";
import { evidenceFeed } from "./explore.js";

export function dashboard() {
  const data = state.data;
  if (!data?.summary.metrics.total_rows) return emptyState();
  const topTheme = topItem(data.chart_data?.complaints, "theme");
  const topVehicle = topItem(data.chart_data?.vehicles, "vehicle_mentioned", "comment_count");
  return `
    <section class="readout-deck">
      <div>${metricGrid(data.summary.metrics)}</div>
      <aside class="run-card">
        <span class="micro-label">Run focus</span>
        <strong>${esc(topTheme ? humanLabel(topTheme.theme) : "No dominant complaint")}</strong>
        <p>${esc(topVehicle
          ? `${humanLabel(topVehicle.vehicle_mentioned)} has the largest visible sample.`
          : "Collect or classify more rows to build a stronger issue map.")}</p>
        <div class="run-card-actions">
          <button class="button primary" type="button" data-save-export>Save ZIP</button>
          <button class="button secondary" type="button" data-jump="collect">Collect</button>
        </div>
      </aside>
    </section>
    <div class="panel-grid">
      ${chartPanel("sentiment", "Sentiment distribution")}
      ${chartPanel("flags", "Signal flags")}
      ${chartPanel("complaints", "Complaint themes")}
    </div>
    <div class="panel-grid two">
      ${chartPanel("priority", "Priority map", "volume x negativity")}
      ${panel("Evidence feed", evidenceFeed(data.evidence.slice(0, 8)), "latest matched rows", "evidence-panel")}
    </div>`;
}
