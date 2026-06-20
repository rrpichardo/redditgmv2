# Plan B Findings

## Baseline

- Checkout: `/Users/ricopichardo/.codex/worktrees/e8f3/redditgmv2`.
- HEAD: `c7a02de88160eda8f3ed199abe2b4aa86df5e548`, matching the Plan B target exactly.
- Branch: detached HEAD; `origin/main` and `main` point to the same commit.
- Working tree: clean. Root planning files are ignored operational notes and do not appear in Git status.
- Feature branch: `codex/plan-b-standalone`, created from the exact target commit.
- The prior root planning notes described pipeline hardening in `/Users/ricopichardo/.codex/worktrees/f3f8/redditgmv2`; they were stale for this task.

## Approved Scope

- Build Plan B only: B0-B9 plus B10 verification.
- Plan A remains deferred and must consume Plan B later.
- The attachment already specifies architecture, behavior matrices, error states, data provenance, frontend behavior, and verification expectations.

## Initial Repository Map

- Backend/API: `app.py`.
- Core analytics: `src/gm_insights.py`, `src/trend_insights.py`, `src/charts.py`.
- Config and artifacts: `src/app_config.py`, `src/run_store.py`, `src/qa_retrieval.py`, `src/pdf_export.py`.
- Jobs: `scripts/analyze_run.py`, `scripts/faiss_qa_job.py`, `scripts/trend_briefing_job.py`.
- Frontend: `web/js/views/*.js`, `web/js/api.js`, `web/js/nav.js`.
- Existing tests: milestone- and phase-oriented files under `tests/`.

## Prior Operational Context Worth Preserving

- In this repo family, detached HEAD is normal for Codex worktrees; attach this checkout to a feature branch before commits.
- Runtime verification must use the project virtual environment and prove HTTP/browser reachability, not infer readiness from startup logs.
- Earlier work established that the active runtime root can differ from the editing worktree. Plan B verification will therefore use an explicit isolated temporary runtime root.
- The user's established preference is to move from an approved implementation request into failing tests and code, while still stating the exact checkout and revision inspected.

## Baseline Verification

- Shared environment import probe passed for pytest, FastAPI, pandas, OpenAI, and FAISS.
- Sandboxed baseline: 284 tests passed; 20 browser/API tests errored only because the sandbox denied localhost socket binding in `tests/conftest.py::_free_port`.
- An escalated full-suite rerun did not return output and was terminated. The failure is environmental rather than a product assertion failure; B10 will rerun browser coverage with explicit localhost access.

## API and Data-Flow Findings

- `run_snapshot()` currently embeds up to 500 evidence rows and computes Explorer pagination client-side.
- `request_filters()` already centralizes all Explorer filters, so `/api/evidence` can reuse it and `filter_analyzed()`.
- `/api/trends/timeseries` is weekly-only, uses offset-sensitive timestamp parsing, has unstructured details, does not zero-fill missing buckets, and emits counts only.
- `CollectRequest` still owns `source`, ad-hoc `subreddits`, and `since_days`; collection directly resolves GM/competitor lists from the legacy repo.
- `/api/qa/status` currently reports only job records or `idle`; it does not compare Q&A metadata to the current published generation.
- Trend briefing downloads are resolved under the tag run root rather than the published generation and expose PDF only.

## Core Module Findings

- `normalize_reddit_frame()` determines `source_type` but does not persist `post_body_norm` or a distinct normalized comment body; `target_text` conflates the analyzed body.
- `analyzed_frame()` already converts missing Reddit score values to zero, preserving negative and large numeric scores.
- `evidence_table()` lacks `source_id`, `source_type`, post body, and distinct comment body and sorts only by score, so stable ties are not guaranteed.
- Raw complaint analytics should remain intact. A separate chart-presentation helper is the safest boundary for sentinel exclusion and counts.
- Cluster labeling still uses a module-level hardcoded prompt. Existing config has classification and synthesis prompt slots only and writes both config and secrets non-atomically.
- Q&A artifact writes already have a reusable atomic JSON helper, but metadata contains no generation, run, or classified-input fingerprint.
- The pipeline publishes artifact families from a staged generation; prompt and Q&A provenance should be written inside that same generation before publication.

## Frontend Findings

- Dashboard hardcodes weekly/count language and replaces all time-series failures with `Date data required to render this chart.`
- Quadrant and leaderboard chart containers mount even when no valid trend signals exist.
- Explorer currently paginates the bulk evidence payload 25 rows at a time in browser memory and embeds Q&A at the bottom of Explorer.
- Q&A polling clears an existing timer before starting, but navigation does not clear it; rendering checks only the Explorer view.
- `since_days` remains in both collection and settings payload construction.
- The existing five-tab navigation can host Q&A inside Dashboard without adding a tab; the Dashboard renderer is the correct composition point.

## Implementation Hooks

- `app.py` already supports `REDDITGM_RUNTIME_ROOT`; Plan B tests can isolate runtime by monkeypatching `app.RUNTIME` or launching a subprocess with that environment variable.
- Pipeline publication writes a `.run_id` marker into every published artifact family. Generation identity can be derived from `current.resolve().name`, and run identity can be read from the family marker.
- `AnalysisCoordinator._start_attempt()` is the correct place to attach prompt provenance to attempt metadata; a run-level snapshot can be written under `runs/<run_id>/config/` before trend execution.
- `build_chart_data()` is the current chart presentation boundary; complaint rows and counts can be changed there without altering raw analytics functions.
- `tests/test_m5.py` demonstrates the preferred API-test pattern: monkeypatch `app.RUNTIME` to a temporary root and use FastAPI `TestClient`.
- Existing browser fixtures write under the repository runtime and require localhost binding. New Plan B integration tests should use a dedicated temporary runtime fixture to avoid touching real data.

