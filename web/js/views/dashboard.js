// dashboard.js — signals-first landing: hero card, 2-col signal grid,
// run metrics, and chart panels (3 existing + 4 new trend charts).
import { state, esc, fmt, humanLabel } from "../state.js";
import { panel, chartPanel, chartDataDetails, metricGrid, emptyState } from "../components.js";
import {
  renderIntoEl, renderSparkline, renderAccessibleTable,
  buildQuadrantOption, buildLeaderboardOption,
  buildLineTimeseriesOption, buildStackedAreaOption,
} from "../charts.js";
import { renderSafeMarkdown } from "../markdown.js";

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

function trendEmptyState(detail) {
  return `<div class="notice">${esc(detail || "This chart needs more dated records.")}</div>`;
}

function reportCard(kind, title, cardId, previewId) {
  const report = state.reportData?.[kind] || {};
  const formats = report.formats || {};
  const markdownReady = formats.markdown === "ready" && Boolean(report.markdown);
  const pdfReady = formats.pdf === "ready";
  const tag = encodeURIComponent(state.tag);
  const markdownHref = kind === "synthesis"
    ? `/api/download/report?tag=${tag}`
    : `/api/download/trend-md?tag=${tag}`;
  const pdfHref = kind === "synthesis"
    ? `/api/download/briefing-pdf?tag=${tag}`
    : `/api/download/trend-pdf?tag=${tag}`;
  const warning = kind === "trend" ? state.trendBriefingJobStatus?.warning : "";

  return `<section id="${cardId}" class="panel report-preview-card" aria-labelledby="${cardId}Title">
    <div class="panel-head report-preview-head">
      <div>
        <span class="report-preview-kicker">Published report</span>
        <h3 id="${cardId}Title">${esc(title)}</h3>
      </div>
      <div class="report-preview-actions" aria-label="${esc(title)} downloads">
        ${markdownReady ? `<a class="export-chip" href="${markdownHref}">Markdown</a>` : `<span class="export-chip is-disabled">Markdown unavailable</span>`}
        ${pdfReady ? `<a class="export-chip" href="${pdfHref}">PDF</a>` : `<span class="export-chip is-disabled">PDF unavailable</span>`}
      </div>
    </div>
    ${warning ? `<div class="notice" role="status">${esc(warning)}</div>` : ""}
    <div id="${previewId}" class="report-preview-scroll">
      ${markdownReady
        ? `<article class="report-markdown">${renderSafeMarkdown(report.markdown)}</article>`
        : `<div class="report-preview-empty"><strong>No report published yet.</strong><span>Run the analysis pipeline to generate this briefing.</span></div>`}
    </div>
  </section>`;
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
      <div id="sparkline-${c.cluster_id}" aria-hidden="true" style="width:80px;height:32px;flex-shrink:0"></div>
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
    const validQuadrantClusters = clusters.filter(
      (c) => c.trend_signal?.velocity?.valid && c.trend_signal?.zscore?.valid,
    );
    const validLeaderboardClusters = clusters.filter((c) => c.trend_signal?.velocity?.valid);
    // Mount the velocity × z-score quadrant scatter chart.
    const quadEl = document.getElementById("quadrantChart");
    if (quadEl) {
      if (validQuadrantClusters.length) {
        renderIntoEl(quadEl, buildQuadrantOption(validQuadrantClusters));
      } else {
        quadEl.innerHTML = trendEmptyState(
          "Velocity needs at least 2 recent and 3 baseline records; Z-score needs at least 4 periods.",
        );
      }
      renderAccessibleTable(
        document.querySelector('[data-chart-table="quadrantChart"]'),
        "Velocity and Z-score data",
        ["signal", "velocity", "z_score", "confidence"],
        validQuadrantClusters.map((c) => ({
            signal: c.label?.short_label || `Cluster ${c.cluster_id}`,
            velocity: c.trend_signal.velocity.velocity,
            z_score: c.trend_signal.zscore.zscore,
            confidence: c.trend_signal.confidence_banner,
        })),
      );
    }

    // Mount the trend leaderboard bar chart.
    const lbEl = document.getElementById("leaderboardChart");
    if (lbEl) {
      if (validLeaderboardClusters.length) {
        renderIntoEl(lbEl, buildLeaderboardOption(validLeaderboardClusters));
      } else {
        lbEl.innerHTML = trendEmptyState(
          "Velocity needs at least 2 recent and 3 baseline records before signals can be ranked.",
        );
      }
      renderAccessibleTable(
        document.querySelector('[data-chart-table="leaderboardChart"]'),
        "Trend leaderboard data",
        ["signal", "velocity", "confidence"],
        validLeaderboardClusters.map((c) => ({
            signal: c.label?.short_label || `Cluster ${c.cluster_id}`,
            velocity: c.trend_signal.velocity.velocity,
            confidence: c.trend_signal.confidence_banner,
        })),
      );
    }

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
    // Mount the collection-volume-resistant negative-share line chart.
    const negEl = document.getElementById("negTimeChart");
    if (negEl) {
      renderIntoEl(negEl, buildLineTimeseriesOption(tsd, "negative_share_pct"));
      renderAccessibleTable(
        document.querySelector('[data-chart-table="negTimeChart"]'),
        `Negative share by ${tsd.granularity || "time bucket"}`,
        ["bucket", "negative_share_pct", "total"],
        (tsd.buckets || []).map((bucket, index) => ({
          bucket,
          negative_share_pct: tsd.negative_share_pct?.[index],
          total: tsd.total?.[index],
        })),
      );
    }

    // Mount weekly stacked-area sentiment chart.
    const sentEl = document.getElementById("sentTimeChart");
    if (sentEl) {
      renderIntoEl(sentEl, buildStackedAreaOption(tsd));
      renderAccessibleTable(
        document.querySelector('[data-chart-table="sentTimeChart"]'),
        `Sentiment counts by ${tsd.granularity || "time bucket"}`,
        ["bucket", "positive", "neutral", "negative", "total"],
        (tsd.buckets || []).map((bucket, index) => ({
          bucket,
          positive: tsd.positive?.[index],
          neutral: tsd.neutral?.[index],
          negative: tsd.negative?.[index],
          total: tsd.total?.[index],
        })),
      );
    }
  } else {
    const detail = tsd?.detail || "Run analysis on records from at least two UTC dates to render this chart.";
    ["negTimeChart", "sentTimeChart"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = trendEmptyState(detail);
    });
  }
}

