# Plan B Standalone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the complete standalone Plan B against `c7a02de`, with truthful trend coverage, paginated evidence, stored collection lists, prompt and generation provenance, recoverable Q&A, dual-format trend reports, and isolated verification.

**Architecture:** Keep FastAPI as the HTTP composition layer and move new behavior into pure, focused modules. Preserve current endpoints and raw analytics where possible; add reason-bearing and provenance-aware boundaries that the vanilla JavaScript UI consumes directly.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, pandas, scikit-learn, FAISS, fpdf2, SQLite/file artifacts, vanilla ES modules, ECharts, pytest, Playwright.

---

## File Structure

- Create `src/timeseries.py`: UTC parsing, adaptive bucket selection, zero filling, reason codes, and rate series.
- Create `src/subreddit_lists.py`: typed list records, validation, atomic persistence, seeding, and conflict checks.
- Create `src/trend_report.py`: canonical report model plus Markdown rendering.
- Create `tests/test_plan_b_timeseries.py`: B0 date matrix and API contracts.
- Create `tests/test_plan_b_evidence.py`: B1/B2 normalization, filtering, sorting, and pagination.
- Create `tests/test_plan_b_presentation.py`: B3/B4/B5 chart metadata and browser-source contracts.
- Create `tests/test_plan_b_subreddit_lists.py`: B6 store, CRUD, collection snapshot, and health contracts.
- Create `tests/test_plan_b_prompt.py`: B7 validation, fallback, atomic saves, and run snapshots.
- Create `tests/test_plan_b_qa.py`: B8 fingerprint and status-state contracts.
- Create `tests/test_plan_b_reports.py`: B9 canonical model, Markdown/PDF consistency, publication, and partial failure.
- Create `tests/test_plan_b_browser.py`: accessible evidence expansion, pagination, grouping, Q&A recovery, polling cleanup, and list-editor flows.
- Modify `app.py`: expose new endpoints and compose the new modules.
- Modify `src/gm_insights.py`: preserve distinct evidence fields and expose stable evidence rows.
- Modify `src/charts.py`: complaint presentation data and metadata.
- Modify `src/app_config.py`: default cluster prompt, validation, reset, and atomic writes.
- Modify `src/trend_insights.py`: load validated effective cluster prompt.
- Modify `src/qa_retrieval.py`: classified-input fingerprint and generation metadata.
- Modify `scripts/analyze_run.py`: prompt snapshot and provenance arguments.
- Modify `scripts/faiss_qa_job.py`: receive generation/run provenance.
- Modify `scripts/trend_briefing_job.py`: build one model, publish Markdown then PDF, and report partial failure.
- Modify `web/js/api.js`: evidence query helper and structured error detail handling.
- Modify `web/js/state.js`: server-driven evidence page state.
- Modify `web/js/views/dashboard.js`: sections, honest chart states, Q&A relocation, and report links.
- Modify `web/js/views/explore.js`: labels, server pagination, evidence cards, and removal of embedded Q&A.
- Modify `web/js/views/collect.js`: remove `since_days` and add named-list editor.
- Modify `web/js/views/settings.js`: remove `since_days` and add prompt validation/reset.
- Modify `web/js/views/qa.js`: generation-aware status/recovery and exported polling cleanup.
- Modify `web/js/nav.js`: stop Q&A polling on navigation.
- Modify `web/styles.css`: two-line clamps, overflow controls, section descriptions, and compact external links.
- Modify `config.json`: remove `since_days`, add cluster prompt.
- Modify `README.md`: document the configurable legacy collector root and dormant backend lookback compatibility.

### Task 1: B0 UTC Time-Series Contract and Honest Trend Cards

**Files:**
- Create: `src/timeseries.py`
- Create: `tests/test_plan_b_timeseries.py`
- Modify: `app.py`
- Modify: `web/js/views/dashboard.js`
- Modify: `web/js/charts.js`

- [ ] **Step 1: Write the failing date-matrix tests**

