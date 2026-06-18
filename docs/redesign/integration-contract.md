# redditgm v2 — Frozen Integration Contract (Phase 0)

Status: FROZEN as of 2026-06-18. Both the backend track (Track A) and the frontend
track (Track B) build against this. Changes require updating this doc first.

- Contract A (Pipeline / run API) and Contract B (step-state vocabulary) are
  frozen-on-paper here; they are implemented in Phase 1 Track A.
- Contract C (chart_specs → ECharts mapping) is implemented in Phase 0 (web/js/charts.js).

---

## Contract A — Pipeline / run API (frozen; built in Phase 1 Track A)

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
          name, state: <step-state>,          # §Contract B vocabulary
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

---

## Contract B — Step-state vocabulary (frozen)

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

---

## Contract C — chart_specs → ECharts mapping (implemented in Phase 0)

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