## B0 Implementation Result

- `src/timeseries.py` now owns UTC parsing, adaptive daily/weekly buckets, zero filling, timestamp coverage metadata, stable reason codes, finite-integer cluster filtering, and negative-share rates.
- Dashboard time-series copy is driven by backend granularity/detail, while velocity and Z-score cards explain their existing minimum-data thresholds.
- Focused verification: 56 tests passed across Plan B time-series, Phase 5 trends, and M3 dashboard coverage.

## B1/B2 Backend Implementation Result

- Normalization now preserves `post_body_norm` and `comment_body_norm` while retaining `target_text` compatibility.
- Generic/imported rows preserve already-normalized subreddit, score, date, permalink, and post-ID fields even when they do not carry every classifier key.
- `/api/evidence` applies the complete Explorer filter set, uses stable score/source ordering, returns one-based server pages, and declares `reddit_score` units.
- Date filtering now compares normalized UTC timestamps, avoiding pandas date/datetime incompatibility.

## B2/B3 Frontend Implementation Result

- Explorer now fetches exactly 10 evidence records per server page; filter changes reset to page one and no client-side bulk slicing remains.
- Evidence cards use an editorial Post/Comment hierarchy, measured two-line clamps, native keyboard buttons with `aria-expanded`, and compact external links.
- Dashboard now explicitly separates model-assigned predefined categories from cluster-discovered signals and defines `not_applicable` inline.
- Q&A was removed from Explorer and will be composed below Signals to watch during the generation-aware Q&A slice.
- Combined browser verification passed 36 tests, including actual overflow measurement, Enter-key expansion, and page-two navigation.

## B4/B5 Implementation Result

- Raw complaint functions remain unchanged; `complaint_chart_presentation()` now filters primary and secondary sentinels only for visual payloads.
- Chart payloads expose complaint record counts as `total_complaints`, `applicable`, and `excluded`, with an explicit no-applicable-themes fallback.
- `since_days` is gone from Collect, Settings, browser payloads, and current config defaults. The backend flag remains documented as dormant legacy compatibility.

## B6 Implementation Result

- Subreddit lists are atomic JSON records with generated IDs, normalized case-insensitive subreddit deduplication, safe names, typed ownership, and optimistic versions.
- GM and competitor seeds import once from an explicitly configured legacy collector; no machine-specific collector fallback remains.
- Collection receives `subreddit_list_id` and snapshots both JSON provenance and collector text beneath `runtime/<tag>/collect/<job_id>/config/` before spawning.
- `/api/health` now distinguishes collector configuration from path availability.
- The Gathering view provides create, edit, select, and save flows; Chromium verification proved a created named list is subsequently sent in the collection payload with no `since_days`.

## B7 Implementation Result

- The cluster-label prompt now has a safe config default, strict placeholder validation, and a parseable required JSON output contract.
- Config and secret writes use atomic sibling replacement; Settings can preview validation and reset to the server default.
- `/api/analyze` snapshots prompt text, source, validation, and SHA-256 under the immutable run directory before spawning.
- The coordinator carries the prompt hash in run/attempt metadata and passes the snapshot path to the trend worker, which uses that exact prompt for labeling.

## B8 Implementation Result

- Q&A metadata now records the exact classified-input SHA-256 plus run/generation provenance when available.
- `/api/qa/status` projects artifacts, current input, jobs, active analysis, and key availability into `ready`, `building`, `missing`, `failed`, `stale`, or `blocked_no_api_key`.
- Search and answer reject stale/non-ready artifacts with structured recovery guidance; request-scoped API keys satisfy readiness without being persisted.
- Q&A now sits directly below Signals to watch on Dashboard. Healthy state hides build controls; stale/failed/missing states expose only the relevant recovery action.
- Navigation and new polling both clear the existing Q&A timer. Chromium verified stale recovery placement and timer cleanup.

## B9 Implementation Result

- `TrendReportModel` is the single semantic source for both report formats, with deterministic signal-first cluster ordering and exact artifact round-tripping.
- Markdown includes timestamp coverage, cluster summaries, representative records, and explicit generation/run provenance; no-timestamp reports say trend direction is unavailable.
- Trend-report workers write Markdown first and treat PDF as independently fallible. A PDF renderer failure publishes Markdown and terminates as `completed_with_warnings` rather than discarding the useful artifact.
- Report downloads resolve through the atomically published `current` generation. Markdown and PDF have separate API routes and separate links on Trends and Dashboard.
- The coordinator pre-allocates the generation identity passed into the report, and uses the same identity when publishing the generation.
- `completed_with_warnings` is now terminal for generic job discovery and is preserved by coordinator child monitoring, preventing warning-complete workers from being mistaken for active jobs.

## B10 Verification Result

- Fresh full-suite evidence: 385 tests passed, including backend, coordinator, API, PDF, and Chromium coverage.
- A live Uvicorn process bound to `127.0.0.1:8766` with a new temporary runtime; `/api/health`, `/`, and `/static/js/app.js` returned 200.
- The empty time-series endpoint returned `no_classified_data` with explicit valid/invalid timestamp counts, rather than inventing chart data.
- The rendered browser showed the five-tab standalone interface and named subreddit-list editor with zero console errors.
- The live process shut down cleanly after verification; no real runtime data was read or mutated.