```python
@pytest.mark.parametrize(
    ("timestamps", "ok", "reason", "granularity"),
    [
        (["2026-01-01T12:00:00-04:00"], False, "single_day_no_timeseries", None),
        (["2026-01-01T23:30:00-04:00", "2026-01-03T00:30:00+00:00"], True, None, "day"),
        (["2026-01-01", "2026-01-15"], True, None, "week"),
        (["bad", None], False, "no_valid_timestamps", None),
    ],
)
def test_build_timeseries_contract(timestamps, ok, reason, granularity):
    frame = classified_frame(timestamps)
    result = build_timeseries(frame)
    assert result["ok"] is ok
    assert result.get("reason_code") == reason
    assert result.get("granularity") == granularity
```

Add explicit tests for sparse months, mixed offsets, UTC boundary shifts, missing daily/weekly buckets, null/malformed cluster IDs, two non-empty buckets, valid/invalid counts, and `negative_share_pct` values.

- [ ] **Step 2: Run the tests and verify RED**

Run: `/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_plan_b_timeseries.py -q`

Expected: collection fails because `src.timeseries` does not exist.

- [ ] **Step 3: Implement the pure builder and route delegation**

```python
def build_timeseries(frame: pd.DataFrame) -> dict[str, Any]:
    timestamps = pd.to_datetime(frame.get("created_at_norm"), errors="coerce", utc=True)
    valid = frame.loc[timestamps.notna()].copy()
    # choose day for span < 14, otherwise Monday-based week; reindex full range
    # return negative_share_pct as the default series and finite integer clusters only
```

Make `/api/trends/timeseries` return this result and add `no_classified_data` when the classified file is absent.

- [ ] **Step 4: Verify GREEN and frontend source RED**

Run the time-series tests, then add assertions that Dashboard renders returned `detail`, dynamic granularity/unit labels, and validity-derived empty-state copy for quadrant, leaderboard, category-by-model, and co-occurrence.

- [ ] **Step 5: Implement honest Dashboard rendering**

Render chart containers only when usable data exists. Use `timeseriesData.detail`, `granularity_label`, and `unit`. Derive quadrant/leaderboard explanations from `velocity.data_note` and `zscore.data_note` when no valid points remain.

- [ ] **Step 6: Run focused tests and commit**

Run: `/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_plan_b_timeseries.py tests/test_phase5.py tests/test_m3.py -q`

Commit: `feat: add truthful UTC trend timeseries`

### Task 2: B1/B2 Reddit Score Semantics and Paginated Evidence API

**Files:**
- Create: `tests/test_plan_b_evidence.py`
- Modify: `src/gm_insights.py`
- Modify: `app.py`

- [ ] **Step 1: Write failing normalization and pagination tests**

```python
def test_normalization_preserves_post_and_comment_fields():
    frame = normalize_reddit_frame(pd.DataFrame([{
        "post_id": "p1", "comment_id": "c1", "post_title": "Title",
        "post_selftext": "Parent body", "comment_body": "Reply", "comment_score": "-4",
    }]))
    row = frame.iloc[0]
    assert row["source_type"] == "comment"
    assert row["post_body_norm"] == "Parent body"
    assert row["comment_body_norm"] == "Reply"
    assert row["score_norm"] == -4

def test_evidence_endpoint_sorts_score_then_source_id_and_paginates(client, runtime):
    response = client.get("/api/evidence?tag=evidence&page=2&page_size=10&search=reply")
    assert response.json().keys() >= {"items", "page", "page_size", "total_items", "total_pages"}
```

Cover negative, missing-to-zero, and very-large scores; post-only, comment-only, deleted, missing-parent, and imported rows; every Explorer filter; invalid pages; and stable ties by `source_id`.

- [ ] **Step 2: Run tests and verify RED**

Expected: missing normalized columns and 404 for `/api/evidence`.

- [ ] **Step 3: Add distinct evidence fields and stable query helper**

