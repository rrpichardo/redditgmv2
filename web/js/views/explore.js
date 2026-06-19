// explore.js — filters, evidence renderers, and the chart-heavy Explore view.
// Also exports explorerView() which stitches explore + Q&A into the combined
// Data Explorer tab, and bindExplorerEvents() which covers both filter and Q&A bindings.
import { state, $, $$, esc, fmt, humanLabel, debounce } from "../state.js";
import { chartPanel, metricGrid, emptyState } from "../components.js";
import { loadRun, loadDetailCharts } from "../app.js";
import { render } from "../nav.js";
import { qaView, startQaBuildIndex, refreshQaStatus, submitQaQuestion, submitQaSearch } from "./qa.js";

const EVIDENCE_PAGE_SIZE = 25;

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
    date_start: f.date_start || undefined,
    date_end: f.date_end || undefined,
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
    (f.min_score ? 1 : 0) +
    (f.date_start ? 1 : 0) +
    (f.date_end ? 1 : 0)
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

  // Date range inputs — shown only when the backend reports min/max dates.
  const minDate = opts.dateStart?.[0] || "";
  const maxDate = opts.dateEnd?.[0] || "";
  const dateRange = minDate || maxDate ? `
    <div class="filter-control">
      <label class="filter-label">Date from</label>
      <input id="filter-date-start" type="date" class="filter-input"
        value="${esc(f.date_start || "")}"
        min="${esc(minDate)}" max="${esc(maxDate)}">
    </div>
    <div class="filter-control">
      <label class="filter-label">Date to</label>
      <input id="filter-date-end" type="date" class="filter-input"
        value="${esc(f.date_end || "")}"
        min="${esc(minDate)}" max="${esc(maxDate)}">
    </div>` : "";

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
      ${dateRange}
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
  $("#filter-date-start")?.addEventListener("change", applyFilters);
  $("#filter-date-end")?.addEventListener("change", applyFilters);
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
  const dateStartEl = $("#filter-date-start");
  if (dateStartEl?.value) f.date_start = dateStartEl.value;
  const dateEndEl = $("#filter-date-end");
  if (dateEndEl?.value) f.date_end = dateEndEl.value;
  state.filters = f;
  state.evidencePage = 0;  // reset to first page on any filter change
  loadRun();
}

