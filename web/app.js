const state = {
  tag: "gm_vehicle_on_demand",
  view: "dashboard",
  data: null,
  report: "",
  filters: {},
  classifyJobStatus: null,
  exportJobStatus: null,
  collectPollTimer: null,
  classifyPollTimer: null,
  exportPollTimer: null,
  // Phase 5: trends
  trendsData: null,
  trendJobStatus: null,
  trendBriefingJobStatus: null,
  trendPollTimer: null,
  trendBriefingPollTimer: null,
  // Phase 6: Q&A
  qaJobStatus: null,
  qaHits: null,
  qaAnswer: null,
  qaPollTimer: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const fmt = new Intl.NumberFormat("en-US");
const pct = (value) => `${Number(value || 0).toFixed(1)}%`;
const fileSize = (bytes) => {
  const value = Number(bytes || 0);
  if (!value) return "0 KB";
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(value / 1024))} KB`;
};
const esc = (value) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

function apiUrl(path, params = {}) {
  const url = new URL(path, window.location.origin);
  Object.entries(params).forEach(([key, value]) => {
    if (Array.isArray(value)) {
      value.forEach((item) => item && url.searchParams.append(key, item));
    } else if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, value);
    }
  });
  return url;
}

async function request(path, options = {}) {
  const response = await fetch(path, options);
  const text = await response.text();
  let body = {};
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { detail: text };
  }
  if (!response.ok) {
    throw new Error(body.detail || `Request failed with ${response.status}`);
  }
  return body;
}

function setNotice(message = "", type = "") {
  const bar = $("#statusBar");
  if (!message) {
    bar.innerHTML = "";
    return;
  }
  bar.innerHTML = `<div class="notice ${type}" role="status">${esc(message)}</div>`;
}

function setBusy(button, busy, label) {
  if (!button) return;
  button.disabled = busy;
  if (label) button.textContent = busy ? "Working..." : label;
}

function compactText(value, fallback = "unknown") {
  const text = String(value ?? "").trim();
  return text || fallback;
}

function humanLabel(value) {
  return compactText(value).replaceAll("_", " ");
}

function topItem(rows, labelKey, valueKey = "count") {
  if (!rows?.length) return null;
  return [...rows].sort((a, b) => Number(b[valueKey] || 0) - Number(a[valueKey] || 0))[0];
}

// ---------------------------------------------------------------------------
// Filters
// ---------------------------------------------------------------------------

function buildFilterParams() {
  const f = state.filters || {};
  return {
    sentiment: f.sentiment || [],
    vehicle: f.vehicle || [],
    subreddit: f.subreddit || [],
    severity: f.severity || [],
    comment_type: f.comment_type || [],
    competitor: f.competitor || [],
    search: f.search || "",
    min_score: f.min_score || undefined,
  };
}

function hasActiveFilters() {
  const f = state.filters || {};
  return (
    (f.sentiment?.length || 0) +
    (f.vehicle?.length || 0) +
    (f.subreddit?.length || 0) +
    (f.severity?.length || 0) +
    (f.comment_type?.length || 0) +
    (f.competitor?.length || 0) +
    (f.search ? 1 : 0) +
    (f.min_score ? 1 : 0)
  ) > 0;
}

function filterPanel() {
  const opts = state.data?.filterOptions || {};
  const f = state.filters || {};
  if (!Object.keys(opts).length) return "";

  const sel = (key, label, options, apiKey = key) => {
    const selected = f[apiKey] || [];
    const opts_html = options.map((o) =>
      `<option value="${esc(o)}" ${selected.includes(o) ? "selected" : ""}>${esc(o)}</option>`
    ).join("");
    return `<div class="filter-control">
      <label class="filter-label">${esc(label)}</label>
      <select id="filter-${key}" multiple size="3" class="filter-select" data-filter-key="${esc(apiKey)}">${opts_html}</select>
    </div>`;
  };

  const activeCount = hasActiveFilters() ? ` (${Object.values(state.filters).flat().filter(Boolean).length} active)` : "";
  return `<section class="panel filter-panel">
    <div class="panel-head">
      <h3>Filters${activeCount ? `<span class="filter-active-badge">${activeCount}</span>` : ""}</h3>
      <button id="clearFiltersBtn" class="button secondary small">Clear all</button>
    </div>
    <div class="filter-grid">
      ${opts.sentiment?.length ? sel("sentiment", "Sentiment", opts.sentiment) : ""}
      ${opts.vehicle?.length ? sel("vehicle", "Vehicle", opts.vehicle) : ""}
      ${opts.subreddit?.length ? sel("subreddit", "Subreddit", opts.subreddit) : ""}
      ${opts.severity?.length ? sel("severity", "Severity", opts.severity) : ""}
      ${opts.commentType?.length ? sel("commentType", "Comment type", opts.commentType, "comment_type") : ""}
      ${opts.competitor?.length ? sel("competitor", "Competitor", opts.competitor) : ""}
      <div class="filter-control">
        <label class="filter-label">Search text</label>
        <input id="filter-search" type="text" class="filter-input" value="${esc(f.search || "")}" placeholder="keyword…">
      </div>
      <div class="filter-control">
        <label class="filter-label">Min score</label>
        <input id="filter-minscore" type="number" class="filter-input" value="${f.min_score || ""}" placeholder="0">
      </div>
    </div>
  </section>`;
}

function bindFilterEvents() {
  $$(".filter-select").forEach((sel) => {
    sel.addEventListener("change", applyFilters);
  });
  const searchInput = $("#filter-search");
  if (searchInput) {
    searchInput.addEventListener("input", debounce(applyFilters, 420));
  }
  $("#filter-minscore")?.addEventListener("change", applyFilters);
  $("#clearFiltersBtn")?.addEventListener("click", clearFilters);
}

function applyFilters() {
  const f = {};
  $$(".filter-select").forEach((sel) => {
    const key = sel.dataset.filterKey || sel.id.replace("filter-", "");
    const values = Array.from(sel.selectedOptions).map((o) => o.value);
    if (values.length) f[key] = values;
  });
  const searchEl = $("#filter-search");
  if (searchEl?.value.trim()) f.search = searchEl.value.trim();
  const minEl = $("#filter-minscore");
  if (minEl?.value) f.min_score = parseFloat(minEl.value);
  state.filters = f;
  loadRun();
}

function clearFilters() {
  state.filters = {};
  loadRun();
}

// ---------------------------------------------------------------------------
// Chart renderers (Phase 3 — consume chart_specs + chart_data)
// ---------------------------------------------------------------------------

function formatValue(value, format) {
  const n = Number(value || 0);
  switch (format) {
    case "pct": return pct(n);
    case "count_pct": return fmt.format(Math.round(n));
    case "score": return n.toFixed(1);
    case "correlation": return n.toFixed(2);
    default: return fmt.format(Math.round(n));
  }
}

function renderChart(id) {
  const spec = state.data?.chart_specs?.[id];
  const rawData = state.data?.chart_data?.[id];
  if (!spec) return `<div class="notice">Chart spec not found: ${esc(id)}.</div>`;

  const rows = Array.isArray(rawData) ? rawData : [];
  const minRows = spec.minimum_rows ?? 1;

  // Check minimum_rows for list-based charts
  if (spec.type !== "heatmap" && rows.length < minRows) {
    return `<div class="notice">${esc(spec.fallback || "Insufficient data.")}</div>`;
  }

  let chart;
  switch (spec.type) {
    case "bar":            chart = renderBarChart(spec, rows); break;
    case "stacked_bar":    chart = renderStackedBarLong(spec, rows); break;
    case "stacked_bar_100": chart = renderStackedBarWide(spec, rows); break;
    case "grouped_bar":    chart = renderGroupedBar(spec, rows); break;
    case "scatter":        chart = scatter(rows); break;
    case "heatmap":        chart = renderHeatmapChart(spec, rawData); break;
    default:               chart = renderCompactTable(rows);
  }

  const note = spec.quality_note
    ? `<small class="chart-note">${esc(spec.quality_note)}</small>`
    : "";
  return note ? `${chart}${note}` : chart;
}

function renderBarChart(spec, rows) {
  const xField = spec.x_field || Object.keys(rows[0] || {})[0] || "label";
  const yField = spec.y_field || "count";
  const colorMap = spec.color_map || {};
  const max = Math.max(...rows.map((r) => Number(r[yField] || 0)), 1);

  return `<div class="bar-list">${rows.slice(0, 20).map((row) => {
    const label = humanLabel(row[xField]);
    const value = Number(row[yField] || 0);
    const color = colorMap[String(row[xField] || "")] || "var(--blue)";
    return `<div class="bar-row">
      <strong title="${esc(label)}">${esc(label)}</strong>
      <div class="bar-track" aria-hidden="true">
        <div class="bar-fill" style="width:${(value / max) * 100}%;background:${color}"></div>
      </div>
      <span class="bar-value">${formatValue(value, spec.value_format)}</span>
    </div>`;
  }).join("")}</div>`;
}

function renderStackedBarLong(spec, rows) {
  // Long format: [{vehicle_mentioned: str, issue_severity: str, count: int}, ...]
  const xField = spec.x_field || "vehicle_mentioned";
  const seriesField = spec.series_field || "issue_severity";
  const valueField = spec.value_field || "count";
  const seriesOrder = spec.series_order || [];
  const colorMap = spec.color_map || {};

  // Pivot to groups
  const groups = {};
  const allSeries = new Set(seriesOrder);
  for (const row of rows) {
    const x = String(row[xField] || "");
    const s = String(row[seriesField] || "");
    const v = Number(row[valueField] || 0);
    if (!groups[x]) groups[x] = {};
    groups[x][s] = (groups[x][s] || 0) + v;
    allSeries.add(s);
  }
  const series = [
    ...seriesOrder,
    ...[...allSeries].filter((s) => !seriesOrder.includes(s)),
  ];

  const entries = Object.entries(groups);
  if (!entries.length) return `<div class="notice">${esc(spec.fallback || "No data.")}</div>`;

  return `<div class="bar-list stacked-bar-list">${entries.map(([x, vals]) => {
    const total = series.reduce((a, s) => a + (vals[s] || 0), 0) || 1;
    const segs = series.filter((s) => vals[s] > 0).map((s) => {
      const v = vals[s] || 0;
      const w = ((v / total) * 100).toFixed(1);
      const color = colorMap[s] || "var(--blue)";
      return `<div class="stacked-seg" style="width:${w}%;background:${color}" title="${esc(s)}: ${v}"></div>`;
    }).join("");
    return `<div class="bar-row">
      <strong title="${esc(humanLabel(x))}">${esc(humanLabel(x))}</strong>
      <div class="stacked-track">${segs}</div>
      <span class="bar-value">${fmt.format(total)}</span>
    </div>`;
  }).join("")}</div>`;
}

function renderStackedBarWide(spec, rows) {
  // Wide format: [{vehicle: str, category1: int, ...}, ...]
  const xField = spec.x_field || "vehicle";
  const seriesOrder = spec.series_order || [];
  const colorMap = spec.color_map || {};
  if (!rows.length) return `<div class="notice">${esc(spec.fallback || "No data.")}</div>`;

  const allKeys = Object.keys(rows[0]).filter((k) => k !== xField);
  const series = [
    ...seriesOrder.filter((s) => allKeys.includes(s)),
    ...allKeys.filter((s) => !seriesOrder.includes(s)),
  ];

  return `<div class="bar-list stacked-bar-list">${rows.slice(0, 15).map((row) => {
    const xVal = String(row[xField] || "");
    const total = series.reduce((a, s) => a + Number(row[s] || 0), 0) || 1;
    const segs = series.map((s) => {
      const v = Number(row[s] || 0);
      if (v <= 0) return "";
      const w = ((v / total) * 100).toFixed(1);
      const color = colorMap[s] || "var(--blue)";
      return `<div class="stacked-seg" style="width:${w}%;background:${color}" title="${esc(humanLabel(s))}: ${v}"></div>`;
    }).join("");
    return `<div class="bar-row">
      <strong title="${esc(humanLabel(xVal))}">${esc(humanLabel(xVal))}</strong>
      <div class="stacked-track">${segs}</div>
      <span class="bar-value">${fmt.format(Math.round(total))}</span>
    </div>`;
  }).join("")}</div>`;
}

function renderGroupedBar(spec, rows) {
  // Wide format: [{powertrain: str, metric1: float, ...}, ...]
  const xField = spec.x_field || "label";
  const seriesKeys = spec.series_keys || [];
  if (!rows.length || !seriesKeys.length) {
    return `<div class="notice">${esc(spec.fallback || "No data.")}</div>`;
  }
  const allVals = rows.flatMap((r) => seriesKeys.map((k) => Number(r[k] || 0)));
  const maxVal = Math.max(...allVals, 1);
  const colors = ["var(--blue)", "var(--teal)", "var(--amber)", "var(--red)", "var(--violet)"];

  return `<div class="grouped-bar-list">${rows.slice(0, 8).map((row) => {
    const label = humanLabel(String(row[xField] || ""));
    const subBars = seriesKeys.map((key, i) => {
      const v = Number(row[key] || 0);
      const w = ((v / maxVal) * 100).toFixed(1);
      return `<div class="bar-row sub-bar-row">
        <span class="bar-sub-label">${esc(humanLabel(key))}</span>
        <div class="bar-track"><div class="bar-fill" style="width:${w}%;background:${colors[i % colors.length]}"></div></div>
        <span class="bar-value">${formatValue(v, spec.value_format)}</span>
      </div>`;
    }).join("");
    return `<div class="grouped-group">
      <strong class="group-label">${esc(label)}</strong>
      ${subBars}
    </div>`;
  }).join("")}</div>`;
}

function renderHeatmapChart(spec, data) {
  if (!data) {
    return `<div class="notice">${esc(spec.fallback || "No heatmap data.")}</div>`;
  }

  // category_by_model format: {rows, columns, values}
  if (data.rows !== undefined && data.columns !== undefined) {
    const { rows, columns, values } = data;
    if (!rows.length || !columns.length) {
      return `<div class="notice">${esc(spec.fallback || "No data.")}</div>`;
    }
    const maxVal = Math.max(...values.flat().map((v) => v || 0), 1);
    const hdrs = `<tr><th></th>${columns.map((c) =>
      `<th title="${esc(c)}">${esc(humanLabel(c).slice(0, 14))}</th>`
    ).join("")}</tr>`;
    const body = rows.map((row, i) => {
      const cells = columns.map((_, j) => {
        const v = values[i]?.[j];
        if (v === null || v === undefined) return `<td class="hm-empty">—</td>`;
        const alpha = Math.min(0.85, (Number(v) / maxVal) * 0.85 + 0.12).toFixed(2);
        return `<td style="background:rgba(157,45,37,${alpha})" title="${v}">${v}</td>`;
      }).join("");
      return `<tr><th>${esc(humanLabel(row))}</th>${cells}</tr>`;
    }).join("");
    return `<div class="heatmap-wrap"><table class="heatmap-table"><thead>${hdrs}</thead><tbody>${body}</tbody></table></div>`;
  }

  // cooccurrence: dict-of-dicts
  if (typeof data === "object" && Object.keys(data).length > 0) {
    const cols = Object.keys(data);
    const hdrs = `<tr><th></th>${cols.map((c) =>
      `<th title="${esc(c)}">${esc(humanLabel(c).slice(0, 10))}</th>`
    ).join("")}</tr>`;
    const body = cols.map((row) => {
      const cells = cols.map((col) => {
        const v = data[row]?.[col];
        if (v === null || v === undefined) return "<td>—</td>";
        const n = Number(v) || 0;
        const bg =
          n > 0.5 ? `rgba(22,96,68,${Math.min(0.75, n * 0.75).toFixed(2)})`
          : n < -0.3 ? `rgba(157,45,37,${Math.min(0.55, Math.abs(n) * 0.55).toFixed(2)})`
          : "transparent";
        return `<td style="background:${bg}">${n.toFixed(2)}</td>`;
      }).join("");
      return `<tr><th>${esc(humanLabel(row).slice(0, 12))}</th>${cells}</tr>`;
    }).join("");
    return `<div class="heatmap-wrap"><table class="heatmap-table"><thead>${hdrs}</thead><tbody>${body}</tbody></table></div>`;
  }

  return `<div class="notice">${esc(spec.fallback || "No heatmap data.")}</div>`;
}

function renderCompactTable(rows) {
  if (!rows?.length) return `<div class="notice">No data.</div>`;
  const keys = Object.keys(rows[0] || {}).slice(0, 6);
  const hdrs = keys.map((k) => `<th>${esc(k)}</th>`).join("");
  const body = rows.slice(0, 20).map((row) =>
    `<tr>${keys.map((k) => `<td>${esc(String(row[k] ?? ""))}</td>`).join("")}</tr>`
  ).join("");
  return `<div class="table-wrap"><table><thead><tr>${hdrs}</tr></thead><tbody>${body}</tbody></table></div>`;
}

// ---------------------------------------------------------------------------
// Job status UI
// ---------------------------------------------------------------------------

function jobStatusCard(status) {
  if (!status) return `<div class="notice">No active job.</div>`;
  const st = status.state || "unknown";
  const total = Number(status.total || 0);
  const processed = Number(status.processed || 0);
  const pctDone = total > 0 ? Math.round((processed / total) * 100) : 0;
  const stateClass = st === "completed" ? "success" : st === "failed" ? "error" : "running";
  const progressBar = total > 0
    ? `<div class="job-progress-track"><div class="job-progress-fill" style="width:${pctDone}%"></div></div>`
    : "";
  const errSpan = status.errors ? `<span class="error-chip">${status.errors} errors</span>` : "";
  const artifacts = (status.artifact_paths || []).map((p) => {
    const name = p.split("/").pop();
    return `<span class="artifact-chip" title="${esc(p)}">${esc(name)}</span>`;
  }).join("");

  return `<div class="job-status-card ${stateClass}">
    <div class="job-status-header">
      <span class="job-state-badge">${esc(st)}</span>
      <span class="job-kind-badge">${esc(status.kind || "")}</span>
      ${errSpan}
    </div>
    ${progressBar}
    ${total > 0 ? `<div class="job-meta">${processed} / ${total} rows${pctDone > 0 ? ` (${pctDone}%)` : ""}</div>` : ""}
    ${artifacts ? `<div class="job-artifacts">${artifacts}</div>` : ""}
    ${status.error ? `<div class="notice error" style="margin-top:0.5rem">${esc(status.error)}</div>` : ""}
  </div>`;
}

// ---------------------------------------------------------------------------
// Collect polling
// ---------------------------------------------------------------------------

function collectProgressText(status) {
  const done = Number(status?.completed_subreddits || 0);
  const total = Number(status?.total_subreddits || 0);
  const count = total ? `${done}/${total}` : `${done}`;
  const last = status?.last_subreddit ? ` | last: r/${status.last_subreddit}` : "";
  const elapsed = status?.elapsed_seconds ? ` | ${Math.round(status.elapsed_seconds)}s` : "";
  return `${count} subreddits${last}${elapsed}`;
}

function clearCollectPolling() {
  if (state.collectPollTimer) {
    window.clearInterval(state.collectPollTimer);
    state.collectPollTimer = null;
  }
}

function updateCollectUi(status) {
  const running = status?.status === "running";
  const failed = status?.status === "failed";
  const progress = collectProgressText(status);
  const button = $("#collectBtn");
  setBusy(button, running, "Run collector");

  const progressBox = $("#collectProgress");
  if (progressBox) {
    progressBox.hidden = false;
    progressBox.textContent = `${status.status}: ${progress}`;
    progressBox.classList.toggle("is-running", running);
    progressBox.classList.toggle("is-failed", failed);
  }

  const log = $("#collectLog");
  if (log) {
    log.hidden = false;
    log.textContent = status?.log || `${status.status}: ${progress}`;
    log.scrollTop = log.scrollHeight;
  }

  if (running) {
    setNotice(`Collector running: ${progress}`);
  } else if (failed) {
    setNotice("Collector failed. The log has the details.", "error");
  } else if (status?.status === "completed") {
    setNotice(`Collector completed: ${progress}`, "success");
  }
}

// ---------------------------------------------------------------------------
// Main data load
// ---------------------------------------------------------------------------

async function loadRun() {
  state.tag = $("#tagInput").value.trim() || "gm_vehicle_on_demand";
  setNotice("Loading run...");
  try {
    const params = buildFilterParams();
    state.data = await request(apiUrl("/api/run", { tag: state.tag, ...params }));

    const collectStatus = await request(
      apiUrl("/api/collect/status", { tag: state.tag })
    ).catch(() => null);

    if (collectStatus?.status === "running") {
      $("#collectorStatus").textContent = `running ${collectProgressText(collectStatus)}`;
    } else {
      $("#collectorStatus").textContent = state.data.status.legacy_collector_found
        ? "ready"
        : "missing";
    }

    const analyzed = state.data.summary.metrics.analyzed_rows ?? 0;
    const total = state.data.summary.metrics.total_rows ?? 0;
    const filterNote = hasActiveFilters() ? " [filtered]" : "";
    $("#runSubtitle").textContent =
      `${state.data.tag} / ${fmt.format(total)} rows / ${fmt.format(analyzed)} analyzed / ` +
      `${state.data.status.has_classified ? "classified" : "source only"}${filterNote}`;
    $("#railRows").textContent = `${fmt.format(total)} total`;
    $("#railExports").textContent = state.data.status.has_source ? "ready" : "empty";

    updateDownloads();
    render();

    if (collectStatus?.status === "running") {
      updateCollectUi(collectStatus);
      startCollectPolling(collectStatus.job_id);
    } else {
      setNotice("");
    }
  } catch (error) {
    setNotice(error.message, "error");
  }
}

function updateDownloads() {
  const classified = $("#classifiedDownload");
  const report = $("#reportDownload");
  const sourceZip = $("#sourceZipDownload");
  const combined = $("#combinedDownload");
  const saveZip = $("#saveZipBtn");
  const hasSource = Boolean(state.data?.status.has_source);

  if (saveZip) saveZip.disabled = !hasSource;
  if (sourceZip) {
    sourceZip.href = `/api/download/source?tag=${encodeURIComponent(state.tag)}&kind=all`;
    sourceZip.setAttribute("aria-disabled", hasSource ? "false" : "true");
  }
  if (combined) {
    combined.href = `/api/download/source?tag=${encodeURIComponent(state.tag)}&kind=combined`;
    combined.setAttribute("aria-disabled", hasSource ? "false" : "true");
  }
  if (classified) {
    classified.href = `/api/download/classified?tag=${encodeURIComponent(state.tag)}`;
    classified.setAttribute("aria-disabled", state.data?.status.has_classified ? "false" : "true");
  }
  if (report) {
    report.href = `/api/download/report?tag=${encodeURIComponent(state.tag)}`;
    report.setAttribute("aria-disabled", state.data?.status.has_report ? "false" : "true");
  }
}

// ---------------------------------------------------------------------------
// Views
// ---------------------------------------------------------------------------

function setView(view) {
  state.view = view;
  $$(".tab").forEach((tab) => tab.classList.toggle("is-active", tab.dataset.view === view));
  // Load trends data on first visit or tab switch
  if (view === "trends") {
    loadTrends().then(() => render());
    // Also load job status if not already tracking
    if (!state.trendPollTimer) refreshTrendsStatus();
    if (!state.trendBriefingPollTimer) refreshTrendBriefingStatus();
    return;
  }
  render();
}

function emptyState() {
  const template = $("#emptyTemplate").content.cloneNode(true);
  const root = document.createElement("div");
  root.appendChild(template);
  $(".button", root).addEventListener("click", () => setView("collect"));
  return root.innerHTML;
}

function metricGrid(metrics) {
  const cards = [
    ["Analyzed rows", fmt.format(metrics.analyzed_rows), `${fmt.format(metrics.skipped_rows)} skipped`, "volume"],
    ["Complaint rate", pct(metrics.complaint_rate), `${fmt.format(metrics.complaints)} complaint rows`, "alert"],
    ["Negative rate", pct(metrics.negative_rate), "share of analyzed rows", "negative"],
    ["Competitor signal", pct(metrics.competitor_rate), "mentions outside GM", "rival"],
    ["EV topic mix", pct(metrics.ev_rate), "EV-related rows", "ev"],
  ];
  return `<div class="metric-grid">${cards.map(([label, value, note, tone]) =>
    `<article class="metric-card tone-${tone}">
      <span>${label}</span>
      <strong>${value}</strong>
      <small>${note}</small>
    </article>`
  ).join("")}</div>`;
}

// Legacy simple bar chart (still used on dashboard)
function bars(rows, labelKey, valueKey = "count", color = "var(--blue)") {
  if (!rows?.length) return `<div class="notice">No data for this view.</div>`;
  const max = Math.max(...rows.map((row) => Number(row[valueKey] || 0)), 1);
  return `<div class="bar-list">${rows.map((row) => {
    const value = Number(row[valueKey] || 0);
    return `<div class="bar-row">
      <strong>${esc(humanLabel(row[labelKey]))}</strong>
      <div class="bar-track" aria-hidden="true">
        <div class="bar-fill" style="width:${(value / max) * 100}%;background:${color}"></div>
      </div>
      <span class="bar-value">${fmt.format(value)}</span>
    </div>`;
  }).join("")}</div>`;
}

function panel(title, body, note = "", className = "") {
  return `<section class="panel ${esc(className)}">
    <div class="panel-head">
      <h3>${esc(title)}</h3>
      ${note ? `<small>${esc(note)}</small>` : ""}
    </div>
    ${body}
  </section>`;
}

function chartPanel(id, title, note = "") {
  return panel(title, renderChart(id), note, "chart-panel");
}

// Priority scatter (shared between dashboard and explore)
function scatter(rows) {
  if (!rows?.length) return `<div class="notice">No complaint priorities yet.</div>`;
  const width = 720;
  const height = 360;
  const pad = 44;
  const plotted = rows.slice(0, 12);
  const maxX = Math.max(...plotted.map((row) => Number(row.volume || 0)), 1);
  const maxY = Math.max(...plotted.map((row) => Number(row.pct_negative || 0)), 100);
  const points = plotted.map((row) => {
    const x = pad + (Number(row.volume || 0) / maxX) * (width - pad * 2);
    const y = height - pad - (Number(row.pct_negative || 0) / maxY) * (height - pad * 2);
    const radius = Math.max(7, Math.min(24, Number(row.volume || 1) * 4));
    const fill =
      row.priority === "Fix now" ? "#c2413b"
      : row.priority === "Monitor" ? "#d97706"
      : "#1e40af";
    return `<g tabindex="0" aria-label="${esc(row.theme)} ${row.volume} complaints ${row.pct_negative} percent negative">
      <circle cx="${x}" cy="${y}" r="${radius}" fill="${fill}" opacity="0.78"></circle>
      <title>${esc(humanLabel(row.theme))}: ${fmt.format(row.volume || 0)} complaints, ${pct(row.pct_negative)}</title>
    </g>`;
  }).join("");
  const legend = plotted.slice(0, 6).map((row) =>
    `<li><strong>${esc(humanLabel(row.theme))}</strong><span>${fmt.format(row.volume || 0)} / ${pct(row.pct_negative)}</span></li>`
  ).join("");
  return `<div class="priority-map">
    <svg class="chart-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Complaint priority matrix">
      <rect x="${pad}" y="${pad}" width="${width - pad * 2}" height="${height - pad * 2}" rx="10"></rect>
      <line x1="${pad}" y1="${height - pad}" x2="${width - pad}" y2="${height - pad}"></line>
      <line x1="${pad}" y1="${pad}" x2="${pad}" y2="${height - pad}"></line>
      <line class="axis-mid" x1="${pad}" y1="${height / 2}" x2="${width - pad}" y2="${height / 2}"></line>
      <line class="axis-mid" x1="${width / 2}" y1="${pad}" x2="${width / 2}" y2="${height - pad}"></line>
      <text x="${width / 2}" y="${height - 10}" text-anchor="middle">Complaint volume</text>
      <text x="16" y="${height / 2}" transform="rotate(-90 16 ${height / 2})" text-anchor="middle">% negative</text>
      ${points}
    </svg>
    <ol class="priority-legend">${legend}</ol>
  </div>`;
}

function evidenceFeed(rows) {
  if (!rows?.length) return `<div class="notice">No evidence rows for this view.</div>`;
  return `<div class="evidence-feed">${rows.map((row) =>
    `<article class="evidence-item">
      <div class="evidence-meta">
        <span>${esc(row.created_at_norm || "")}</span>
        <span>r/${esc(row.subreddit_norm || "unknown")}</span>
        <span>${esc(row.sentiment || "unlabeled")}</span>
      </div>
      <a href="${esc(row.permalink_norm || "#")}" target="_blank" rel="noreferrer">
        ${esc(row.description || row.title_norm || "No text available")}
      </a>
      <div class="evidence-tags">
        <span>${esc(humanLabel(row.vehicle_mentioned))}</span>
        <span>${esc(humanLabel(row.top_complaint_category))}</span>
      </div>
    </article>`
  ).join("")}</div>`;
}

function evidenceTable(rows) {
  if (!rows?.length) return `<div class="notice">No evidence rows for this view.</div>`;
  const headers = ["date", "subreddit", "vehicle", "sentiment", "theme", "score", "description"];
  return `<div class="table-wrap"><table>
    <thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((row) =>
      `<tr>
        <td>${esc(row.created_at_norm || "")}</td>
        <td>${esc(row.subreddit_norm || "")}</td>
        <td>${esc(row.vehicle_mentioned || "")}</td>
        <td>${esc(row.sentiment || "")}</td>
        <td>${esc(row.top_complaint_category || "")}</td>
        <td>${esc(row.score_norm || "")}</td>
        <td>${row.permalink_norm
          ? `<a href="${esc(row.permalink_norm)}" target="_blank" rel="noreferrer">${esc(row.description || row.title_norm || "")}</a>`
          : esc(row.description || row.title_norm || "")}</td>
      </tr>`
    ).join("")}</tbody>
  </table></div>`;
}

function sourceDownloadsPanel() {
  const files = state.data?.status.source_files || [];
  const hasSource = Boolean(state.data?.status.has_source);
  const fileMap = Object.fromEntries(files.map((file) => [file.kind, file]));
  const items = [
    ["all", "All data ZIP", hasSource, "raw + classified + report"],
    ["combined", "Posts + comments CSV", fileMap.combined?.exists, fileSize(fileMap.combined?.bytes)],
    ["posts", "Posts CSV", fileMap.posts?.exists, fileSize(fileMap.posts?.bytes)],
    ["comments", "Comments CSV", fileMap.comments?.exists, fileSize(fileMap.comments?.bytes)],
  ];
  return `<section class="panel download-panel">
    <div class="panel-head">
      <h2>Exports</h2>
      <small>${hasSource ? `${fmt.format(state.data.summary.metrics.total_rows)} rows loaded` : "no source rows"}</small>
    </div>
    <div class="field-row flush">
      <button id="saveZipPanelBtn" class="button primary" type="button" ${hasSource ? "" : "disabled"}>Save ZIP to Downloads</button>
    </div>
    <div class="download-grid">${items.map(([kind, label, enabled, note]) =>
      `<a class="download-tile" href="/api/download/source?tag=${encodeURIComponent(state.tag)}&kind=${kind}"
          aria-disabled="${enabled ? "false" : "true"}" data-save-kind="${kind}">
        <strong>${esc(label)}</strong>
        <span>${esc(note)}</span>
      </a>`
    ).join("")}</div>
  </section>`;
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------

function dashboard() {
  const data = state.data;
  if (!data?.summary.metrics.total_rows) return emptyState();
  const charts = data.charts;
  const topTheme = topItem(charts.complaints, "theme");
  const topVehicle = topItem(charts.vehicles, "vehicle_mentioned", "comment_count");
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
      ${panel("Sentiment distribution", bars(charts.sentiment, "sentiment", "count", "var(--green)"))}
      ${panel("Signal flags", bars(charts.flags.slice(0, 8), "flag", "count", "var(--blue)"))}
      ${panel("Complaint themes", bars(charts.complaints.slice(0, 8), "theme", "count", "var(--amber)"))}
    </div>
    <div class="panel-grid two">
      ${panel("Priority map", scatter(charts.priority), "volume x negativity", "priority-panel")}
      ${panel("Evidence feed", evidenceFeed(data.evidence.slice(0, 8)), "latest matched rows", "evidence-panel")}
    </div>`;
}

