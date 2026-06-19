# Pipeline Hardening Design

## Objective

Make the existing Reddit analysis pipeline reliable from launch through PDF and Q&A artifacts, while preserving its current coordinator, SQLite ledger, subprocess workers, and browser UI.

## Scope

This change covers the failures observed in the live `gm_vehicle_on_demand` run and the immediately adjacent lifecycle paths:

1. Eliminate the new-run status race that briefly returns `404 Run not found`.
2. Clear stale UI notices after status polling recovers.
3. Prevent or clearly explain overlapping retry requests.
4. Generate trend PDFs containing Unicode punctuation and symbols.
5. Avoid requesting more KMeans clusters than distinct embedding vectors.
6. Add runtime-focused regression tests and verify the fixes against the existing live dataset.

The design does not replace the coordinator, change artifact schemas, change the LLM prompts, or rerun successful paid LLM steps unnecessarily.

## Architecture

### Durable run registration

`POST /api/analyze` will create the run row and pending step rows synchronously before spawning `scripts/analyze_run.py`. The coordinator will continue to acquire the single-writer tag lock and will adopt the pre-created run instead of assuming that it always owns run creation. If spawning fails, the API will mark the ledger run failed so clients never receive an unresolvable run ID.

This preserves the current subprocess boundary while making the API response contract truthful: once a run ID is returned, `/api/pipeline/status` can resolve it immediately.

### Browser status recovery and retry behavior

The pipeline view will treat a successful status response as recovery. It will clear a prior run-status error, refresh the matching run summary in the dropdown, and keep polling from the authoritative status response.

Retry buttons will be disabled for the request lifecycle. A `409 run_active` response will identify the active run instead of displaying only `Request failed with 409`; the UI will select that run when it is present in the run list and resume polling. This retains the backend single-writer lock and improves the client behavior around it.

### Unicode PDF rendering

The PDF exporter will register DejaVu Sans regular, bold, and italic font files located through Matplotlib's font manager, then use that family throughout the trend briefing. DejaVu Sans supports the em dash, check mark, and user-generated Unicode text already emitted by the report.

Font setup will live in one helper so future PDF builders can adopt it without duplicating path lookup and registration. Failure to locate the fonts will raise a direct dependency/configuration error before page rendering begins.

### Distinct-vector cluster cap

KMeans will calculate the number of distinct embedding rows and cap the requested cluster count to that value as well as the existing row-count limits. One distinct vector will produce one cluster; two distinct vectors will produce at most two clusters. Metadata and progress totals will report the actual cluster count.

This removes `ConvergenceWarning` for duplicate embeddings and avoids presenting empty or duplicate cluster themes as meaningful findings.

## Error handling

- A worker-spawn failure leaves a visible failed run instead of an orphaned ID.
- A recovered status poll clears only pipeline status errors; unrelated notices remain under their existing lifecycle.
- A retry conflict preserves the active run and directs the user to it.
- Missing Unicode fonts fail with a precise message before partial PDF output is published.
- Degenerate embeddings produce one valid cluster instead of forcing an invalid minimum of two.

## Testing

Regression tests will be written before production changes and must fail for the observed reasons.

- API test: a returned analyze run ID is immediately available from the status endpoint.
- API test: spawn failure marks the synchronously registered run failed.
- Browser test: a failed status poll followed by success clears the stale notice.
- Browser test: retry conflict exposes/selects the active run and does not launch a second retry.
- PDF test: trend briefing renders em dashes, check marks, and Unicode cluster labels.
- Clustering test: duplicate vectors cap KMeans at the number of distinct vectors, including the one-vector case.
- Existing focused suites and the full pytest suite remain green.

## Live rollout and verification

Implementation will be completed and verified in the Codex worktree. After the implementation commit passes verification, that commit will be cherry-picked onto the live `claude/angry-volhard-f4b947` branch, whose existing runtime data and untracked launch configuration will be preserved. The live server will then restart from that branch. Verification will use the existing completed classification, briefing, trend, and Q&A artifacts and retry only the failed trend-PDF step. The browser must show a completed PDF step, a downloadable PDF artifact, no stale run-not-found notice, and no active tag lock.
