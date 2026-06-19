// charts.js — ECharts rendering: light theme, instance registry (dispose on
// re-render), pure spec->option mapping for the 6 chart types, and a mount pass.
// ECharts is a global (window.echarts) loaded via <script> in index.html.
import { fmt, pct, humanLabel, debounce, esc } from "./state.js";

const THEME_NAME = "redditgm";
let themeRegistered = false;

// Register the custom light theme once. Subsequent calls are no-ops.
function ensureTheme() {
  if (themeRegistered) return;
  echarts.registerTheme(THEME_NAME, {
    // Six-color palette covering the main semantic categories.
    color: ["#2563eb", "#0d9488", "#d97706", "#7c3aed", "#db2777", "#64748b"],
    backgroundColor: "transparent",
    textStyle: { fontFamily: "inherit", color: "#475569" },
    categoryAxis: { axisLine: { lineStyle: { color: "#cbd5e1" } }, axisTick: { show: false }, axisLabel: { color: "#475569" }, splitLine: { show: false } },
    valueAxis: { axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: "#475569" }, splitLine: { lineStyle: { color: "#eef2f6" } } },
    tooltip: { backgroundColor: "#0f172a", borderWidth: 0, textStyle: { color: "#f8fafc" } },
    legend: { textStyle: { color: "#475569" } },
  });
  themeRegistered = true;
}

// Map from DOM element → ECharts instance so we can dispose on re-render.
const instances = new Map();

// Dispose the ECharts instance attached to el (if any) and remove it from the registry.
export function disposeChart(el) {
  const inst = instances.get(el);
  if (inst) { inst.dispose(); instances.delete(el); }
}

// Dispose every live instance — call on full view teardown.
export function disposeAll() {
  for (const inst of instances.values()) inst.dispose();
  instances.clear();
}

// Bind a single debounced window resize handler that resizes all live charts.
let resizeBound = false;
export function bindResize() {
  if (resizeBound) return;
  window.addEventListener("resize", debounce(() => {
    for (const inst of instances.values()) inst.resize();
  }, 150));
  resizeBound = true;
}

// Internal: apply value_format hint to a number before it appears in an axis label.
function fmtValue(v, format) {
  const n = Number(v || 0);
  switch (format) {
    case "pct": return pct(n);
    case "score": return n.toFixed(1);
    case "correlation": return n.toFixed(2);
    default: return fmt.format(Math.round(n));
  }
}

// ---------------------------------------------------------------------------
// Spec → ECharts option builders — one per chart type.
// ---------------------------------------------------------------------------

// Horizontal bar chart. Caps at 20 rows. Supports per-key color_map.
function optBar(spec, rows) {
  const xf = spec.x_field, yf = spec.y_field || "count";
  const cm = spec.color_map || {};
  const slice = rows.slice(0, 20);
  const cats = slice.map((r) => humanLabel(r[xf]));
  const data = slice.map((r) => {
    const key = String(r[xf] ?? "");
    return { value: Number(r[yf] || 0), itemStyle: cm[key] ? { color: cm[key] } : undefined };
  });
  return {
    grid: { left: 8, right: 24, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: "value", axisLabel: { formatter: (v) => fmtValue(v, spec.value_format) } },
    yAxis: { type: "category", data: cats, inverse: true },
    tooltip: { trigger: "axis", valueFormatter: (v) => fmtValue(v, spec.value_format) },
    series: [{ type: "bar", data, barMaxWidth: 22 }],
  };
}

// Clustered (grouped) bar chart. Caps at 8 categories.
function optGroupedBar(spec, rows) {
  const xf = spec.x_field, keys = spec.series_keys || [];
  const slice = rows.slice(0, 8);
  const cats = slice.map((r) => humanLabel(r[xf]));
  const series = keys.map((k) => ({ name: humanLabel(k), type: "bar", data: slice.map((r) => Number(r[k] || 0)) }));
  return {
    legend: {}, grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
    xAxis: { type: "category", data: cats },
    yAxis: { type: "value", axisLabel: { formatter: (v) => fmtValue(v, spec.value_format) } },
    tooltip: { trigger: "axis", valueFormatter: (v) => fmtValue(v, spec.value_format) },
    series,
  };
}