// ---------------------------------------------------------------------------
// Collect view
// ---------------------------------------------------------------------------

function collectView() {
  return `
    <div class="collect-layout">
      <section class="panel collector-panel">
        <div class="panel-head"><h2>Collector</h2><small>append-only</small></div>
        <div class="form-grid">
          <div class="control"><label for="collectSource">Source</label>
            <select id="collectSource">
              <option value="gm">GM vehicle list</option>
              <option value="competitor">Competitor list</option>
              <option value="custom">Custom list</option>
            </select>
          </div>
          <div class="control"><label for="listingLimit">Posts per subreddit</label>
            <input id="listingLimit" type="number" min="1" max="500" value="100">
          </div>
          <div class="control"><label for="commentsLimit">Comments per post</label>
            <input id="commentsLimit" type="number" min="0" max="25" value="5">
          </div>
          <div class="control"><label for="sinceDays">Since days</label>
            <input id="sinceDays" type="number" min="0" max="3650" value="0">
          </div>
          <div class="control wide"><label for="customSubs">Custom subreddits</label>
            <textarea id="customSubs" placeholder="Silverado&#10;Chevy"></textarea>
          </div>
          <div class="control"><label for="dryRun">Dry run</label>
            <select id="dryRun"><option value="false">No</option><option value="true">Yes</option></select>
          </div>
        </div>
        <div class="field-row">
          <button id="collectBtn" class="button primary">Run collector</button>
        </div>
        <div id="collectProgress" class="collector-progress" hidden></div>
        <pre id="collectLog" class="log-box" hidden></pre>
      </section>
      ${sourceDownloadsPanel()}
    </div>
    <section class="panel upload-panel">
      <div class="panel-head"><h2>Upload CSV</h2><small>collector or classified output</small></div>
      <div class="field-row">
        <input id="uploadInput" type="file" accept=".csv">
        <button id="uploadBtn" class="button secondary">Load CSV</button>
      </div>
    </section>`;
}

