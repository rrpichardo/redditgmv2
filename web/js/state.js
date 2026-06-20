// state.js — shared mutable app state + pure helpers (no app-module imports).

// Mutable state bag shared across view modules.
export const state = {
  // Workspace identity
  tag: "gm_vehicle_on_demand",
  view: "dashboard",
  data: null,
  report: "",
  filters: {},

  // Server config (loaded from /api/config on startup)
  config: null,

  // Settings fields (were in the rail inputs; now managed by Settings tab)
  provider: "openrouter",
  model: "gpt-oss-120b",
  apiKey: "",

  // Job polling timers
  classifyJobStatus: null,
  exportJobStatus: null,
  collectPollTimer: null,
  classifyPollTimer: null,
  exportPollTimer: null,
  trendsData: null,
  timeseriesData: null,
  trendJobStatus: null,
  trendBriefingJobStatus: null,
  trendPollTimer: null,
  trendBriefingPollTimer: null,
  qaJobStatus: null,
  qaHits: null,
  qaAnswer: null,
  qaPollTimer: null,
  evidence: {
    items: [],
    page: 1,
    page_size: 10,
    total_items: 0,
    total_pages: 0,
    score_unit: "reddit_score",
  },

  // Pipeline tracking (M2-frontend implements full UI)
  pipelineRunId: null,
  pipelinePollTimer: null,
  pipelineRuns: [],
};

// Shorthand DOM selectors.
export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

// Number formatters.
export const fmt = new Intl.NumberFormat("en-US");
export const pct = (value) => `${Number(value || 0).toFixed(1)}%`;

// ---------------------------------------------------------------------------
// Pure helpers — copied verbatim from web/app.js (lines 30-35, 36-41, 43-49,
// 93-97, 98-101, 102-110, 216-225). Exported so charts.js and view modules
// can import them without depending on the monolith.
// ---------------------------------------------------------------------------

// Human-readable byte size.
export const fileSize = (bytes) => {
  const value = Number(bytes || 0);
  if (!value) return "0 KB";
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(value / 1024))} KB`;
};

// HTML-escape a value for safe insertion into innerHTML.
export const esc = (value) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

// Returns a debounced wrapper that delays fn by ms milliseconds.
export function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

// Strip whitespace; fall back to a default string if empty.
export function compactText(value, fallback = "unknown") {
  const text = String(value ?? "").trim();
  return text || fallback;
}

// Replace underscores with spaces for display labels.
export function humanLabel(value) {
  return compactText(value).replaceAll("_", " ");
}

// Return the row with the highest numeric value in valueKey.
export function topItem(rows, labelKey, valueKey = "count") {
  if (!rows?.length) return null;
  return [...rows].sort((a, b) => Number(b[valueKey] || 0) - Number(a[valueKey] || 0))[0];
}

// Format a numeric value based on a named format hint.
export function formatValue(value, format) {
  const n = Number(value || 0);
  switch (format) {
    case "pct": return pct(n);
    case "count_pct": return fmt.format(Math.round(n));
    case "score": return n.toFixed(1);
    case "correlation": return n.toFixed(2);
    default: return fmt.format(Math.round(n));
  }
}