// Reset filters and reload the run.
export function clearFilters() {
  state.filters = {};
  state.evidencePage = 0;
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

// Single evidence card for the Explorer — shows title + expandable body text.
function evidenceCard(row) {
  const title = row.title_norm || "";
  const body = row.target_text || "";
  const desc = row.description || "";
  const date = row.created_at_norm || "";
  const subreddit = row.subreddit_norm || "";
  const vehicle = row.vehicle_mentioned || "";
  const sentiment = row.sentiment || "";
  const theme = humanLabel(row.top_complaint_category || "");
  const score = row.score_norm != null ? Number(row.score_norm).toFixed(2) : "";
  const permalink = row.permalink_norm
    ? `https://reddit.com${row.permalink_norm}`
    : "";

  // Prefer raw body text; fall back to the AI-generated description.
  const content = body || desc;
  const snippet = content.slice(0, 120);
  const hasMore = content.length > 120;

  return `<article class="evidence-item">
    <div class="evidence-meta">
      ${date ? `<span>${esc(date)}</span>` : ""}
      ${subreddit ? `<span>r/${esc(subreddit)}</span>` : ""}
      ${vehicle ? `<span>${esc(vehicle)}</span>` : ""}
      ${sentiment ? `<span class="tag">${esc(sentiment)}</span>` : ""}
      ${theme ? `<span class="tag">${esc(theme)}</span>` : ""}
      ${score ? `<span style="color:var(--muted);font-size:0.8rem">score ${score}</span>` : ""}
    </div>
    ${title
      ? (permalink
        ? `<a href="${esc(permalink)}" target="_blank" rel="noreferrer" class="evidence-title">${esc(title)}</a>`
        : `<strong class="evidence-title">${esc(title)}</strong>`)
      : ""}
    ${content ? `
    <details class="evidence-body">
      <summary>${esc(snippet)}${hasMore ? "…" : ""}</summary>
      ${hasMore ? `<p class="evidence-body-full">${esc(content)}</p>` : ""}
    </details>` : ""}
  </article>`;
}

// Paginated evidence card list for the Explorer view, sorted by relevance (score_norm).
// Pagination state lives in state.evidencePage; prev/next buttons are bound in bindExplorerEvents().
export function evidenceTable(rows) {
  if (!rows?.length) return `<div class="notice">No evidence rows for this view.</div>`;

  const total = rows.length;
  const totalPages = Math.max(1, Math.ceil(total / EVIDENCE_PAGE_SIZE));
  const page = Math.min(Math.max(0, state.evidencePage || 0), totalPages - 1);
  const slice = rows.slice(page * EVIDENCE_PAGE_SIZE, (page + 1) * EVIDENCE_PAGE_SIZE);

  const pager = `<div class="evidence-pager">
    <button id="evidencePrevBtn" class="button small" ${page === 0 ? "disabled" : ""}>← Prev</button>
    <span class="evidence-pager-info">
      Page ${page + 1} of ${totalPages} · ${fmt.format(total)} rows · sorted by relevance
    </span>
    <button id="evidenceNextBtn" class="button small" ${page >= totalPages - 1 ? "disabled" : ""}>Next →</button>
  </div>`;

  return `
    ${pager}
    <div class="evidence-feed">
      ${slice.map((row) => evidenceCard(row)).join("")}
    </div>
    ${totalPages > 1 ? pager.replace("evidencePrevBtn", "evidencePrevBtn2").replace("evidenceNextBtn", "evidenceNextBtn2") : ""}
  `;
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

    <div class="chart-section">
      <div class="chart-section-head">
        <h3>Category analysis</h3>
        <small style="color:var(--muted)">heavy — loads after main charts</small>
      </div>
      <div class="panel-grid two">
        ${chartPanel("category_by_model", "Category × model heatmap", "", "420px")}
        ${chartPanel("cooccurrence", "Flag co-occurrence", "", "420px")}
      </div>
    </div>

    <section class="panel spaced">
      <div class="panel-head"><h2>Evidence</h2><small>matched rows · ranked by relevance</small></div>
      ${evidenceTable(state.data.evidence)}
    </section>`;
}

// ---------------------------------------------------------------------------
// Combined Data Explorer tab (explore + Q&A folded in at the bottom)
// ---------------------------------------------------------------------------

// explorerView stitches the full explore HTML with the Q&A section below a divider.
export function explorerView() {
  return `
    ${exploreView()}
    <hr style="margin: 2rem 0;" />
    <section class="panel">
      <div class="panel-head"><h2>Q&amp;A</h2><small>evidence-backed answers from your data</small></div>
    </section>
    ${qaView()}
  `;
}

// bindExplorerEvents covers filter controls, evidence pagination, and Q&A action buttons.
export function bindExplorerEvents() {
  bindFilterEvents();

  // Evidence pagination — both the top and bottom button pairs share the same handlers.
  const prevPage = () => {
    if ((state.evidencePage || 0) > 0) {
      state.evidencePage = (state.evidencePage || 0) - 1;
      render();
    }
  };
  const nextPage = () => {
    const total = state.data?.evidence?.length || 0;
    const maxPage = Math.max(0, Math.ceil(total / EVIDENCE_PAGE_SIZE) - 1);
    if ((state.evidencePage || 0) < maxPage) {
      state.evidencePage = (state.evidencePage || 0) + 1;
      render();
    }
  };
  $("#evidencePrevBtn")?.addEventListener("click", prevPage);
  $("#evidenceNextBtn")?.addEventListener("click", nextPage);
  $("#evidencePrevBtn2")?.addEventListener("click", prevPage);
  $("#evidenceNextBtn2")?.addEventListener("click", nextPage);

  // Q&A bindings.
  $("#qaBuildIndexBtn")?.addEventListener("click", startQaBuildIndex);
  $("#qaRefreshBtn")?.addEventListener("click", refreshQaStatus);
  $("#qaSubmitBtn")?.addEventListener("click", submitQaQuestion);
  $("#qaSearchBtn")?.addEventListener("click", submitQaSearch);
}