// ---------------------------------------------------------------------------
// Classify view
// ---------------------------------------------------------------------------

function classifyView() {
  const metrics = state.data?.summary.metrics || {};
  const jobStatus = state.classifyJobStatus;
  const isRunning = jobStatus?.state === "running";

  return `
    <section class="panel classify-panel">
      <div class="panel-head">
        <h2>Quick classify</h2>
        <small>${fmt.format(metrics.total_rows || 0)} source rows</small>
      </div>
      ${metricGrid(metrics)}
      <div class="field-row">
        <label class="control" style="max-width:180px">
          <span>Rows</span>
          <input id="classifyLimit" type="number" min="1" value="50">
        </label>
        <button id="previewClassifyBtn" class="button primary">Preview classify</button>
        <button id="llmClassifyBtn" class="button secondary">LLM classify (inline)</button>
      </div>
    </section>

    <section class="panel spaced">
      <div class="panel-head">
        <h2>Full-run LLM job</h2>
        <small>durable background subprocess</small>
      </div>
      <p class="panel-desc">Classifies all pending rows via the configured LLM. Runs as a background job — safe to close the browser and return later.</p>
      <div class="field-row">
        <label class="control" style="max-width:130px">
          <span>Row limit (0 = all)</span>
          <input id="jobLimitInput" type="number" min="0" value="0">
        </label>
        <button id="startClassifyJobBtn" class="button primary" ${isRunning ? "disabled" : ""}>
          ${isRunning ? "Job running…" : "Start classify job"}
        </button>
        <button id="refreshClassifyJobBtn" class="button secondary">Refresh status</button>
      </div>
      ${jobStatusCard(jobStatus)}
    </section>

    <section class="panel spaced">
      <div class="panel-head"><h2>Evidence sample</h2><small>classification context</small></div>
      ${evidenceFeed((state.data?.evidence || []).slice(0, 10))}
    </section>`;
}

