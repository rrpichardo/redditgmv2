# Plan B Standalone Project Design

**Status:** Approved by the user's request to implement the attached Plan B.

**Baseline:** `/Users/ricopichardo/.codex/worktrees/e8f3/redditgmv2` at `c7a02de88160eda8f3ed199abe2b4aa86df5e548`.

## Goal

Ship Plan B as a complete standalone product increment against the current repository without adding `/api/dashboard`, a server-driven card contract, or any Plan A dependency. The result must make date coverage, evidence, complaint charts, collection lists, cluster prompts, Q&A artifacts, and trend reports honest, attributable, recoverable, and testable.

## Architecture

Plan B extends the existing FastAPI, pandas, file-artifact, SQLite run-ledger, and vanilla JavaScript architecture. New domain behavior lives in focused modules and pure helpers; `app.py` remains the HTTP composition layer. Existing endpoints remain compatible unless Plan B explicitly removes a browser request field.

Published analysis artifacts remain generation-scoped beneath the pipeline's `current` symlink. Mutable application configuration and subreddit-list definitions remain outside published analysis generations, while every collection or analysis run stores an immutable snapshot of the effective mutable input it used.

## Backend Components

### Time-series truthfulness

Add a pure time-series builder that:

- parses mixed-offset timestamps into UTC;
- reports valid and invalid timestamp counts;
- returns `no_classified_data`, `no_valid_timestamps`, `single_day_no_timeseries`, or `insufficient_nonempty_buckets` when a chart cannot be drawn;
- uses daily UTC buckets for spans under 14 days and Monday-based weekly UTC buckets for spans of 14 days or more;
- zero-fills every missing bucket in the inclusive range;
- requires at least two non-empty buckets;
- emits negative share as the default series and declares `unit: negative_share_pct`;
- emits cluster counts only for finite integer cluster IDs.

The existing `/api/trends/timeseries` route delegates to this builder and always returns a stable `reason_code` plus plain-English `detail` when unavailable.

### Evidence query

Normalization persists `source_type`, `post_body_norm`, and `comment_body_norm` while preserving `target_text` for existing classification behavior. `GET /api/evidence` accepts the complete Explorer filter set plus one-based `page` and bounded `page_size`. It filters server-side, sorts by Reddit score descending and `source_id` ascending, and returns page metadata. Missing Reddit score remains zero; negative and very large scores remain valid.

The legacy evidence slice in `/api/run` remains temporarily for compatibility with Classify and other consumers, but Explorer uses only the paginated endpoint.

### Complaint presentation

Raw `complaint_summary()` and `all_complaint_mentions()` remain unchanged. A chart-specific helper removes blank and sentinel categories from both primary and secondary mentions and returns rows plus `total_complaints`, `applicable`, and `excluded` record counts. The current chart payload carries that metadata and an honest empty state when no applicable category remains.

### Subreddit-list store

Add `src/subreddit_lists.py` and `config/subreddit_lists/`. The store uses stable opaque IDs, validated display names, the types `gm`, `competitor`, and `custom`, normalized `r/`-free subreddit names, case-insensitive duplicate removal, atomic JSON writes, and optimistic version conflicts. IDs and paths are generated internally so user input cannot select filenames or traverse directories.

On first use, seed GM and competitor lists from configured legacy list files when available. CRUD endpoints list, fetch, create, and update definitions. `CollectRequest` selects `subreddit_list_id`; collection resolves that list, writes an immutable snapshot under the tag's run configuration directory, and passes the snapshot path to the legacy collector. The collector root becomes configurable and `/api/health` reports the configured path and availability.

### Cluster-prompt configuration

Add a safe default cluster-label prompt to application config. A validator requires the complete placeholder set and validates the declared JSON response example as an object containing the expected output keys. Settings can preview validation, save a valid prompt, or reset to default. Config and secret writes use atomic sibling replacement.

The trend labeler loads the effective prompt with fallback to the safe default. Each analysis attempt snapshots the effective prompt, its SHA-256 hash, and validation result within the attempt configuration before trend work begins.

### Generation-aware Q&A

Add a stable SHA-256 fingerprint of the classified CSV used to build Q&A artifacts. `metadata.json` records the fingerprint plus generation and run identifiers when available. `/api/qa/status` compares metadata with the current classified input and combines artifact, job, API-key, and pipeline state into one of `ready`, `building`, `missing`, `failed`, `stale`, or `blocked_no_api_key`.