// Stacked bar from long-format data (one row per x + series combination).
function optStackedLong(spec, rows) {
  const xf = spec.x_field || "vehicle_mentioned";
  const sf = spec.series_field || "issue_severity";
  const vf = spec.value_field || "count";
  const order = spec.series_order || [];
  const cm = spec.color_map || {};
  const groups = {}; const found = new Set(order);
  // Pivot: group[x][series] = total
  for (const r of rows) {
    const x = String(r[xf] ?? ""), s = String(r[sf] ?? ""), v = Number(r[vf] || 0);
    (groups[x] = groups[x] || {})[s] = (groups[x][s] || 0) + v;
    found.add(s);
  }
  // Respect series_order; append any remaining series alphabetically.
  const names = [...order, ...[...found].filter((s) => !order.includes(s))];
  const cats = Object.keys(groups);
  const series = names.map((s) => ({
    name: humanLabel(s), type: "bar", stack: "total",
    itemStyle: cm[s] ? { color: cm[s] } : undefined,
    data: cats.map((x) => groups[x][s] || 0),
  }));
  return {
    legend: {}, grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
    xAxis: { type: "category", data: cats.map(humanLabel) },
    yAxis: { type: "value" },
    tooltip: { trigger: "axis" },
    series,
  };
}

// 100% stacked bar from wide-format data (one row per x, series as columns).
function optStacked100(spec, rows) {
  const xf = spec.x_field || "vehicle";
  const order = spec.series_order || [];
  const cm = spec.color_map || {};
  const slice = rows.slice(0, 15);
  // Derive series keys from the first row, excluding the x field.
  const keys = Object.keys(slice[0] || {}).filter((k) => k !== xf);
  const names = [...order.filter((s) => keys.includes(s)), ...keys.filter((s) => !order.includes(s))];
  const cats = slice.map((r) => humanLabel(r[xf]));
  // Compute row totals for normalization.
  const totals = slice.map((r) => names.reduce((a, s) => a + Number(r[s] || 0), 0) || 1);
  const series = names.map((s) => ({
    name: humanLabel(s), type: "bar", stack: "total",
    itemStyle: cm[s] ? { color: cm[s] } : undefined,
    data: slice.map((r, i) => Number(((Number(r[s] || 0) / totals[i]) * 100).toFixed(1))),
  }));
  return {
    legend: {}, grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
    xAxis: { type: "category", data: cats },
    yAxis: { type: "value", max: 100, axisLabel: { formatter: "{value}%" } },
    tooltip: { trigger: "axis", valueFormatter: (v) => pct(v) },
    series,
  };
}

// Scatter plot with optional color coding and size-by-volume encoding.
function optScatter(spec, rows) {
  const xf = spec.x_field || "volume", yf = spec.y_field || "pct_negative";
  const lf = spec.label_field || "theme", cf = spec.color_field || "priority";
  const cm = spec.color_map || {};
  const slice = rows.slice(0, 40);
  const data = slice.map((r) => ({
    value: [Number(r[xf] || 0), Number(r[yf] || 0)],
    name: humanLabel(r[lf]),
    itemStyle: { color: cm[String(r[cf] ?? "")] || "#64748b" },
  }));
  return {
    grid: { left: 8, right: 24, top: 16, bottom: 36, containLabel: true },
    xAxis: { type: "value", name: humanLabel(xf), nameLocation: "middle", nameGap: 26 },
    yAxis: { type: "value", name: humanLabel(yf), nameLocation: "middle", nameGap: 40, axisLabel: { formatter: "{value}%" } },
    tooltip: { trigger: "item", formatter: (p) => `${p.data.name}<br/>${fmt.format(p.value[0])} / ${pct(p.value[1])}` },
    series: [{ type: "scatter", data, symbolSize: (d) => Math.max(10, Math.min(40, d[0] * 4)) }],
  };
}

// Heatmap. Accepts either structured {rows, columns, values} or a flat correlation dict.
function optHeatmap(spec, data) {
  if (data && data.rows !== undefined && data.columns !== undefined) {
    // Structured format: explicit rows/columns/values arrays.
    const { rows, columns, values } = data;
    const cells = []; let max = 1;
    rows.forEach((_, i) => columns.forEach((__, j) => {
      const v = values[i] && values[i][j];
      if (v === null || v === undefined) return;
      cells.push([j, i, Number(v)]);
      if (Number(v) > max) max = Number(v);
    }));
    return {
      grid: { left: 8, right: 8, top: 8, bottom: 60, containLabel: true },
      xAxis: { type: "category", data: columns.map(humanLabel), axisLabel: { rotate: 45 } },
      yAxis: { type: "category", data: rows.map(humanLabel) },
      visualMap: { min: 0, max, calculable: true, orient: "horizontal", left: "center", bottom: 0, inRange: { color: ["#fdeceb", "#9d2d25"] } },
      tooltip: { position: "top" },
      series: [{ type: "heatmap", data: cells }],
    };
  }
  // Flat correlation dict format: data[rowKey][colKey] = value.
  const cols = Object.keys(data || {});
  const cells = [];
  cols.forEach((rk, i) => cols.forEach((ck, j) => {
    const v = data[rk] && data[rk][ck];
    if (v === null || v === undefined) return;
    cells.push([j, i, Number(Number(v).toFixed(2))]);
  }));
  return {
    grid: { left: 8, right: 8, top: 8, bottom: 60, containLabel: true },
    xAxis: { type: "category", data: cols.map(humanLabel), axisLabel: { rotate: 45 } },
    yAxis: { type: "category", data: cols.map(humanLabel) },
    visualMap: { min: -1, max: 1, calculable: true, orient: "horizontal", left: "center", bottom: 0, inRange: { color: ["#9d2d25", "#f1efe8", "#166044"] } },
    tooltip: { position: "top" },
    series: [{ type: "heatmap", data: cells }],
  };
}