// ---------------------------------------------------------------------------
// Explore view (uses new chart renderers + filters)
// ---------------------------------------------------------------------------

function exploreView() {
  if (!state.data?.summary.metrics.total_rows) return emptyState();

  return `
    ${filterPanel()}
    ${metricGrid(state.data.summary.metrics)}

    <div class="chart-section">
      <div class="panel-grid">
        ${chartPanel("sentiment", "Sentiment distribution")}
        ${chartPanel("severity_summary", "Severity summary")}
        ${chartPanel("complaints", "Top complaint themes")}
      </div>
      <div class="panel-grid two">
        ${panel("Priority map", scatter(state.data.chart_data?.priority || []), "volume × negativity", "priority-panel")}
        ${chartPanel("flags", "Signal flags")}
      </div>
    </div>

    <div class="chart-section">
      <div class="chart-section-head"><h3>Vehicle breakdown</h3></div>
      <div class="panel-grid two">
        ${chartPanel("vehicles", "Vehicles — complaint rate")}
        ${chartPanel("severity_by_model", "Severity by model")}
      </div>
      <div class="panel-grid two">
        ${chartPanel("complaint_by_model", "Complaint mix by model")}
        ${chartPanel("sentiment_by_model", "Sentiment by model")}
      </div>
    </div>

    <div class="chart-section">
      <div class="chart-section-head"><h3>Context &amp; engagement</h3></div>
      <div class="panel-grid">
        ${chartPanel("engagement", "Engagement level")}
        ${chartPanel("comment_type", "Comment type")}
        ${chartPanel("engagement_weighted_themes", "Engagement-weighted themes")}
      </div>
    </div>

    <div class="chart-section">
      <div class="chart-section-head"><h3>EV &amp; competitors</h3></div>
      <div class="panel-grid two">
        ${chartPanel("ev", "EV comparison")}
        ${chartPanel("competitor_breakdown", "Competitor breakdown")}
      </div>
      <div class="panel-grid two">
        ${chartPanel("subreddit", "Subreddit breakdown")}
        ${chartPanel("all_complaint_mentions", "All complaint mentions")}
      </div>
    </div>

    <section class="panel spaced">
      <div class="panel-head"><h2>Evidence</h2><small>matched rows</small></div>
      ${evidenceTable(state.data.evidence)}
    </section>`;
}