```python
def evidence_table(df: pd.DataFrame, limit: int | None = None) -> pd.DataFrame:
    columns = ["source_id", "source_type", "post_id_norm", "title_norm",
               "post_body_norm", "comment_body_norm", "score_norm",
               "description", "created_at_norm", "subreddit_norm", "permalink_norm"]
    out = analyzed.loc[:, existing].sort_values(
        ["score_norm", "source_id"], ascending=[False, True], kind="mergesort"
    )
    return out.head(limit) if limit is not None else out
```

- [ ] **Step 4: Add `GET /api/evidence`**

Reuse `request_filters()`, clamp `page_size` to 1-100, use one-based pages, and return an empty final page rather than duplicating rows.

- [ ] **Step 5: Run focused tests and commit**

Run: `/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_plan_b_evidence.py tests/test_phase0.py tests/test_m3.py -q`

Commit: `feat: add paginated evidence API`

### Task 3: B2/B3 Explorer Evidence UI and Dashboard Grouping

**Files:**
- Create: `tests/test_plan_b_browser.py`
- Modify: `web/js/api.js`
- Modify: `web/js/state.js`
- Modify: `web/js/views/explore.js`
- Modify: `web/js/views/dashboard.js`
- Modify: `web/styles.css`

- [ ] **Step 1: Write failing browser/source contract tests**

Assert the score label is `Minimum Reddit score`, search placeholder is `Search post title, comment & summary…`, `/api/evidence` is requested with `page_size=10`, the card has labeled Post and Comment sections, and the expansion button is absent for non-overflowing text.

```python
button = page.locator("[data-evidence-expand]").first
button.focus()
button.press("Enter")
assert button.get_attribute("aria-expanded") == "true"
```

Assert Dashboard shows the two required section headings/descriptions and the `not_applicable` glossary.

- [ ] **Step 2: Run browser/source tests and verify RED**

Run source-only selectors first; run Playwright tests with localhost permission.

- [ ] **Step 3: Implement server-driven evidence state**

Add `state.evidence = {items: [], page: 1, page_size: 10, total_items: 0, total_pages: 0}` and an async `loadEvidence()` that uses current filters. Filter changes reset to page 1 and load both charts and evidence without relying on `/api/run.evidence`.

- [ ] **Step 4: Implement accessible evidence cards**

Use two-line CSS clamps. After render, compare `scrollHeight` with `clientHeight`; only then reveal the corresponding button. Button click and Enter/Space toggle a class and `aria-expanded`.

- [ ] **Step 5: Add predefined/discovered composition**

Place Sentiment, Signal flags, Complaint themes, and Vehicles under Predefined. Place signals, time series, quadrant, and leaderboard under Discovered. Remove Q&A from Explorer in preparation for Task 8.

- [ ] **Step 6: Run focused tests and commit**

Run: `/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_plan_b_browser.py -k 'evidence or grouping or score' -q`

Commit: `feat: redesign evidence explorer and chart grouping`

### Task 4: B4 Complaint Presentation Metadata and B5 Lookback Removal

**Files:**
- Create: `tests/test_plan_b_presentation.py`
- Modify: `src/gm_insights.py`
- Modify: `src/charts.py`
- Modify: `app.py`
- Modify: `web/js/views/collect.js`
- Modify: `web/js/views/settings.js`
- Modify: `src/app_config.py`
- Modify: `config.json`
- Modify: `README.md`

- [ ] **Step 1: Write failing complaint presentation tests**

```python
def test_complaint_presentation_excludes_primary_and_secondary_sentinels():
    result = complaint_chart_presentation(frame)
    assert {row["theme"] for row in result["items"]}.isdisjoint({"", "not_applicable", "none", "unknown"})
    assert result["counts"] == {"total_complaints": 4, "applicable": 2, "excluded": 2}
    assert "not_applicable" in complaint_summary(frame).theme.tolist()  # raw unchanged
```

- [ ] **Step 2: Verify RED, then implement the chart-only helper**

Return complaint chart data as items and expose counts through `chart_meta.complaints` while keeping the chart's data array compatible.

- [ ] **Step 3: Write failing source tests for `since_days`**

