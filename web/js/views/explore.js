// explore.js — filters, evidence renderers, and the chart-heavy Explore view.
// Also exports explorerView() which stitches explore + Q&A into the combined
// Data Explorer tab, and bindExplorerEvents() which covers both filter and Q&A bindings.
import { state, $, $$, esc, fmt, humanLabel, debounce } from "../state.js";
import { chartPanel, metricGrid, emptyState } from "../components.js";
import { loadRun, loadDetailCharts } from "../app.js";
import { render } from "../nav.js";
import { apiUrl, request } from "../api.js";

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
      <label class="filter-label" for="filter-${key}">${esc(label)}</label>
      <select id="filter-${key}" name="${esc(apiKey)}" multiple size="3" class="filter-select" data-filter-key="${esc(apiKey)}">${opts_html}</select>
    </div>`;
  };

  // Date range inputs — shown only when the backend reports min/max dates.
  const minDate = opts.dateStart?.[0] || "";
  const maxDate = opts.dateEnd?.[0] || "";
  const dateRange = minDate || maxDate ? `
    <div class="filter-control">
      <label class="filter-label" for="filter-date-start">Date from</label>
      <input id="filter-date-start" name="date_start" type="date" class="filter-input"
        value="${esc(f.date_start || "")}"
        min="${esc(minDate)}" max="${esc(maxDate)}">
    </div>
    <div class="filter-control">
      <label class="filter-label" for="filter-date-end">Date to</label>
      <input id="filter-date-end" name="date_end" type="date" class="filter-input"
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
        <label class="filter-label" for="filter-search">Search evidence</label>
        <input id="filter-search" name="search" type="search" autocomplete="off" class="filter-input" value="${esc(f.search || "")}" placeholder="Search post title, comment & summary…">
      </div>
      <div class="filter-control">
        <label class="filter-label" for="filter-minscore">Minimum Reddit score</label>
        <input id="filter-minscore" name="min_score" type="number" inputmode="decimal" autocomplete="off" class="filter-input" value="${f.min_score || ""}" placeholder="0">
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
  state.evidence.page = 1;
  loadRun();
}

// Reset filters and reload the run.
export function clearFilters() {
  state.filters = {};
  state.evidence.page = 1;
  loadRun();
}

export async function loadEvidence(page = state.evidence.page || 1) {
  return request(apiUrl("/api/evidence", {
    tag: state.tag,
    ...buildFilterParams(),
    page,
    page_size: state.evidence.page_size || 10,
  }));
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
function evidenceSection(label, title, body, index, kind) {
  if (!title && !body) return "";
  const controlId = `evidence-${index}-${kind}`;
  return `<section class="evidence-section" aria-labelledby="${controlId}-label">
    <div class="evidence-section-head">
      <span id="${controlId}-label" class="evidence-section-label">${label}</span>
      ${title ? `<strong class="evidence-section-title">${esc(title)}</strong>` : ""}
    </div>
    ${body ? `<p id="${controlId}" class="evidence-copy is-clamped" data-evidence-copy>${esc(body)}</p>
      <button class="evidence-expand" type="button" data-evidence-expand aria-controls="${controlId}" aria-expanded="false" hidden>Show more</button>` : ""}
  </section>`;
}

function evidenceCard(row, index) {
  const title = row.title_norm || "";
  const postBody = row.post_body_norm || "";
  const commentBody = row.comment_body_norm || (row.source_type === "comment" ? row.target_text || "" : "");
  const fallbackBody = !postBody && !commentBody ? row.target_text || row.description || "" : "";
  const date = row.created_at_norm || "";
  const subreddit = row.subreddit_norm || "";
  const vehicle = row.vehicle_mentioned || "";
  const sentiment = row.sentiment || "";
  const theme = humanLabel(row.top_complaint_category || "");
  const score = row.score_norm != null ? Number(row.score_norm) : 0;
  const permalink = row.permalink_norm
    ? `https://reddit.com${row.permalink_norm}`
    : "";

  return `<article class="evidence-item">
    <div class="evidence-meta">
      ${date ? `<span>${esc(date)}</span>` : ""}
      ${subreddit ? `<span>r/${esc(subreddit)}</span>` : ""}
      ${vehicle ? `<span>${esc(vehicle)}</span>` : ""}
      ${sentiment ? `<span class="tag">${esc(sentiment)}</span>` : ""}
      ${theme ? `<span class="tag">${esc(theme)}</span>` : ""}
      <span>Reddit score ${esc(score)}</span>
    </div>
    ${evidenceSection("Post", title, postBody, index, "post")}
    ${evidenceSection("Comment", "", commentBody, index, "comment")}
    ${evidenceSection("Evidence", "", fallbackBody, index, "fallback")}
    ${permalink ? `<a href="${esc(permalink)}" target="_blank" rel="noreferrer" class="evidence-external-link external-link" aria-label="Open this evidence on Reddit">↗</a>` : ""}
  </article>`;
}

export function evidenceTable() {
  const evidence = state.evidence || {};
  const rows = state.evidence.items || [];
  if (!rows.length) return `<div class="notice">No evidence rows match this view.</div>`;

  const page = evidence.page || 1;
  const totalPages = evidence.total_pages || 1;
  const total = evidence.total_items || rows.length;

  const pager = `<div class="evidence-pager">
    <button id="evidencePrevBtn" class="button small" ${page <= 1 ? "disabled" : ""}>← Prev</button>
    <span class="evidence-pager-info">
      Page ${page} of ${totalPages} · ${fmt.format(total)} rows · sorted by Reddit score
    </span>
    <button id="evidenceNextBtn" class="button small" ${page >= totalPages ? "disabled" : ""}>Next →</button>
  </div>`;

  return `
    ${pager}
    <div class="evidence-feed">
      ${rows.map((row, index) => evidenceCard(row, index)).join("")}
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
      ${evidenceTable()}
    </section>`;
}

// ---------------------------------------------------------------------------
export function explorerView() {
  return exploreView();
}

export function bindExplorerEvents() {
  bindFilterEvents();

  document.querySelectorAll("[data-evidence-copy]").forEach((copy) => {
    const button = copy.parentElement?.querySelector("[data-evidence-expand]");
    if (!button) return;
    const overflows = copy.scrollHeight > copy.clientHeight + 1;
    button.hidden = !overflows;
    if (!overflows) copy.classList.remove("is-clamped");
    button.addEventListener("click", () => {
      const expanded = button.getAttribute("aria-expanded") === "true";
      button.setAttribute("aria-expanded", String(!expanded));
      button.textContent = expanded ? "Show more" : "Show less";
      copy.classList.toggle("is-clamped", expanded);
    });
  });

  const goToEvidencePage = async (page) => {
    state.evidence = await loadEvidence(page);
    render();
  };
  const prevPage = () => goToEvidencePage(Math.max(1, (state.evidence.page || 1) - 1));
  const nextPage = () => goToEvidencePage(Math.min(state.evidence.total_pages, (state.evidence.page || 1) + 1));
  $("#evidencePrevBtn")?.addEventListener("click", prevPage);
  $("#evidenceNextBtn")?.addEventListener("click", nextPage);
  $("#evidencePrevBtn2")?.addEventListener("click", prevPage);
  $("#evidenceNextBtn2")?.addEventListener("click", nextPage);

}