// ---------------------------------------------------------------------------
// Briefing + Exports view
// ---------------------------------------------------------------------------

function briefingView() {
  const exportJob = state.exportJobStatus;
  const isExporting = exportJob?.state === "running";
  const tag = state.tag;
  const hasClassified = Boolean(state.data?.status.has_classified);

  return `
    <section class="panel briefing-actions">
      <div class="panel-head"><h2>Narrative briefing</h2><small>markdown export</small></div>
      <div class="field-row">
        <button id="templateBriefBtn" class="button primary">Template briefing</button>
        <button id="llmBriefBtn" class="button secondary">LLM briefing</button>
      </div>
    </section>

    <section class="panel spaced">
      <div class="panel-head">
        <h2>PDF exports</h2>
        <small>charts ZIP or full briefing PDF</small>
      </div>
      <p class="panel-desc">Render all charts as PNGs and package as a ZIP, or combine with the narrative to produce a full briefing PDF. Requires classified data.</p>
      <div class="field-row">
        <button id="chartsZipBtn" class="button secondary" ${isExporting || !hasClassified ? "disabled" : ""}>
          Export charts ZIP
        </button>
        <button id="briefingPdfBtn" class="button secondary" ${isExporting || !hasClassified ? "disabled" : ""}>
          Export briefing PDF
        </button>
        <button id="refreshExportBtn" class="button secondary">Refresh status</button>
      </div>
      ${jobStatusCard(exportJob)}
      <div class="field-row" style="margin-top:0.85rem">
        <a id="downloadChartsLink" class="export-chip"
           href="/api/download/charts?tag=${encodeURIComponent(tag)}"
           aria-disabled="${hasClassified ? "false" : "true"}">Download charts ZIP</a>
        <a id="downloadPdfLink" class="export-chip"
           href="/api/download/briefing-pdf?tag=${encodeURIComponent(tag)}"
           aria-disabled="${hasClassified ? "false" : "true"}">Download briefing PDF</a>
      </div>
      ${!hasClassified ? `<div class="notice" style="margin-top:0.5rem">Classify data first to enable PDF exports.</div>` : ""}
    </section>

    <section class="panel spaced">
      <pre id="briefingText" class="briefing">${esc(state.report || "No briefing generated yet.")}</pre>
    </section>`;
}

// ---------------------------------------------------------------------------
// Trends view (Phase 5)
// ---------------------------------------------------------------------------

function trendConfidenceBadge(level) {
  const colors = { high: "var(--green, #16a34a)", medium: "var(--amber, #d97706)", low: "var(--red, #c2413b)" };
  const color = colors[level] || "var(--muted)";
  return `<span class="confidence-badge" style="background:${color};color:#fff;padding:2px 7px;border-radius:4px;font-size:0.75rem;font-weight:600">${esc(level?.toUpperCase() || "—")}</span>`;
}

function trendDirectionIcon(direction) {
  if (direction === "rising") return "↑";
  if (direction === "falling") return "↓";
  if (direction === "stable") return "→";
  return "—";
}

function renderTrendCluster(cluster) {
  const label = cluster.label || {};
  const sig = cluster.trend_signal || {};
  const vel = sig.velocity || {};
  const zsc = sig.zscore || {};
  const conf = sig.confidence_banner || "";
  const confNote = sig.confidence_note || "";

  const hasSignal = conf && conf !== "";
  const velDir = vel.direction || "";
  const zscDir = zsc.direction || "";

  return `<article class="panel" style="margin-bottom:0.75rem">
    <div class="panel-head" style="display:flex;align-items:center;gap:0.5rem">
      <h4 style="margin:0;flex:1">${esc(label.short_label || `Cluster ${cluster.cluster_id}`)}</h4>
      ${hasSignal ? trendConfidenceBadge(conf) : ""}
      <span style="color:var(--muted);font-size:0.75rem">${esc(label.theme_type || "")}</span>
    </div>
    ${hasSignal && confNote ? `<div class="notice" style="margin:0.5rem 0;font-size:0.85rem">${esc(confNote)}</div>` : ""}
    <div style="display:flex;gap:1.5rem;flex-wrap:wrap;font-size:0.875rem;margin-top:0.25rem">
      ${hasSignal ? `
        <span><strong>Velocity:</strong> ${trendDirectionIcon(velDir)} ${esc(velDir || "—")}
          <small style="color:var(--muted)"> (${vel.recent_count ?? "?"}↑ recent / ${vel.baseline_count ?? "?"}↑ baseline)</small>
        </span>
        <span><strong>Z-score:</strong> ${trendDirectionIcon(zscDir)} ${esc(zscDir || "—")}
          <small style="color:var(--muted)"> (${zsc.n_periods ?? "?"} periods)</small>
        </span>
        <span><strong>Agreement:</strong> ${esc(sig.agreement || "—")}</span>
      ` : `<span style="color:var(--muted)">Run trend analysis to see signals.</span>`}
      <span><strong>Size:</strong> ${fmt.format(cluster.cluster_size)}</span>
      ${cluster.top_vehicles?.length ? `<span><strong>Vehicles:</strong> ${cluster.top_vehicles.slice(0, 3).map(esc).join(", ")}</span>` : ""}
    </div>
    ${label.detailed_label ? `<p style="margin:0.5rem 0 0;font-size:0.85rem;color:var(--muted)">${esc(label.detailed_label)}</p>` : ""}
  </article>`;
}

function trendJobBar(status) {
  if (!status || status.state === "idle") return "";
  const state_label = status.state || "idle";
  const is_running = state_label === "running";
  const color = { completed: "success", failed: "error", interrupted: "error" }[state_label] || "";
  const warning = status.trend_warning ? `<small style="color:var(--amber)"> ⚠ Trend signals: ${esc(status.trend_warning)}</small>` : "";
  return `<div class="notice ${color}" style="margin-bottom:0.75rem">
    Clustering job: <strong>${esc(state_label)}</strong>
    ${is_running ? ` — ${fmt.format(status.processed || 0)} / ${fmt.format(status.total || 0)} clusters` : ""}
    ${warning}
  </div>`;
}

function trendBriefingBar(status) {
  if (!status || status.state === "idle") return "";
  const state_label = status.state || "idle";
  const color = { completed: "success", failed: "error", interrupted: "error" }[state_label] || "";
  const pdfReady = state_label === "completed";
  const tag = state.tag;
  return `<div class="notice ${color}" style="margin-bottom:0.5rem">
    Briefing PDF: <strong>${esc(state_label)}</strong>
    ${pdfReady ? `<a href="/api/download/trend-pdf?tag=${encodeURIComponent(tag)}" style="margin-left:0.75rem" class="export-chip">Download PDF</a>` : ""}
    ${status.error ? ` — ${esc(status.error)}` : ""}
  </div>`;
}

