// dashboard.js — signals-first landing: hero card, 2-col signal grid,
// run metrics, and chart panels (3 existing + 4 new trend charts).
import { state, esc, fmt, humanLabel } from "../state.js";
import { panel, chartPanel, metricGrid, emptyState } from "../components.js";
import {
  renderIntoEl, renderSparkline,
  buildQuadrantOption, buildLeaderboardOption,
  buildLineTimeseriesOption, buildStackedAreaOption,
} from "../charts.js";

// ── Helpers ─────────────────────────────────────────────────────────────────

// Map velocity direction to a single arrow character.
function dirArrow(dir) {
  if (dir === "rising") return "↑";
  if (dir === "falling") return "↓";
  if (dir === "stable") return "→";
  return "—";
}

// Inline confidence badge with color-coded background.
function confBadge(level) {
  const styles = {
    high: "background:#dcfce7;color:#166534",
    medium: "background:#fef9c3;color:#713f12",
    low: "background:#f1f5f9;color:#475569",
  };
  const s = styles[level] || styles.low;
  return `<span style="${s};border-radius:3px;padding:1px 5px;font-size:10px;font-weight:700">${esc((level || "n/a").toUpperCase())}</span>`;
}

// Sort: rising HIGH→MED→LOW → stable → falling → N/A. Tiebreak by cluster_size.
function sortSignals(clusters) {
  const dirOrd = { rising: 0, stable: 1, falling: 2, insufficient_data: 3 };
  const confOrd = { high: 0, medium: 1, low: 2 };
  return [...clusters].sort((a, b) => {
    const aDir = a.trend_signal?.velocity?.direction || "insufficient_data";
    const bDir = b.trend_signal?.velocity?.direction || "insufficient_data";
    const aC = a.trend_signal?.confidence_banner || "low";
    const bC = b.trend_signal?.confidence_banner || "low";
    // Primary sort: direction order
    const d = (dirOrd[aDir] ?? 3) - (dirOrd[bDir] ?? 3);
    if (d !== 0) return d;
    // Secondary sort: confidence level
    const c = (confOrd[aC] ?? 2) - (confOrd[bC] ?? 2);
    if (c !== 0) return c;
    // Tiebreak: cluster volume descending
    return (b.cluster_size || 0) - (a.cluster_size || 0);
  });
}

// True if any cluster has a rising velocity signal.
function hasRising(clusters) {
  return clusters.some((c) => c.trend_signal?.velocity?.direction === "rising");
}

// ── Signals section ──────────────────────────────────────────────────────────

// Render the hero card for the top-ranked cluster.
function signalHero(clusters) {
  const noRising = !hasRising(clusters);
  // If nothing is rising, sort by volume; otherwise use signal sort.
  const top = noRising
    ? [...clusters].sort((a, b) => (b.cluster_size || 0) - (a.cluster_size || 0))[0]
    : sortSignals(clusters)[0];
  if (!top) return "";

  const sig = top.trend_signal || {};
  const vel = sig.velocity || {};
  const zsc = sig.zscore || {};
  const label = top.label || {};
  const dir = vel.direction || "insufficient_data";
  const conf = sig.confidence_banner || "";
  // Append velocity % and z-score direction as inline notes if available.
  const velPct = vel.velocity != null ? ` · ${(vel.velocity * 100).toFixed(0)}% velocity` : "";
  const zNote = zsc.direction && zsc.direction !== "insufficient_data"
    ? ` · z-score ${dirArrow(zsc.direction)}`
    : "";
  const note = noRising
    ? "No accelerating signals detected — sorted by volume."
    : esc(sig.confidence_note || "");

  return `<div style="background:#fff;border:1px solid #bfdbfe;border-radius:10px;padding:14px 18px;display:flex;gap:16px;align-items:center;margin-bottom:8px;box-shadow:0 1px 4px rgba(37,99,235,.07)">
    <div style="flex:1">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:5px">
        <span style="font-size:17px">${dirArrow(dir)}</span>
        <span style="font-size:15px;font-weight:700;color:#0f172a">${esc(label.short_label || `Cluster ${top.cluster_id}`)}</span>
        ${conf ? confBadge(conf) : ""}
      </div>
      <div style="font-size:11px;color:#64748b">${note}${velPct}${zNote}</div>
    </div>
    <div style="text-align:right;border-left:1px solid #e2e8f0;padding-left:16px;min-width:60px">
      <div style="font-size:24px;font-weight:800;color:#2563eb">${fmt.format(top.cluster_size || 0)}</div>
      <div style="font-size:10px;color:#94a3b8">posts</div>
    </div>
  </div>`;
}

