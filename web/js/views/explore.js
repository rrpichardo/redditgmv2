// explore.js — filters, evidence renderers, and the chart-heavy Explore view.
import { state, $, $$, esc, fmt, humanLabel, debounce } from "../state.js";
import { chartPanel, metricGrid, emptyState } from "../components.js";
import { loadRun } from "../app.js";

// ---------------------------------------------------------------------------
// Filters
// ---------------------------------------------------------------------------

// Translate state.filters into the query-param shape /api/run expects.
export function buildFilterParams() {
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

// True when any filter dimension is set.
export function hasActiveFilters() {
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

// Render the multi-select filter panel from available filter options.
export function filterPanel() {
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

// Wire change/input listeners on the filter controls.
export function bindFilterEvents() {
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

// Read the current filter controls into state and reload the run.
export function applyFilters() {
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

// Reset filters and reload the run.
export function clearFilters() {
  state.filters = {};
  loadRun();
}

// ---------------------------------------------------------------------------
// Evidence renderers
// ---------------------------------------------------------------------------

// Card-style evidence feed (dashboard + classify views).
export function evidenceFeed(rows) {
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

// Tabular evidence (Explore view).
export function evidenceTable(rows) {
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

// ---------------------------------------------------------------------------
// Explore view
// ---------------------------------------------------------------------------

export function exploreView() {
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
        ${chartPanel("priority", "Priority map", "volume × negativity")}
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