function trendsView() {
  const tag = state.tag;
  const td = state.trendsData;
  const hasClassified = !!(state.data && state.data.summary && state.data.summary.metrics?.analyzed_rows > 0);
  const hasClusters = !!(td && td.ok && td.clusters?.length);
  const trendSummary = td?.trend_summary;
  const hasTimestamps = trendSummary?.has_timestamps;
  const dataSpanDays = trendSummary?.data_span_days || 0;
  const tjs = state.trendJobStatus;
  const bjs = state.trendBriefingJobStatus;

  const jobRunning = tjs?.state === "running";

  return `<div style="padding:1rem">
    ${trendJobBar(tjs)}
    ${trendBriefingBar(bjs)}

    <!-- Data confidence gate -->
    <section class="panel" style="margin-bottom:1rem">
      <div class="panel-head"><h3>⚠ Data Confidence Notice</h3></div>
      <p style="font-size:0.875rem">
        Trend signals require sufficient data across time.
        ${!hasTimestamps && hasClusters
          ? "<strong>No timestamps detected — velocity and z-score signals are unavailable. Cluster labels show thematic content only.</strong>"
          : hasTimestamps
            ? `Dataset spans <strong>${dataSpanDays} days</strong>. Signals marked <em>directional only</em> lack enough data for statistical reliability.`
            : "Run clustering first to see trend signals."
        }
      </p>
      <p style="font-size:0.875rem;color:var(--muted)">
        Trend claims are estimates based on post timestamps and cluster assignment.
        Small clusters (&lt;5 posts) or short spans (&lt;14 days) produce low-confidence signals.
        Always check the confidence badge before acting on a trend.
      </p>
    </section>

    <!-- Clustering controls -->
    <section class="panel" style="margin-bottom:1rem">
      <div class="panel-head"><h3>Clustering & Trend Analysis</h3></div>
      <div style="display:flex;gap:0.5rem;flex-wrap:wrap;align-items:center">
        <label for="nClustersInput">Clusters (k):</label>
        <input id="nClustersInput" type="number" min="2" max="30" value="10" style="width:60px" />
        <button id="startTrendsBtn" class="button primary" ${!hasClassified || jobRunning ? "disabled" : ""}>
          ${hasClusters ? "Re-run clustering" : "Run clustering"}
        </button>
        <button id="refreshTrendsBtn" class="button">Refresh status</button>
        ${hasClusters ? `<button id="trendBriefingBtn" class="button" ${bjs?.state === "running" ? "disabled" : ""}>Generate briefing PDF</button>` : ""}
        ${bjs?.state === "running" ? `<button id="refreshTrendBriefingBtn" class="button">Refresh briefing</button>` : ""}
      </div>
      ${!hasClassified ? `<div class="notice" style="margin-top:0.5rem">Classify data first before running trend analysis.</div>` : ""}
    </section>

    <!-- Results -->
    ${hasClusters ? `
      <section>
        <div style="display:flex;align-items:baseline;gap:0.75rem;margin-bottom:0.5rem">
          <h3 style="margin:0">Cluster Trends</h3>
          <small style="color:var(--muted)">${td.clusters.length} clusters · ${esc(td.metadata?.model || "")}</small>
        </div>
        ${hasTimestamps
          ? `<div class="notice" style="margin-bottom:0.75rem;font-size:0.85rem">
              Clusters sorted by rising activity first. ${dataSpanDays >= 14 ? "" : "Short data span — treat directional signals as exploratory."}
            </div>`
          : `<div class="notice" style="margin-bottom:0.75rem;font-size:0.85rem">No timestamps in dataset — trend directions are unavailable.</div>`
        }
        ${[...td.clusters]
          .sort((a, b) => {
            const dirOrder = { rising: 0, falling: 1, stable: 2, insufficient_data: 3 };
            const confOrder = { high: 0, medium: 1, low: 2 };
            const aDir = a.trend_signal?.velocity?.direction || "insufficient_data";
            const bDir = b.trend_signal?.velocity?.direction || "insufficient_data";
            const aC = a.trend_signal?.confidence_banner || "low";
            const bC = b.trend_signal?.confidence_banner || "low";
            return (dirOrder[aDir] ?? 3) - (dirOrder[bDir] ?? 3)
              || (confOrder[aC] ?? 2) - (confOrder[bC] ?? 2);
          })
          .map(renderTrendCluster).join("")}
      </section>
    ` : !tjs || tjs.state === "idle"
      ? `<div class="notice">No clustering results yet. Click <em>Run clustering</em> to start.</div>`
      : ""
    }
  </div>`;
}

async function loadTrends() {
  try {
    const td = await request(apiUrl("/api/trends", { tag: state.tag }));
    state.trendsData = td;
  } catch {
    // Non-fatal — trends section shows "no data" state
    state.trendsData = null;
  }
}

// ---------------------------------------------------------------------------
// Render dispatch
// ---------------------------------------------------------------------------

function render() {
  const root = $("#viewRoot");
  const views = {
    dashboard,
    collect: collectView,
    classify: classifyView,
    explore: exploreView,
    briefing: briefingView,
    trends: trendsView,
    qa: qaView,
  };
  root.innerHTML = (views[state.view] || (() => ""))();
  bindViewEvents();
}

// ---------------------------------------------------------------------------
// Event bindings
// ---------------------------------------------------------------------------

function handleSaveKindClick(event) {
  const target = event.currentTarget;
  event.preventDefault();
  if (target.getAttribute("aria-disabled") === "true" || target.disabled) return;
  saveExport(target.dataset.saveKind || "all");
}

function bindViewEvents() {
  $$("[data-jump]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.jump)));
  $$("[data-save-export]").forEach((button) => button.addEventListener("click", () => saveExport("all")));
  $$("#viewRoot [data-save-kind]").forEach((target) => target.addEventListener("click", handleSaveKindClick));

  // Collect
  $("#collectBtn")?.addEventListener("click", collect);
  $("#saveZipPanelBtn")?.addEventListener("click", () => saveExport("all"));
  $("#uploadBtn")?.addEventListener("click", upload);

  // Classify
  $("#previewClassifyBtn")?.addEventListener("click", previewClassify);
  $("#llmClassifyBtn")?.addEventListener("click", llmClassify);
  $("#startClassifyJobBtn")?.addEventListener("click", startClassifyJob);
  $("#refreshClassifyJobBtn")?.addEventListener("click", refreshClassifyJobStatus);

  // Briefing / exports
  $("#templateBriefBtn")?.addEventListener("click", () => briefing(false));
  $("#llmBriefBtn")?.addEventListener("click", () => briefing(true));
  $("#chartsZipBtn")?.addEventListener("click", () => startExportJob("charts"));
  $("#briefingPdfBtn")?.addEventListener("click", () => startExportJob("briefing"));
  $("#refreshExportBtn")?.addEventListener("click", refreshExportJobStatus);

  // Trends (Phase 5)
  $("#startTrendsBtn")?.addEventListener("click", startTrendsJob);
  $("#refreshTrendsBtn")?.addEventListener("click", refreshTrendsStatus);
  $("#trendBriefingBtn")?.addEventListener("click", startTrendBriefingJob);
  $("#refreshTrendBriefingBtn")?.addEventListener("click", refreshTrendBriefingStatus);

  // Q&A (Phase 6)
  $("#qaBuildIndexBtn")?.addEventListener("click", startQaBuildIndex);
  $("#qaRefreshBtn")?.addEventListener("click", refreshQaStatus);
  $("#qaSubmitBtn")?.addEventListener("click", submitQaQuestion);
  $("#qaSearchBtn")?.addEventListener("click", submitQaSearch);

  // Filters (explore view)
  bindFilterEvents();
}

// ---------------------------------------------------------------------------
// Collect polling
// ---------------------------------------------------------------------------

function startCollectPolling(jobId = "") {
  clearCollectPolling();
  state.collectPollTimer = window.setInterval(() => pollCollectStatus(jobId), 1500);
}

async function pollCollectStatus(jobId = "") {
  try {
    const status = await request(apiUrl("/api/collect/status", { tag: state.tag, job_id: jobId }));
    updateCollectUi(status);
    if (status.status !== "running") {
      clearCollectPolling();
      setBusy($("#collectBtn"), false, "Run collector");
      await loadRun();
      updateCollectUi(status);
    }
  } catch (error) {
    clearCollectPolling();
    setBusy($("#collectBtn"), false, "Run collector");
    setNotice(error.message, "error");
  }
}

// ---------------------------------------------------------------------------
// Classify job polling
// ---------------------------------------------------------------------------

function clearClassifyPolling() {
  if (state.classifyPollTimer) {
    window.clearInterval(state.classifyPollTimer);
    state.classifyPollTimer = null;
  }
}

function startClassifyPolling(jobId = "") {
  clearClassifyPolling();
  state.classifyPollTimer = window.setInterval(() => pollClassifyJobStatus(jobId), 2000);
}

async function pollClassifyJobStatus(jobId = "") {
  try {
    const params = jobId ? { tag: state.tag, job_id: jobId } : { tag: state.tag };
    const status = await request(apiUrl("/api/classify/status", params));
    state.classifyJobStatus = status;
    if (state.view === "classify") render();
    const done = ["completed", "failed", "interrupted"].includes(status.state);
    if (done) {
      clearClassifyPolling();
      if (status.state === "completed") {
        setNotice("Classify job completed.", "success");
        await loadRun();
      } else {
        setNotice(`Classify job ${status.state}.`, "error");
      }
    }
  } catch {
    clearClassifyPolling();
  }
}

async function startClassifyJob() {
  const btn = $("#startClassifyJobBtn");
  setBusy(btn, true, "Start classify job");
  try {
    const limit = Number($("#jobLimitInput")?.value || 0);
    const status = await request("/api/classify/job", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tag: state.tag,
        provider: $("#providerSelect").value,
        model: $("#modelInput").value,
        api_key: $("#apiKeyInput").value,
        limit,
      }),
    });
    state.classifyJobStatus = status;
    render();
    if (status.state === "running") {
      setNotice("Classify job started.");
      startClassifyPolling(status.job_id);
    }
  } catch (error) {
    setNotice(error.message, "error");
    setBusy(btn, false, "Start classify job");
  }
}

async function refreshClassifyJobStatus() {
  try {
    const status = await request(apiUrl("/api/classify/status", { tag: state.tag }));
    state.classifyJobStatus = status;
    render();
    if (status.state === "running") startClassifyPolling(status.job_id);
  } catch (error) {
    setNotice(error.message, "error");
  }
}

// ---------------------------------------------------------------------------
// Export job polling
// ---------------------------------------------------------------------------

function clearExportPolling() {
  if (state.exportPollTimer) {
    window.clearInterval(state.exportPollTimer);
    state.exportPollTimer = null;
  }
}

function startExportPolling(jobId = "") {
  clearExportPolling();
  state.exportPollTimer = window.setInterval(() => pollExportJobStatus(jobId), 2000);
}

async function pollExportJobStatus(jobId = "") {
  try {
    const params = jobId ? { tag: state.tag, job_id: jobId } : { tag: state.tag };
    const status = await request(apiUrl("/api/export/status", params));
    state.exportJobStatus = status;
    if (state.view === "briefing") render();
    const done = ["completed", "failed", "interrupted"].includes(status.state);
    if (done) {
      clearExportPolling();
      if (status.state === "completed") {
        setNotice("Export completed. Download links are now active.", "success");
      } else {
        setNotice(`Export ${status.state}.`, "error");
      }
    }
  } catch {
    clearExportPolling();
  }
}