Assert the Collect and Settings modules no longer contain `sinceDays` or emit `since_days`, while `app.py` retains documented dormant CLI compatibility.

- [ ] **Step 4: Remove browser/config lookback fields**

Delete the controls and request/config serialization, remove the default from `config.json`, and document that only legacy direct backend callers can still pass the dormant field until a later API cleanup.

- [ ] **Step 5: Run focused tests and commit**

Run: `/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_plan_b_presentation.py tests/test_m3.py tests/test_m5.py -k 'not browser' -q`

Commit: `feat: clarify complaint charts and collection controls`

### Task 5: B6 Typed Subreddit-List Store, CRUD, Snapshots, and Editor

**Files:**
- Create: `src/subreddit_lists.py`
- Create: `config/subreddit_lists/.gitkeep`
- Create: `tests/test_plan_b_subreddit_lists.py`
- Modify: `app.py`
- Modify: `web/js/views/collect.js`
- Modify: `web/styles.css`
- Modify: `README.md`

- [ ] **Step 1: Write failing store tests**

```python
def test_create_normalizes_and_deduplicates_subreddits(store):
    record = store.create("Launch list", "custom", ["r/Silverado", "silverado", " GMC "])
    assert record["subreddits"] == ["Silverado", "GMC"]
    assert "/" not in record["id"]

def test_update_requires_current_version(store):
    record = store.create("A", "custom", ["silverado"])
    with pytest.raises(ListConflictError):
        store.update(record["id"], version=record["version"] - 1,
                     display_name="A revised", list_type="custom", subreddits=["gmc"])
```

Cover traversal-like names, filename collisions, duplicate display names, empty/comment-only definitions, invalid types, case-insensitive duplicates, atomic replacement, seeding, and edit-during-collection snapshots.

- [ ] **Step 2: Run store tests and verify RED**

Expected: missing `src.subreddit_lists`.

- [ ] **Step 3: Implement store and migration**

Use generated IDs such as `list_<uuid>`, one JSON file per record, an index-free directory scan, normalized subreddit lines, and atomic `os.replace()`. Seed stable `gm-default` and `competitor-default` records from configured legacy files on first store initialization.

- [ ] **Step 4: Add CRUD and collection integration tests**

Test `GET /api/subreddit-lists`, `GET /api/subreddit-lists/{id}`, `POST`, and versioned `PUT`. Test `CollectRequest.subreddit_list_id`, immutable snapshot path, command argument, and `/api/health` collector path.

- [ ] **Step 5: Implement API and snapshot integration**

Snapshot to `runtime/<tag>/collect/<job_id>/config/subreddit_list.json` and write a `.txt` sibling passed to the collector. Never pass the mutable store file itself.

- [ ] **Step 6: Implement Collect editor and verify conflicts**

Provide list selection, view/edit, create, save, and 409 conflict copy that preserves the user's text and offers a reload.

- [ ] **Step 7: Run focused tests and commit**

Run: `/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_plan_b_subreddit_lists.py -q`

Commit: `feat: add named subreddit list store`

### Task 6: B7 Cluster Prompt Config, Validation, Reset, and Run Snapshot

**Files:**
- Create: `tests/test_plan_b_prompt.py`
- Modify: `src/app_config.py`
- Modify: `src/trend_insights.py`
- Modify: `scripts/analyze_run.py`
- Modify: `app.py`
- Modify: `web/js/views/settings.js`
- Modify: `config.json`

- [ ] **Step 1: Write failing prompt and atomic-write tests**

```python
def test_validate_cluster_prompt_requires_all_placeholders():
    result = validate_cluster_prompt("Analyze {cluster_size}; return {}")
    assert result["valid"] is False
    assert "centroid_reps" in result["missing_placeholders"]

def test_effective_prompt_falls_back_when_saved_value_is_invalid(monkeypatch):
    assert get_cluster_label_prompt({"prompts": {"cluster_label": "broken"}}) == DEFAULT_CLUSTER_LABEL_PROMPT
```