// Render the 2-column grid of remaining clusters (all except the hero).
function signalGrid(clusters) {
  const noRising = !hasRising(clusters);
  const ordered = noRising
    ? [...clusters].sort((a, b) => (b.cluster_size || 0) - (a.cluster_size || 0))
    : sortSignals(clusters);
  const rest = ordered.slice(1); // everything after the hero
  if (!rest.length) return "";
  const rows = rest.map((c) => {
    const sig = c.trend_signal || {};
    const vel = sig.velocity || {};
    const dir = vel.direction || "insufficient_data";
    const label = c.label || {};
    // Show velocity % if available; fall back to direction label or "no data".
    const velText = vel.velocity != null
      ? `${(vel.velocity * 100).toFixed(0)}%`
      : dir === "stable" ? "stable" : "no data";
    // Dim the label text for clusters with no signal data.
    const dimColor = dir === "insufficient_data" ? "#94a3b8" : "#0f172a";
    return `<div style="background:#fff;border:1px solid #e2e8f0;border-radius:7px;padding:7px 12px;display:flex;align-items:center;gap:8px;font-size:12px">
      <span style="width:14px;text-align:center;font-size:14px">${dirArrow(dir)}</span>
      <span style="flex:1;font-weight:500;color:${dimColor}">${esc(humanLabel(label.short_label || `Cluster ${c.cluster_id}`))}</span>
      <span style="color:#64748b;font-size:11px">${esc(velText)}</span>
      ${sig.confidence_banner ? confBadge(sig.confidence_banner) : ""}
      <div id="sparkline-${c.cluster_id}" style="width:80px;height:32px;flex-shrink:0"></div>
    </div>`;
  });
  return `<div style="display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-bottom:20px">${rows.join("")}</div>`;
}

// ── Mount trend charts (called by nav.js after DOM is painted) ───────────────

// This is called from render() in nav.js after root.innerHTML is set.
// It's a no-op when the current view isn't dashboard.
export function mountDashboardCharts() {
  if (state.view !== "dashboard") return;
  const td = state.trendsData;
  const tsd = state.timeseriesData;
  const clusters = td?.ok && td.clusters?.length ? td.clusters : null;

  if (clusters) {
    // Mount the velocity × z-score quadrant scatter chart.
    const quadEl = document.getElementById("quadrantChart");
    if (quadEl) renderIntoEl(quadEl, buildQuadrantOption(clusters));

    // Mount the trend leaderboard bar chart.
    const lbEl = document.getElementById("leaderboardChart");
    if (lbEl) renderIntoEl(lbEl, buildLeaderboardOption(clusters));

    // Determine grid order (same logic as signalGrid) to find each cluster's DOM id.
    const noRising = !hasRising(clusters);
    const gridOrder = noRising
      ? [...clusters].sort((a, b) => (b.cluster_size || 0) - (a.cluster_size || 0))
      : sortSignals(clusters);

    // Mount sparklines for each grid row (skip index 0 = hero).
    gridOrder.slice(1).forEach((c) => {
      const el = document.getElementById(`sparkline-${c.cluster_id}`);
      const data = tsd?.by_cluster?.[String(c.cluster_id)];
      if (el && data?.length) renderSparkline(el, data);
    });
  }

  if (tsd?.ok) {
    // Mount weekly negative-mention line chart.
    const negEl = document.getElementById("negTimeChart");
    if (negEl) renderIntoEl(negEl, buildLineTimeseriesOption(tsd, "negative"));

    // Mount weekly stacked-area sentiment chart.
    const sentEl = document.getElementById("sentTimeChart");
    if (sentEl) renderIntoEl(sentEl, buildStackedAreaOption(tsd));
  } else {
    // Show a graceful fallback if timeseries data isn't loaded yet.
    ["negTimeChart", "sentTimeChart"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = `<div class="notice">Date data required to render this chart.</div>`;
    });
  }
}

// ── Main view function ───────────────────────────────────────────────────────

export function dashboard() {
  const data = state.data;
  // Return empty state template when no run data is loaded.
  if (!data?.summary?.metrics?.total_rows) return emptyState();

  const td = state.trendsData;
  // Only show signals section when trends have been fetched and returned clusters.
  const clusters = td?.ok && td.clusters?.length ? td.clusters : null;

  const signalsSection = clusters
    ? `<div style="margin-bottom:4px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
          <span style="font-size:11px;font-weight:700;letter-spacing:.09em;color:#64748b;text-transform:uppercase">Signals to watch</span>
          <span style="font-size:11px;color:#94a3b8">${clusters.length} clusters</span>
        </div>
        ${signalHero(clusters)}
        ${signalGrid(clusters)}
      </div>`
    : `<div class="notice" style="margin-bottom:1rem">Run analysis to see trend signals.</div>`;

  return `
    ${signalsSection}
    <div style="border-top:1px solid #e2e8f0;margin:4px 0 16px"></div>
    <div>${metricGrid(data.summary.metrics)}</div>
    <div class="panel-grid" style="margin-top:1rem">
      ${chartPanel("sentiment", "Sentiment distribution")}
      ${chartPanel("flags", "Signal flags")}
      ${chartPanel("complaints", "Complaint themes")}
    </div>
    <div class="panel-grid two" style="margin-top:0.75rem">
      <section class="panel chart-panel">
        <div class="panel-head"><h3>Velocity × Z-score quadrant</h3><small>color = confidence</small></div>
        <div id="quadrantChart" style="height:280px"></div>
      </section>
      <section class="panel chart-panel">
        <div class="panel-head"><h3>Trend leaderboard</h3><small>by velocity</small></div>
        <div id="leaderboardChart" style="height:280px"></div>
      </section>
    </div>
    <div class="panel-grid two" style="margin-top:0.75rem">
      <section class="panel chart-panel">
        <div class="panel-head"><h3>Negative mentions over time</h3><small>weekly</small></div>
        <div id="negTimeChart" style="height:220px"></div>
      </section>
      <section class="panel chart-panel">
        <div class="panel-head"><h3>Sentiment over time</h3><small>weekly stacked</small></div>
        <div id="sentTimeChart" style="height:220px"></div>
      </section>
    </div>
    <div class="panel-grid" style="margin-top:0.75rem">
      ${chartPanel("priority", "Priority map", "volume × negativity")}
    </div>`;
}