async function startExportJob(kind) {
  const btnId = kind === "charts" ? "#chartsZipBtn" : "#briefingPdfBtn";
  const btn = $(btnId);
  setBusy(btn, true, kind === "charts" ? "Export charts ZIP" : "Export briefing PDF");
  try {
    const status = await request("/api/export/pdf-job", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tag: state.tag, kind }),
    });
    state.exportJobStatus = status;
    render();
    if (status.state === "running") {
      setNotice(`${kind === "charts" ? "Charts" : "Briefing"} export job started.`);
      startExportPolling(status.job_id);
    }
  } catch (error) {
    setNotice(error.message, "error");
    setBusy(btn, false, kind === "charts" ? "Export charts ZIP" : "Export briefing PDF");
  }
}

async function refreshExportJobStatus() {
  try {
    const status = await request(apiUrl("/api/export/status", { tag: state.tag }));
    state.exportJobStatus = status;
    render();
    if (status.state === "running") startExportPolling(status.job_id);
  } catch (error) {
    setNotice(error.message, "error");
  }
}

// ---------------------------------------------------------------------------
// Data actions
// ---------------------------------------------------------------------------

async function collect() {
  const button = $("#collectBtn");
  clearCollectPolling();
  setBusy(button, true, "Run collector");
  setNotice("Collector starting...");
  const body = {
    tag: state.tag,
    source: $("#collectSource").value,
    subreddits: $("#customSubs").value,
    listing_limit: Number($("#listingLimit").value || 100),
    comments_limit: Number($("#commentsLimit").value || 5),
    since_days: Number($("#sinceDays").value || 0),
    dry_run: $("#dryRun").value === "true",
  };
  try {
    const result = await request("/api/collect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    updateCollectUi(result);
    if (result.status === "running") {
      startCollectPolling(result.job_id);
    } else {
      setBusy(button, false, "Run collector");
      await loadRun();
      updateCollectUi(result);
    }
  } catch (error) {
    setNotice(error.message, "error");
    setBusy(button, false, "Run collector");
  }
}

async function saveExport(kind = "all") {
  const labels = {
    all: "ZIP",
    combined: "posts + comments CSV",
    posts: "posts CSV",
    comments: "comments CSV",
    classified: "classified CSV",
    report: "briefing",
  };
  const buttons = kind === "all"
    ? [$("#saveZipBtn"), $("#saveZipPanelBtn")].filter(Boolean)
    : [];
  buttons.forEach((button) => setBusy(button, true, "Save ZIP"));
  setNotice(`Saving ${labels[kind] || "export"}...`);
  try {
    const result = await request(apiUrl("/api/export/save", { tag: state.tag, kind }), {
      method: "POST",
    });
    setNotice(`Saved ${result.filename} to Downloads.`, "success");
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    buttons.forEach((button) =>
      setBusy(button, false, button.id === "saveZipBtn" ? "Save ZIP" : "Save ZIP to Downloads")
    );
  }
}

// ---------------------------------------------------------------------------
// Trend job polling (Phase 5)
// ---------------------------------------------------------------------------

function clearTrendPolling() {
  if (state.trendPollTimer) {
    window.clearInterval(state.trendPollTimer);
    state.trendPollTimer = null;
  }
}

function startTrendPolling(jobId = "") {
  clearTrendPolling();
  state.trendPollTimer = window.setInterval(() => pollTrendStatus(jobId), 2000);
}

async function pollTrendStatus(jobId = "") {
  try {
    const params = jobId ? { tag: state.tag, job_id: jobId } : { tag: state.tag };
    const status = await request(apiUrl("/api/trends/status", params));
    state.trendJobStatus = status;
    if (state.view === "trends") render();
    const done = ["completed", "failed", "interrupted"].includes(status.state);
    if (done) {
      clearTrendPolling();
      if (status.state === "completed") {
        await loadTrends();
        setNotice("Clustering completed. Trend signals ready.", "success");
      } else {
        setNotice(`Clustering ${status.state}.`, "error");
      }
      if (state.view === "trends") render();
    }
  } catch {
    clearTrendPolling();
  }
}

async function startTrendsJob() {
  const btn = $("#startTrendsBtn");
  setBusy(btn, true, hasClustersNow() ? "Re-run clustering" : "Run clustering");
  try {
    const status = await request("/api/trends/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tag: state.tag,
        provider: $("#providerSelect").value,
        model: $("#modelInput").value,
        api_key: $("#apiKeyInput").value,
        n_clusters: Number($("#nClustersInput")?.value || 10),
      }),
    });
    state.trendJobStatus = status;
    if (state.view === "trends") render();
    if (status.state === "running") {
      setNotice("Clustering job started.");
      startTrendPolling(status.job_id);
    }
  } catch (error) {
    setNotice(error.message, "error");
    setBusy(btn, false, "Run clustering");
  }
}

async function refreshTrendsStatus() {
  try {
    const status = await request(apiUrl("/api/trends/status", { tag: state.tag }));
    state.trendJobStatus = status;
    if (status.state === "completed") await loadTrends();
    if (state.view === "trends") render();
    if (status.state === "running") startTrendPolling(status.job_id);
  } catch (error) {
    setNotice(error.message, "error");
  }
}

function hasClustersNow() {
  return !!(state.trendsData?.ok && state.trendsData?.clusters?.length);
}

// Trend briefing polling

function clearTrendBriefingPolling() {
  if (state.trendBriefingPollTimer) {
    window.clearInterval(state.trendBriefingPollTimer);
    state.trendBriefingPollTimer = null;
  }
}

function startTrendBriefingPolling(jobId = "") {
  clearTrendBriefingPolling();
  state.trendBriefingPollTimer = window.setInterval(() => pollTrendBriefingStatus(jobId), 2000);
}

async function pollTrendBriefingStatus(jobId = "") {
  try {
    const params = jobId ? { tag: state.tag, job_id: jobId } : { tag: state.tag };
    const status = await request(apiUrl("/api/trends/briefing/status", params));
    state.trendBriefingJobStatus = status;
    if (state.view === "trends") render();
    const done = ["completed", "failed", "interrupted"].includes(status.state);
    if (done) {
      clearTrendBriefingPolling();
      if (status.state === "completed") {
        setNotice("Trend briefing PDF ready for download.", "success");
      } else {
        setNotice(`Trend briefing ${status.state}.`, "error");
      }
    }
  } catch {
    clearTrendBriefingPolling();
  }
}

async function startTrendBriefingJob() {
  const btn = $("#trendBriefingBtn");
  setBusy(btn, true, "Generate briefing PDF");
  try {
    const status = await request("/api/trends/briefing", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tag: state.tag,
        provider: $("#providerSelect").value,
        model: $("#modelInput").value,
        api_key: $("#apiKeyInput").value,
      }),
    });
    state.trendBriefingJobStatus = status;
    if (state.view === "trends") render();
    if (status.state === "running") {
      setNotice("Trend briefing job started.");
      startTrendBriefingPolling(status.job_id);
    }
  } catch (error) {
    setNotice(error.message, "error");
    setBusy(btn, false, "Generate briefing PDF");
  }
}

async function refreshTrendBriefingStatus() {
  try {
    const status = await request(apiUrl("/api/trends/briefing/status", { tag: state.tag }));
    state.trendBriefingJobStatus = status;
    if (state.view === "trends") render();
    if (status.state === "running") startTrendBriefingPolling(status.job_id);
  } catch (error) {
    setNotice(error.message, "error");
  }
}

async function upload() {
  const input = $("#uploadInput");
  const button = $("#uploadBtn");
  if (!input.files?.length) {
    setNotice("Choose a CSV first.", "error");
    return;
  }
  setBusy(button, true, "Load CSV");
  const form = new FormData();
  form.append("file", input.files[0]);
  try {
    const result = await request(apiUrl("/api/upload", { tag: state.tag }), {
      method: "POST",
      body: form,
    });
    setNotice(`Loaded ${fmt.format(result.rows)} rows as ${result.kind}.`, "success");
    await loadRun();
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(button, false, "Load CSV");
  }
}

async function previewClassify() {
  const button = $("#previewClassifyBtn");
  setBusy(button, true, "Preview classify");
  try {
    const result = await request("/api/classify/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tag: state.tag, limit: Number($("#classifyLimit").value || 50) }),
    });
    setNotice(`Saved preview labels for ${fmt.format(result.rows)} rows.`, "success");
    await loadRun();
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(button, false, "Preview classify");
  }
}

async function llmClassify() {
  const button = $("#llmClassifyBtn");
  setBusy(button, true, "LLM classify");
  try {
    const result = await request("/api/classify/llm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tag: state.tag,
        provider: $("#providerSelect").value,
        model: $("#modelInput").value,
        api_key: $("#apiKeyInput").value,
        limit: Number($("#classifyLimit").value || 25),
      }),
    });
    setNotice(`Saved ${fmt.format(result.rows)} LLM labels.`, "success");
    await loadRun();
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(button, false, "LLM classify");
  }
}

async function briefing(useLlm) {
  const button = useLlm ? $("#llmBriefBtn") : $("#templateBriefBtn");
  setBusy(button, true, useLlm ? "LLM briefing" : "Template briefing");
  try {
    const result = await request("/api/briefing", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tag: state.tag,
        provider: $("#providerSelect").value,
        model: $("#modelInput").value,
        api_key: $("#apiKeyInput").value,
        use_llm: useLlm,
      }),
    });
    state.report = result.report;
    setNotice("Briefing saved.", "success");
    await loadRun();
    setView("briefing");
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(button, false, useLlm ? "LLM briefing" : "Template briefing");
  }
}

// ---------------------------------------------------------------------------
// Q&A / RAG (Phase 6)
// ---------------------------------------------------------------------------