Test malformed JSON example, missing output keys, reset, sibling-temp atomic replacement, and secret atomic replacement.

- [ ] **Step 2: Verify RED, then implement config helpers**

Add `DEFAULT_CLUSTER_LABEL_PROMPT`, required placeholder/output key constants, `validate_cluster_prompt`, `get_cluster_label_prompt`, `reset_cluster_label_prompt`, and a reusable `_atomic_json_write`/`_atomic_text_write`.

- [ ] **Step 3: Add validation/reset API tests and implementation**

Add `POST /api/config/cluster-prompt/validate` and `POST /api/config/cluster-prompt/reset`. Reject invalid prompt saves with HTTP 422 before writing.

- [ ] **Step 4: Add analysis snapshot tests and implementation**

Before trends, write `runs/<run_id>/config/cluster_prompt.json` containing prompt, SHA-256, source, and validation. Include the hash in run/attempt metadata and pass the effective prompt path to the trend worker.

- [ ] **Step 5: Implement Settings preview/reset UI**

Preview must render missing placeholders/output keys without saving. Reset fetches the server default and updates the textarea before the user saves.

- [ ] **Step 6: Run focused tests and commit**

Run: `/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_plan_b_prompt.py tests/test_m15_coordinator.py tests/test_phase4.py -q`

Commit: `feat: make cluster prompts configurable and attributable`

### Task 7: B8 Generation-Aware Q&A Status and Recovery

**Files:**
- Create: `tests/test_plan_b_qa.py`
- Modify: `src/qa_retrieval.py`
- Modify: `scripts/faiss_qa_job.py`
- Modify: `scripts/analyze_run.py`
- Modify: `app.py`
- Modify: `web/js/views/qa.js`
- Modify: `web/js/views/dashboard.js`
- Modify: `web/js/nav.js`
- Modify: `web/js/views/explore.js`

- [ ] **Step 1: Write failing fingerprint and status tests**

```python
@pytest.mark.parametrize("setup,expected", [
    ("matching", "ready"), ("running", "building"), ("none", "missing"),
    ("failed", "failed"), ("old_fingerprint", "stale"), ("no_key", "blocked_no_api_key"),
])
def test_qa_status_state_matrix(setup, expected, runtime):
    assert qa_status_payload(runtime, "tag", api_key_available=setup != "no_key")["state"] == expected
```

Cover imported matching artifacts, active pipeline analysis, failed retry, and current-generation run/generation IDs.

- [ ] **Step 2: Verify RED, then implement provenance**

Use streaming SHA-256 over classified bytes. Write `input_fingerprint`, `generation_id`, and `run_id` to Q&A metadata. Derive generation ID from `current.resolve().name` and run ID from `.run_id` when not passed explicitly.

- [ ] **Step 3: Implement stateful `/api/qa/status` and stale guards**

Return one canonical status payload. Search/answer reject `missing`, `stale`, and `building` with HTTP 409 and structured recovery details.

- [ ] **Step 4: Move Q&A to Dashboard and implement recovery UI**

Healthy path hides Build/Refresh. Missing/failed/stale shows Build, Retry, or Rebuild as appropriate. No-key state links the user to Settings. Keep imported matching artifacts usable.

- [ ] **Step 5: Export polling cleanup and bind navigation**

```javascript
export function stopQaPolling() {
  if (state.qaPollTimer) window.clearInterval(state.qaPollTimer);
  state.qaPollTimer = null;
}
```

Call it before every view change and before starting a new timer. Render Dashboard, not Explorer, during Q&A status updates.

- [ ] **Step 6: Run focused tests and commit**

Run: `/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_plan_b_qa.py tests/test_phase6.py tests/test_plan_b_browser.py -k 'qa or polling' -q`

Commit: `feat: make Q&A generation-aware and recoverable`

### Task 8: B9 Canonical Trend Report to Markdown and PDF