// ── Main view function ───────────────────────────────────────────────────────

export function dashboard() {
  const data = state.data;
  // Return empty state template when no run data is loaded.
  if (!data?.summary?.metrics?.total_rows) return emptyState();

  const td = state.trendsData;
  const tsd = state.timeseriesData;
  const granularityLabel = tsd?.granularity_label || "time-bucketed";
  // Only show signals section when trends have been fetched and returned clusters.
  const clusters = td?.ok && td.clusters?.length ? td.clusters : null;
  const signalsSection = clusters
    ? `<div data-signal-grid style="margin-bottom:4px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
          <span style="font-size:11px;font-weight:700;letter-spacing:.09em;color:#64748b;text-transform:uppercase">Signals to watch</span>
          <span style="font-size:11px;color:#94a3b8">${clusters.length} clusters</span>
        </div>
        ${signalHero(clusters)}
        ${signalGrid(clusters)}
      </div>`
    : `<div class="notice" style="margin-bottom:1rem">Run analysis to see trend signals.</div>`;

  return `
    <div>${metricGrid(data.summary.metrics)}</div>
    <section class="insight-family insight-family--predefined" aria-labelledby="predefined-heading">
      <div class="insight-family-head">
        <div><span class="insight-family-kicker">Model taxonomy</span><h2 id="predefined-heading">Predefined categories</h2></div>
        <p>These are fixed choices assigned by the model—not hand-verified labels and not your subreddit list.</p>
      </div>
      ${reportCard("synthesis", "Synthesis briefing", "synthesisReportCard", "synthesisReportPreview")}
      <div class="panel-grid">
        ${chartPanel("sentiment", "Sentiment distribution")}
        ${chartPanel("flags", "Signal flags")}
        ${chartPanel("complaints", "Complaint themes")}
      </div>
      <div class="panel-grid" style="margin-top:0.75rem">
        ${chartPanel("vehicles", "Vehicles")}
      </div>
      <p class="inline-glossary"><strong>Glossary</strong> · not_applicable = not a complaint</p>
    </section>

    <section class="insight-family insight-family--discovered" aria-labelledby="discovered-heading">
      <div class="insight-family-head">
        <div><span class="insight-family-kicker">Cluster signals</span><h2 id="discovered-heading">Discovered from your data</h2></div>
        <p>These signals are found by clustering your records. They are emerging themes—not fixed categories or subreddit lists.</p>
      </div>
      ${signalsSection}
      ${reportCard("trend", "Trend report", "trendReportCard", "trendReportPreview")}
      <div class="panel-grid two" style="margin-top:0.75rem">
        <section class="panel chart-panel">
          <div class="panel-head"><h3>Velocity × Z-score quadrant</h3><small>color = confidence</small></div>
          <div id="quadrantChart" role="img" aria-label="Velocity by Z-score quadrant chart" style="height:280px"></div>
          ${chartDataDetails("quadrantChart", "Velocity × Z-score quadrant")}
        </section>
        <section class="panel chart-panel">
          <div class="panel-head"><h3>Trend leaderboard</h3><small>by velocity</small></div>
          <div id="leaderboardChart" role="img" aria-label="Trend leaderboard chart" style="height:280px"></div>
          ${chartDataDetails("leaderboardChart", "Trend leaderboard")}
        </section>
      </div>
      <div class="panel-grid two" style="margin-top:0.75rem">
        <section class="panel chart-panel">
          <div class="panel-head"><h3>Negative share over time</h3><small>${esc(granularityLabel)} · rate</small></div>
          <div id="negTimeChart" role="img" aria-label="Negative mentions over time chart" style="height:220px"></div>
          ${chartDataDetails("negTimeChart", "Negative mentions over time")}
        </section>
        <section class="panel chart-panel">
          <div class="panel-head"><h3>Sentiment volume over time</h3><small>${esc(granularityLabel)} counts · affected by collection volume</small></div>
          <div id="sentTimeChart" role="img" aria-label="Sentiment over time chart" style="height:220px"></div>
          ${chartDataDetails("sentTimeChart", "Sentiment over time")}
        </section>
      </div>
    </section>`;
}