function qaJobBar(status) {
  if (!status || status.state === "idle") return "";
  const stateLabel = { running: "Building index…", completed: "Index ready", failed: "Build failed", interrupted: "Build interrupted" }[status.state] || status.state;
  const color = status.state === "completed" ? "success" : status.state === "running" ? "" : "error";
  const pct = status.total > 0 ? Math.round((status.processed / status.total) * 100) : 0;
  return `<div class="notice ${color}" style="margin-bottom:0.5rem">
    <strong>Index build:</strong> ${stateLabel}
    ${status.state === "running" && status.total > 0
      ? ` — ${status.processed}/${status.total} docs (${pct}%)`
      : ""}
    ${status.error ? ` — ${esc(status.error)}` : ""}
  </div>`;
}

function qaEvidenceCard(hit, index) {
  const vehicle = hit.vehicle || "unknown";
  const sentiment = hit.sentiment || "";
  const category = (hit.category || "").replaceAll("_", " ");
  const subreddit = hit.subreddit ? `r/${esc(hit.subreddit)}` : "";
  const permalink = hit.permalink || "";
  const score = (hit.retrieval_score || 0).toFixed(3);
  const body = (hit.body || "").slice(0, 400);
  return `<article class="evidence-item" style="margin-bottom:1rem">
    <div class="evidence-meta">
      <strong>[${index}]</strong>
      <span>${esc(vehicle)}</span>
      ${sentiment ? `<span class="tag">${esc(sentiment)}</span>` : ""}
      ${category ? `<span class="tag">${esc(category)}</span>` : ""}
      ${subreddit ? `<span>${esc(subreddit)}</span>` : ""}
      <span style="color:var(--muted);font-size:0.8rem">sim ${score}</span>
    </div>
    <p style="margin:0.25rem 0 0;font-size:0.875rem">${esc(body)}</p>
    ${permalink ? `<a href="https://reddit.com${esc(permalink)}" target="_blank" rel="noopener" style="font-size:0.8rem">View on Reddit</a>` : ""}
  </article>`;
}

function qaView() {
  const tag = state.tag;
  const qjs = state.qaJobStatus;
  const hits = state.qaHits;
  const answer = state.qaAnswer;
  const hasClassified = !!(state.data && state.data.summary && state.data.summary.metrics?.analyzed_rows > 0);
  const indexReady = qjs?.state === "completed";
  const jobRunning = qjs?.state === "running";

  return `<div style="padding:1rem">
    ${qaJobBar(qjs)}

    <!-- Index controls -->
    <section class="panel" style="margin-bottom:1rem">
      <div class="panel-head"><h3>Q&amp;A Index</h3></div>
      <p style="font-size:0.875rem;margin:0 0 0.5rem">
        Build a FAISS vector index from classified comments so you can ask natural-language questions
        and get evidence-backed answers. Classification must run first.
      </p>
      <div style="display:flex;gap:0.5rem;flex-wrap:wrap;align-items:center">
        <button id="qaBuildIndexBtn" class="button primary" ${!hasClassified || jobRunning ? "disabled" : ""}>
          ${indexReady ? "Rebuild index" : "Build index"}
        </button>
        <button id="qaRefreshBtn" class="button">Refresh status</button>
      </div>
      ${!hasClassified
        ? `<div class="notice" style="margin-top:0.5rem">Classify data first before building the Q&amp;A index.</div>`
        : !indexReady && !jobRunning
          ? `<div class="notice" style="margin-top:0.5rem">No index yet. Click <em>Build index</em> to start.</div>`
          : ""
      }
      ${indexReady && qjs.doc_count != null
        ? `<div class="notice success" style="margin-top:0.5rem">${fmt.format(qjs.doc_count)} docs indexed.</div>`
        : ""
      }
    </section>

    <!-- Question box -->
    ${indexReady ? `
    <section class="panel" style="margin-bottom:1rem">
      <div class="panel-head"><h3>Ask a Question</h3></div>
      <div style="display:flex;gap:0.5rem;flex-wrap:wrap;margin-bottom:0.5rem">
        <input id="qaQuestionInput" type="text" placeholder="e.g. What are the most common Silverado transmission complaints?"
          style="flex:1;min-width:200px" />
        <button id="qaSubmitBtn" class="button primary">Ask (with answer)</button>
        <button id="qaSearchBtn" class="button">Search only</button>
      </div>
      <p style="font-size:0.8rem;color:var(--muted);margin:0">
        <em>Ask</em> retrieves evidence and generates an LLM answer. <em>Search only</em> returns evidence cards without an LLM call.
      </p>
    </section>
    ` : ""}

    <!-- Answer -->
    ${answer ? `
    <section class="panel" style="margin-bottom:1rem">
      <div class="panel-head"><h3>Answer</h3></div>
      <p style="font-size:0.9rem;white-space:pre-wrap">${esc(answer)}</p>
    </section>
    ` : ""}

    <!-- Evidence -->
    ${hits && hits.length > 0 ? `
    <section>
      <div style="display:flex;align-items:baseline;gap:0.5rem;margin-bottom:0.5rem">
        <h3 style="margin:0">Evidence</h3>
        <small style="color:var(--muted)">${hits.length} result${hits.length === 1 ? "" : "s"} · sorted by similarity</small>
      </div>
      <div class="evidence-feed">
        ${hits.map((h, i) => qaEvidenceCard(h, i + 1)).join("")}
      </div>
    </section>
    ` : hits && hits.length === 0 ? `<div class="notice">No relevant evidence found for that query.</div>` : ""}
  </div>`;
}

// Q&A polling

function clearQaPolling() {
  if (state.qaPollTimer) {
    window.clearInterval(state.qaPollTimer);
    state.qaPollTimer = null;
  }
}

function startQaPolling(jobId = "") {
  clearQaPolling();
  state.qaPollTimer = window.setInterval(() => pollQaStatus(jobId), 2000);
}

async function pollQaStatus(jobId = "") {
  try {
    const params = jobId ? { tag: state.tag, job_id: jobId } : { tag: state.tag };
    const status = await request(apiUrl("/api/qa/status", params));
    state.qaJobStatus = status;
    if (state.view === "qa") render();
    const done = ["completed", "failed", "interrupted"].includes(status.state);
    if (done) {
      clearQaPolling();
      if (status.state === "completed") {
        setNotice("Q&A index built. You can now ask questions.", "success");
      } else {
        setNotice(`Index build ${status.state}.`, "error");
      }
    }
  } catch {
    clearQaPolling();
  }
}

async function refreshQaStatus() {
  try {
    const status = await request(apiUrl("/api/qa/status", { tag: state.tag }));
    state.qaJobStatus = status;
    if (state.view === "qa") render();
    if (status.state === "running") startQaPolling(status.job_id);
  } catch (error) {
    setNotice(error.message, "error");
  }
}

// Q&A actions

async function startQaBuildIndex() {
  const btn = $("#qaBuildIndexBtn");
  setBusy(btn, true, "Build index");
  try {
    const status = await request("/api/qa/build-index", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tag: state.tag,
        provider: $("#providerSelect").value,
        model: $("#modelInput").value,
        api_key: $("#apiKeyInput").value,
      }),
    });
    state.qaJobStatus = status;
    state.qaHits = null;
    state.qaAnswer = null;
    if (state.view === "qa") render();
    if (status.state === "running") {
      setNotice("Index build started.");
      startQaPolling(status.job_id);
    }
  } catch (error) {
    setNotice(error.message, "error");
    setBusy(btn, false, "Build index");
  }
}

async function submitQaQuestion() {
  const question = ($("#qaQuestionInput")?.value || "").trim();
  if (!question) { setNotice("Enter a question first.", "error"); return; }
  const btn = $("#qaSubmitBtn");
  setBusy(btn, true, "Ask (with answer)");
  state.qaHits = null;
  state.qaAnswer = null;
  try {
    const result = await request("/api/qa/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tag: state.tag,
        question,
        provider: $("#providerSelect").value,
        model: $("#modelInput").value,
        api_key: $("#apiKeyInput").value,
        k: 8,
      }),
    });
    state.qaHits = result.hits || [];
    state.qaAnswer = result.answer || "";
    if (state.view === "qa") render();
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(btn, false, "Ask (with answer)");
  }
}

async function submitQaSearch() {
  const question = ($("#qaQuestionInput")?.value || "").trim();
  if (!question) { setNotice("Enter a query first.", "error"); return; }
  const btn = $("#qaSearchBtn");
  setBusy(btn, true, "Search only");
  state.qaHits = null;
  state.qaAnswer = null;
  try {
    const result = await request("/api/qa/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tag: state.tag,
        query: question,
        provider: $("#providerSelect").value,
        model: $("#modelInput").value,
        api_key: $("#apiKeyInput").value,
        k: 8,
      }),
    });
    state.qaHits = result.hits || [];
    state.qaAnswer = null;
    if (state.view === "qa") render();
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(btn, false, "Search only");
  }
}

// ---------------------------------------------------------------------------
// Global bindings + init
// ---------------------------------------------------------------------------

function bindGlobalEvents() {
  $("#refreshBtn").addEventListener("click", loadRun);
  $$(".top-actions [data-save-kind]").forEach((target) =>
    target.addEventListener("click", handleSaveKindClick)
  );
  $("#tagInput").addEventListener("change", loadRun);
  $("#providerSelect").addEventListener("change", () => {
    $("#modelInput").value =
      $("#providerSelect").value === "openai" ? "gpt-4o-mini" : "gpt-oss-120b";
  });
  $$(".tab").forEach((tab) => tab.addEventListener("click", () => setView(tab.dataset.view)));
}

bindGlobalEvents();
loadRun();