Search and answer requests reject stale or missing artifacts with recovery guidance. Existing imported artifacts remain readable when their fingerprint matches; previously failed jobs can be retried.

### Canonical trend report

Add `src/trend_report.py` with a typed `TrendReportModel` built once from labels, examples, and signals. Render Markdown and PDF from that model. Writes are atomic and generation-scoped. The worker publishes Markdown first; a later PDF failure preserves Markdown and records a completed-with-warnings result naming successful and failed artifacts. Download endpoints expose both formats from the current generation.

## Frontend Components

### Dashboard composition and honest empty states

Dashboard renders two explicit sections:

- **Predefined categories:** Sentiment, Signal flags, Complaint themes, Vehicles. The description says these are fixed choices assigned by the model, not human-verified labels.
- **Discovered from your data:** Signals to watch, time series, Velocity × Z-score, Trend leaderboard. The description says these are cluster-derived and are not subreddit lists.

An inline glossary defines `not_applicable`. Time-series headings and accessible tables use the returned granularity and unit. Every unavailable time-series, quadrant, leaderboard, category-by-model, or co-occurrence card displays the backend or validity-derived explanation instead of an empty chart.

### Explorer evidence

Rename the score filter to **Minimum Reddit score** and use the search placeholder **Search post title, comment & summary…**. Explorer fetches `/api/evidence` whenever filters or page change and renders 10 items per page.

Evidence cards contain independently labeled Post and Comment sections, hide absent sections, clamp content to two lines, and show a keyboard-operable expand control only after actual overflow measurement. The control updates `aria-expanded`. The Reddit permalink is a compact external-link action.

### Collection and settings

Remove `since_days` controls and request fields. Collect offers a named-list selector plus view, edit, create, and conflict-aware save flows. Settings adds cluster-prompt edit, validation preview, reset, and save behavior. The backend's legacy `--since-days` support remains dormant and documented.

### Q&A recovery

Move Q&A below Signals to watch on Dashboard. Healthy `ready` state shows the question interface without manual build controls. `missing`, `failed`, `stale`, and `blocked_no_api_key` render contextual guidance and only the valid recovery action. Navigation always clears Q&A polling, and starting polling first clears any existing timer.

### Report downloads

Trend report UI displays Markdown and PDF independently based on artifact status, including partial-success copy when Markdown exists but PDF failed.

## Data Flow

1. Collection selects a stored list and snapshots it before invoking the configured legacy collector.
2. Analysis snapshots the effective cluster prompt and uses it for trend labeling.
3. The pipeline stages classified, report, trend, download, and Q&A artifacts in an immutable generation and publishes them through `current`.
4. Q&A metadata and trend reports carry generation/input provenance so browser status can distinguish ready from stale.
5. Dashboard and Explorer request reason-bearing, paginated, or provenance-aware endpoints rather than inferring availability from empty arrays.

## Error Handling

- Invalid timestamps and insufficient coverage are successful API responses with stable reason codes, not server errors.
- Invalid prompt templates return HTTP 422 with structured validation errors and are never saved.
- Subreddit-list validation returns HTTP 422; stale update versions return HTTP 409 with the current record.
- Missing legacy collector configuration is visible in health and collection responses.
- Q&A stale/missing/no-key states are explicit statuses, not `idle`.
- Trend Markdown remains published if PDF rendering fails; job status records the partial outcome.

## Testing and Verification

Use strict red-green-refactor slices. Backend tests cover the full date matrix, evidence filters and page math, score semantics, complaint presentation counts, list-store validation and conflicts, prompt validation and snapshots, Q&A state transitions, and report consistency/partial failure. Browser tests cover section grouping, reason text, evidence overflow accessibility and pagination, Q&A placement/recovery/timer cleanup, removed `since_days`, list editing, and independent report downloads.

The final end-to-end run uses a temporary runtime root with synthetic timestamp fixtures. It must not modify the real dataset, duplicate production rows, or shift production dates. Completion requires targeted tests, the full pytest suite with localhost access, and live browser verification of the Plan B workflows.

## Explicitly Out of Scope

- Plan A's `/api/dashboard` contract or server-defined card composition.
- LLM narrative rewriting for trend reports.
- Fetching historical Reddit data beyond what the collector can retrieve.
- Migrating the collector executable into this repository.