**Files:**
- Create: `src/trend_report.py`
- Create: `tests/test_plan_b_reports.py`
- Modify: `src/pdf_export.py`
- Modify: `scripts/trend_briefing_job.py`
- Modify: `scripts/analyze_run.py`
- Modify: `app.py`
- Modify: `web/js/views/dashboard.js`
- Modify: `web/js/views/trends.js`

- [ ] **Step 1: Write failing model and renderer tests**

```python
def test_markdown_and_pdf_use_same_model(tmp_path):
    model = TrendReportModel.from_artifacts(labels, examples, signals, generation_id="g1", run_id="r1")
    markdown = render_trend_markdown(model)
    pdf = render_trend_pdf(model, tmp_path / "brief.pdf")
    assert model.clusters[0].short_label in markdown
    assert pdf.exists()
```

Test no timestamps, Unicode, empty optional artifacts, generation provenance, atomic writes, and deterministic ordering.

- [ ] **Step 2: Verify RED, then implement canonical model and Markdown**

Use frozen dataclasses for report and cluster records. Markdown includes provenance, coverage/confidence notes, summary table, and per-cluster signals from the same fields consumed by PDF.

- [ ] **Step 3: Refactor PDF builder to accept the model**

Keep a compatibility wrapper for existing callers, but the job constructs one model and passes it to both renderers.

- [ ] **Step 4: Implement partial-failure job semantics**

Write Markdown atomically first. If PDF fails, set `completed_with_warnings`, preserve Markdown, include `artifact_paths`, `failed_artifacts`, and warning text, and exit successfully enough for pipeline publication.

- [ ] **Step 5: Add generation-scoped download endpoints and UI**

Add `/api/download/trend-md`; update PDF to resolve from `output_dir(tag)/downloads`; display each format independently.

- [ ] **Step 6: Run focused tests and commit**

Run: `/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_plan_b_reports.py tests/test_phase5.py tests/test_m15_coordinator.py -q`

Commit: `feat: publish canonical trend reports as markdown and PDF`

### Task 9: B10 Full Verification and Documentation

**Files:**
- Modify: `tests/test_plan_b_browser.py`
- Modify: `README.md`
- Modify: `task_plan.md`
- Modify: `progress.md`
- Modify: `findings.md`

- [ ] **Step 1: Run all Plan B backend tests**

Run: `/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_plan_b_timeseries.py tests/test_plan_b_evidence.py tests/test_plan_b_presentation.py tests/test_plan_b_subreddit_lists.py tests/test_plan_b_prompt.py tests/test_plan_b_qa.py tests/test_plan_b_reports.py -q`

Expected: all pass with zero failures.

- [ ] **Step 2: Run browser tests with localhost access**

Run: `/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_plan_b_browser.py tests/test_m5.py tests/test_redesign_foundation.py -q`

Expected: all available Chromium tests pass; unavailable browser binaries may skip explicitly.

- [ ] **Step 3: Run the full suite**

Run: `/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest -q`

Expected: zero failures or errors.

- [ ] **Step 4: Run isolated end-to-end runtime proof**

Create a temporary runtime root containing synthetic daily, weekly, sparse, malformed, and mixed-timezone fixtures. Launch Uvicorn with `REDDITGM_RUNTIME_ROOT=<temp>`, verify `/api/health`, `/`, static assets, time-series reason codes, evidence pagination, list CRUD, prompt validation, Q&A status, and report downloads. Confirm the repository's real runtime row counts and mtimes are unchanged.

- [ ] **Step 5: Perform live browser verification**

Open the isolated app and verify Dashboard sections, truthful chart explanations, Q&A recovery, Explorer 10/page evidence cards and keyboard expansion, Collect list editor, Settings prompt preview/reset, and independent Markdown/PDF report links. Capture console errors and require none.

- [ ] **Step 6: Audit scope and diff**

Run `git diff --check`, inspect `git status --short -uall`, search for `/api/dashboard`, and confirm no Plan A implementation entered the branch. Re-read every B0-B10 requirement and map it to a passing test or runtime observation.

- [ ] **Step 7: Commit verification artifacts**

Commit: `test: complete standalone Plan B verification`