// Quadrant scatter: velocity (x) vs z-score (y), colored by confidence banner.
// Clusters without valid velocity or zscore signals are excluded.
function optQuadrant(clusters) {
  const confColor = { high: "#16a34a", medium: "#d97706", low: "#94a3b8" };
  const data = clusters
    .filter((c) => c.trend_signal?.velocity?.valid && c.trend_signal?.zscore?.valid)
    .map((c) => {
      const sig = c.trend_signal;
      return {
        value: [Number(sig.velocity.velocity ?? 0), Number(sig.zscore.zscore ?? 0)],
        name: c.label?.short_label || `Cluster ${c.cluster_id}`,
        itemStyle: { color: confColor[sig.confidence_banner] || "#94a3b8" },
      };
    });
  return {
    grid: { left: 16, right: 24, top: 24, bottom: 36, containLabel: true },
    xAxis: { type: "value", name: "Velocity", nameLocation: "middle", nameGap: 28 },
    yAxis: { type: "value", name: "Z-score", nameLocation: "middle", nameGap: 40 },
    tooltip: {
      trigger: "item",
      // esc() sanitizes cluster labels to prevent XSS in tooltip HTML.
      formatter: (p) => `${esc(p.data.name)}<br/>Velocity: ${Number(p.value[0]).toFixed(2)}<br/>Z-score: ${Number(p.value[1]).toFixed(2)}`,
    },
    series: [{
      type: "scatter",
      data,
      symbolSize: 12,
      // Crosshair lines at origin to divide the quadrants.
      markLine: {
        silent: true,
        symbol: "none",
        lineStyle: { color: "#cbd5e1", type: "dashed" },
        data: [{ xAxis: 0 }, { yAxis: 0 }],
      },
    }],
  };
}

// Horizontal bar leaderboard ranked by velocity, capped at 15 clusters.
function optLeaderboard(clusters) {
  const confColor = { high: "#16a34a", medium: "#d97706", low: "#94a3b8" };
  const sorted = [...clusters]
    .filter((c) => c.trend_signal?.velocity?.valid)
    .sort((a, b) => (b.trend_signal.velocity.velocity ?? 0) - (a.trend_signal.velocity.velocity ?? 0))
    .slice(0, 15);
  const cats = sorted.map((c) => c.label?.short_label || `Cluster ${c.cluster_id}`);
  const data = sorted.map((c) => ({
    // Convert velocity fraction to percentage points for readability.
    value: Number(((c.trend_signal.velocity.velocity ?? 0) * 100).toFixed(1)),
    itemStyle: { color: confColor[c.trend_signal?.confidence_banner] || "#94a3b8" },
  }));
  return {
    grid: { left: 8, right: 48, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: "value", axisLabel: { formatter: "{value}%" } },
    yAxis: { type: "category", data: cats, inverse: true },
    tooltip: { trigger: "axis", valueFormatter: (v) => `${v}%` },
    series: [{ type: "bar", data, barMaxWidth: 20 }],
  };
}

// Simple line chart for a single timeseries field in a tsd (time-series data) object.
// tsd must have a `buckets` array of x-axis labels and a `[seriesKey]` array of values.
function optLineTimeseries(tsd, seriesKey) {
  const src = tsd || {};  // guard against null on first load
  return {
    grid: { left: 8, right: 24, top: 16, bottom: 8, containLabel: true },
    xAxis: { type: "category", data: src.buckets || [], axisLabel: { rotate: 30, fontSize: 10 } },
    yAxis: { type: "value" },
    tooltip: { trigger: "axis" },
    series: [{ type: "line", data: src[seriesKey] || [], smooth: true, areaStyle: { opacity: 0.12 } }],
  };
}

