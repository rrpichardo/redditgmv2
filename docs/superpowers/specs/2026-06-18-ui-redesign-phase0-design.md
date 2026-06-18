# Phase 0 — Foundation + Integration Contract (design spec)

Date: 2026-06-18
Status: approved (design); pending spec review → implementation plan
Source plan: redditgm v2 UI/UX Redesign Plan rev. 3
Scope of this doc: **Phase 0 only** (the redesign plan's "Foundation + contract" phase = milestone M0 + freezing the integration contract).

---

## 1. Summary (plain English)

The web UI today is one fragile 2,024-line JavaScript file (`web/app.js`) with hand-drawn
charts and no shared design system. Phase 0 is **foundation work only** — it makes the codebase
safe to build on for the larger redesign that follows. It does **not** add new features.

Three things ship:

1. **Split the monolith** into ~12 small, single-purpose files. The app behaves identically.
2. **Replace every hand-drawn chart** with Apache ECharts, plus one shared light theme and proper
   cleanup so re-rendering never leaks.
3. **Freeze the integration contract** — a written agreement (data shapes, pipeline status
   vocabulary, chart-mapping rules) that lets the later backend and frontend work proceed
   independently without late surprises.

Plus a light design-token system (colors/spacing/type) with **two separate color lanes** so
"negative sentiment" (data) and "failed" (system) never visually collide.

It is verified by: the existing 180 pytest tests staying green, **plus** new browser tests proving
every chart type renders with no console errors and no layout overflow at 1440 / 768 / 375 px.

---

## 2. Scope

### In scope (Phase 0)
- Module split of `web/app.js` → ES modules under `web/js/` (no build step; native `type="module"`).
- Vendored ECharts at `web/vendor/echarts.min.js` + one custom light theme + `dispose()` discipline.
- A migration of all **6 existing chart types** (bar, grouped_bar, stacked_bar, stacked_bar_100,
  scatter, heatmap) from hand-drawn HTML/SVG to ECharts, across Dashboard + Explore.
- A CSS design-token system in `web/styles.css` with the dual color semantics (§5.3).
- A **frozen integration contract** document: `docs/redesign/integration-contract.md` (§6).
- New browser-based regression tests + chart-contract checks (§7).

### Out of scope (later phases — do NOT build now)
- The 5-tab information architecture restructure (that is M1 / Phase 1 Track B).
- The "Analyze data" button, the orchestrator, the Pipeline tab, `src/run_store.py`,
  `scripts/analyze_run.py`, and the `/api/analyze` + `/api/pipeline/*` endpoints
  (Phase 1 Track A). Phase 0 only **writes their shapes on paper** (§6 contracts A & B).
- New trend/time-series charts (velocity×z-score quadrant, sentiment-over-time, etc.) — M3.
- Any change to `src/charts.py` chart colors, `src/trend_insights.py` math, or backend endpoints.
- React/TypeScript migration; dark mode.

---

## 3. Decisions locked

These three engineering calls were made on Rico's behalf (he delegated the technical detail):

| # | Decision | Choice | Why |
|---|----------|--------|-----|
| 1 | Module split target | Mirror the **current 7 tabs**; M1 later reorganizes into 5 tabs | Safe & reversible; app stays fully usable through the backend track; biggest monolith-breakup win now |
| 2 | Chart migration scope | Port **all 6 existing chart types** to ECharts now | The ugly-charts problem is the most visible pain; do the foundation once |
| 3 | Verification tooling | **Python Playwright + pytest** (already installed) | Integrates with the existing 180-test suite; durable regression coverage; green checkmarks instead of claims |

Plus from the source plan (unchanged): light theme only; ECharts; keep all ~18 chart specs;
backend `chart_specs`/`chart_data` payload reused unchanged.

---

## 4. Module architecture (frontend)

`web/app.js` is replaced by ES modules under `web/js/`, served at `/static/js/*` (the existing
`/static` → `web/` mount already supports this). `web/index.html` keeps its 7 tabs and loads a
single entry module.

```
web/
  index.html                  # unchanged structure; <script type="module" src="/static/js/app.js">
  vendor/
    echarts.min.js            # NEW — vendored, no CDN at runtime
  js/
    app.js                    # bootstrap: wires global events, initial load, view dispatch
    state.js                  # the shared mutable `state` object + dom/format helpers ($, $$, fmt, pct, esc, debounce)
    api.js                    # apiUrl(), request(), and every fetch wrapper (run, charts, collect, classify, trends, qa, exports)
    charts.js                 # ECharts init + theme registration + spec→option mapping + renderChart() + dispose registry
    nav.js                    # setView(), tab + jump binding
    components.js             # shared render helpers reused across views (panel, metricGrid, emptyState, evidenceTable, jobStatusCard, bars)
    views/
      dashboard.js
      collect.js
      classify.js
      explore.js
      briefing.js
      trends.js
      qa.js
```

Rules:
- The shared `state` lives in `state.js` and is imported where needed (single mutable source, as today).
- Each `views/*.js` exports a render function returning an HTML string + a binder for its events
  (matching today's `render()` / `bindViewEvents()` pattern), so behavior is preserved.
- Polling timers stay in `state` exactly as today.
- This is a refactor: **every moved function keeps its current behavior.** No "improvements" beyond
  the split itself.

---

## 5. Charts + design system

### 5.1 ECharts integration
- ECharts vendored at `web/vendor/echarts.min.js` (pinned version; full build acceptable for V1).
- `charts.js` registers one custom **light theme** matching the design tokens (axis/grid/tooltip
  colors from the neutral token set; series colors from each spec's `color_map`, else theme palette).
- **Dispose discipline:** `charts.js` keeps a registry of live ECharts instances keyed by container
  element. Before (re)rendering a container, any existing instance for it is `dispose()`d. A single
  resize handler calls `.resize()` on all live instances (debounced).
- Charts honor `prefers-reduced-motion` (disable ECharts animation when set).
- Accessibility: each chart container carries an `aria-label`/description, and ECharts'
  table-equivalent fallback is enabled (standing requirement).

### 5.2 Chart mapping
The backend payload (`chart_specs` + `chart_data` from `src/charts.py`) is consumed **unchanged**.
The exact spec→ECharts mapping is frozen in §6 Contract C and implemented in `charts.js`.

### 5.3 Design tokens + dual color semantics
CSS custom properties in `:root` (`web/styles.css`). Three groups:

**(a) Neutral UI palette** — surfaces, text, borders, spacing scale, type scale, radii, motion
(150–300 ms; disabled under `prefers-reduced-motion`).

**(b) Data-sentiment tokens (charts & data UI).** Chart series colors stay sourced from
`src/charts.py` `color_map` (canonical, unchanged). These tokens **mirror those values** for
non-chart UI (e.g. sentiment pills) and must be kept in sync — no chart recolor in Phase 0:

| Token | Value | Meaning |
|-------|-------|---------|
| `--data-negative` | `#ef4444` | negative sentiment |
| `--data-neutral`  | `#94a3b8` | neutral |
| `--data-positive` | `#22c55e` | positive |
| `--data-critical` / `--data-major` / `--data-minor` / `--data-none` | `#dc2626` / `#f59e0b` / `#3b82f6` / `#94a3b8` | severity scale |

**(c) System-status tokens (pipeline & run health).** A **separate hue lane** — red is reserved
for data-negative; "failed" uses a distinct magenta/rose and always pairs with an icon + word.
Starting palette (final hex confirmed against WCAG AA contrast during M0):

| State | Token | Starting color | Icon | UI label |
|-------|-------|----------------|------|----------|
| pending | `--status-pending` | `#94a3b8` | `ti-clock` | Pending |
| running | `--status-running` | `#2563eb` | `ti-loader-2` | Running |
| completed | `--status-done` | `#15803d` | `ti-check` | Done |
| completed_with_warnings | `--status-warning` | `#b45309` | `ti-alert-triangle` | Warnings |
| blocked | `--status-blocked` | `#475569` | `ti-player-pause` | Blocked |
| skipped | `--status-skipped` | `#94a3b8` | `ti-arrow-right` | Skipped |
| failed | `--status-failed` | `#be123c` | `ti-x` | Failed |
| cancelled | `--status-cancelled` | `#64748b` | `ti-ban` | Cancelled |

Hard requirement (from architecture review): **every status carries a non-color cue** (icon + text),
so the UI is never color-only. These tokens are defined in Phase 0 even though the Pipeline tab that
consumes most of them ships later — they belong to the frozen design system.

---

## 6. The frozen integration contract

Written to `docs/redesign/integration-contract.md`. Three contracts. **Only Contract C is
implemented in Phase 0**; A and B are frozen-on-paper for later phases.

### Contract A — Pipeline / run API (frozen; built in Phase 1 Track A)

```
POST /api/analyze
  req:  { tag, provider, model, api_key? }
  200:  { run_id: str, job_id: str }          # job_id = coordinator job (jobs.py kind="analyze")
  409:  { error: "run_active", message, active_run_id }   # tag-level run lock held

GET /api/pipeline/status?tag=&run_id=
  200:  {
          run_id, tag,
          state: <run-state>,                 # running | completed | completed_with_warnings | failed | cancelled
          started_at, ended_at|null,
          config: { provider, model },
          steps: [ <step> ... ]               # ordered, see step list below
        }
  <step> = {
          name, state: <step-state>,          # §6 Contract B vocabulary
          started_at|null, ended_at|null,
          processed, total, errors, error_rate,
          warning|null,                        # reason string when completed_with_warnings / blocked / skipped
          artifact_paths: [str ...],           # run-scoped snapshot paths
          log_available: bool
        }

GET /api/pipeline/log?tag=&run_id=&step=
  200:  text/plain (tail of the step log)

POST /api/pipeline/cancel
  req:  { tag, run_id }
  200:  { cancelled: bool, state }

POST /api/pipeline/retry
  req:  { tag, run_id, step }
  200:  { run_id, job_id }                     # re-runs `step` + all downstream steps
```

**Canonical ordered step list** (linear dependency chain, hardcoded for V1):

```
["prepare", "classify", "briefing", "trends", "trend_pdf", "qa_index"]
```

- `prepare` — normalize upload + heuristic seed; guards (0/1 row, timestamps, span, API key).
- `classify` — `scripts/classify_job.py`.
- `briefing` — thin `scripts/briefing_job.py` over `src/gm_insights.py` synthesis.
- `trends` — clustering **and** trend signals (one worker: `scripts/trend_job.py`).
- `trend_pdf` — `scripts/trend_briefing_job.py`.
- `qa_index` — `scripts/faiss_qa_job.py`.

Retry-from-step invalidates and re-runs everything **after** the retried step in this order.

### Contract B — Step-state vocabulary (frozen)

Eight states. Maps onto / extends the existing child-job states in `src/jobs.py`
(`pending/running/completed/failed/interrupted`).

| State | Terminal? | Meaning | Derivation by coordinator |
|-------|-----------|---------|---------------------------|
| `pending` | no | not started | initial |
| `running` | no | in progress | child `running` |
| `completed` | yes | clean success | child `completed`, `errors == 0` |
| `completed_with_warnings` | yes | succeeded, degraded | child `completed` with `errors > 0`/`error_rate` above 0 but below fail threshold, **or** a `trend_warning` present |
| `blocked` | yes | prerequisite missing — never started | a `prepare` guard failed (e.g. 0 rows, bad timestamps, missing API key) |
| `skipped` | yes | not applicable for this run | step intentionally not run (e.g. no API key → LLM-dependent step skipped) |
| `failed` | yes | error | child `failed`, non-zero exit, **or** child `interrupted` (dead PID / stale heartbeat → reported as failed with reason="interrupted") |
| `cancelled` | yes | user cancelled | cancel endpoint stopped coordinator/child |

A green "Done" means trustworthy. `completed_with_warnings` always carries a `warning` reason string.

### Contract C — chart_specs → ECharts mapping (implemented in Phase 0)

Source of truth: `src/charts.py` `CHART_SPECS`. For every chart id the renderer reads its spec and
maps to an ECharts `option`. Shared rules: apply `sort` client-side before mapping; if
`rows < minimum_rows` render the `fallback` message instead of a chart; render `quality_note` as a
caption; values are already NaN-cleaned to `null` by the backend; `value_format` drives tooltip/label
formatting (`count` | `pct` | `count_pct` | `score` | `correlation` | `mixed`).

| Spec `type` | ECharts | Field mapping |
|-------------|---------|---------------|
| `bar` | `bar` series | category = `x_field`; value = `y_field`; per-category color from `color_map` if present, else theme |
| `grouped_bar` | multiple `bar` series | category = `x_field`; one series per key in `series_keys`; order from `series_order` |
| `stacked_bar` (long) | stacked `bar` series | records `{x_field, series_field, value_field}`; pivot to one stacked series per distinct `series_field`; stack order = `series_order`; colors = `color_map` |
| `stacked_bar_100` (wide) | stacked `bar` series, normalized | row `{x_field + one numeric col per category}`; normalize each row to 100%; one stacked series per category column; `series_order` + `color_map` |
| `scatter` | `scatter` series | x = `x_field`, y = `y_field`; point color by `color_field` via `color_map`; `label_field` in tooltip/label |
| `heatmap` (lazy) | `heatmap` series + `visualMap` | `category_by_model`: `{rows, columns, values}` matrix (apply `normalize: "row"`); `cooccurrence`: nested dict → matrix |

---

## 7. Verification

Success = all of the following green.

1. **Existing suite unaffected:** the current 180 pytest tests still pass.
2. **App loads & all 7 tabs work** (manual + smoke test): no console errors; Dashboard, Collect,
   Classify, Explore, Briefing, Trends, Q&A all reachable and render.
3. **Chart-contract checks** (new): for each of the 6 chart types, the `charts.js` mapping produces a
   valid ECharts `option` for a representative payload (asserted via Playwright `page.evaluate` against
   the live module) **and** the chart renders an ECharts canvas in the DOM.
4. **Browser smoke (new):** seeded with the golden fixture, the app renders all Explore charts with
   **no console errors** and **no horizontal overflow at 1440 / 768 / 375 px**.

### Test mechanism
- New file: `tests/test_redesign_foundation.py` (kept separate from `tests/test_phase0.py`, which is
  the original project's correctness suite — unrelated to this redesign).
- A pytest fixture launches `uvicorn` on an ephemeral port against a temp `RUNTIME` seeded with the
  existing golden fixture CSV (so `/api/run` + `/api/charts/detail` return data for all chart types).
- Playwright (Chromium) drives the page; assertions via DOM checks + `page.evaluate`.
- Requires a one-time `playwright install chromium`. Tests **skip gracefully** (not fail) if the
  Chromium binary is absent, so CI without it stays green.

---

## 8. Risks & non-goals

- **Behavior drift** is the main risk: a refactor across ~12 files can subtly change a tab's behavior.
  Mitigation: the browser smoke + chart-contract tests, and keeping the split mechanical (no logic
  edits beyond moving code).
- **Not behavior-neutral by design** in one respect: charts are re-rendered via ECharts, so chart
  visuals change (that is the point). All non-chart behavior is preserved.
- Contracts A & B are **paper only** in Phase 0 — implementing them here would be out of scope and
  premature (the backend track owns them).
```