// Stacked area chart for sentiment timeseries (positive/neutral/negative bands).
// Expects tsd.buckets, tsd.positive, tsd.neutral, tsd.negative arrays.
function optStackedAreaTimeseries(tsd) {
  const palette = { positive: "#16a34a", neutral: "#94a3b8", negative: "#dc2626" };
  const series = ["positive", "neutral", "negative"].map((key) => ({
    name: key.charAt(0).toUpperCase() + key.slice(1),
    type: "line",
    stack: "sentiment",   // ECharts stacks series that share the same stack key.
    smooth: true,
    areaStyle: {},
    itemStyle: { color: palette[key] },
    data: tsd[key] || [],
  }));
  return {
    legend: {},
    grid: { left: 8, right: 24, top: 32, bottom: 8, containLabel: true },
    xAxis: { type: "category", data: tsd.buckets || [], axisLabel: { rotate: 30, fontSize: 10 } },
    yAxis: { type: "value" },
    tooltip: { trigger: "axis" },
    series,
  };
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

// Convert a chart spec + data payload into an ECharts option object.
// Pure function — does not touch the DOM or call echarts.*
export function buildChartOption(spec, data) {
  const rows = Array.isArray(data) ? data : [];
  switch (spec.type) {
    case "bar": return optBar(spec, rows);
    case "grouped_bar": return optGroupedBar(spec, rows);
    case "stacked_bar": return optStackedLong(spec, rows);
    case "stacked_bar_100": return optStacked100(spec, rows);
    case "scatter": return optScatter(spec, rows);
    case "heatmap": return optHeatmap(spec, data);
    default: return null;
  }
}

// Render a chart into el. Disposes any existing instance first.
// Shows a fallback notice if row count is below spec.minimum_rows.
export function renderChartInto(el, spec, data) {
  if (!el) return;
  disposeChart(el);
  const rows = Array.isArray(data) ? data : [];
  const minRows = spec.minimum_rows == null ? 1 : spec.minimum_rows;
  if (spec.type !== "heatmap" && rows.length < minRows) {
    el.innerHTML = `<div class="notice">${esc(spec.fallback || "Insufficient data.")}</div>`;
    return;
  }
  const option = buildChartOption(spec, data);
  if (!option) { el.innerHTML = `<div class="notice">Unsupported chart type.</div>`; return; }
  ensureTheme();
  // Lazy-read reduced-motion preference at render time (not module load).
  const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  const inst = echarts.init(el, THEME_NAME, { renderer: "canvas" });
  inst.setOption(Object.assign({ animation: !reduceMotion }, option));
  instances.set(el, inst);
}

// Generic ECharts mount: disposes any existing instance on el, then renders option.
// Use this when you have a fully-built option object and don't need spec dispatch.
export function renderIntoEl(el, option) {
  if (!el || !option) return;
  disposeChart(el);
  ensureTheme();
  // Read reduced-motion preference at render time (user may toggle it while app is open).
  const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  const inst = echarts.init(el, THEME_NAME, { renderer: "canvas" });
  inst.setOption(Object.assign({ animation: !reduceMotion }, option));
  instances.set(el, inst);
}

// Walk all [data-chart] elements under root, match each id to specs/data in payload, and render.
export function mountViewCharts(root, payload) {
  if (!root || !payload) return;
  const specs = payload.chart_specs || {};
  const cdata = payload.chart_data || {};
  root.querySelectorAll("[data-chart]").forEach((el) => {
    const id = el.getAttribute("data-chart");
    const spec = specs[id];
    if (!spec) { el.innerHTML = `<div class="notice">Chart spec not found: ${esc(id)}.</div>`; return; }
    renderChartInto(el, spec, cdata[id]);
  });
}

// Named exports for the M3 dashboard opt builders and sparkline renderer.
export const buildQuadrantOption = optQuadrant;
export const buildLeaderboardOption = optLeaderboard;
export const buildLineTimeseriesOption = optLineTimeseries;
export const buildStackedAreaOption = optStackedAreaTimeseries;

// Mini inline sparkline: fixed 80×32 canvas, no axes, no tooltip.
// weeklyData is a plain array of numbers (one per week bucket).
export function renderSparkline(el, weeklyData) {
  if (!el || !weeklyData?.length) return;
  disposeChart(el);
  ensureTheme();
  // Fixed pixel size: sparklines are decorative and must not reflow.
  const inst = echarts.init(el, THEME_NAME, { renderer: "canvas", width: 80, height: 32 });
  inst.setOption({
    animation: false,
    grid: { left: 0, right: 0, top: 2, bottom: 2 },
    xAxis: { type: "category", show: false, data: weeklyData.map((_, i) => i) },
    yAxis: { type: "value", show: false },
    series: [{ type: "line", data: weeklyData, smooth: true, symbol: "none", lineStyle: { width: 1.5 } }],
  });
  instances.set(el, inst);
}
