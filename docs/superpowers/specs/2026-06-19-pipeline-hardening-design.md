# Pipeline Hardening Design

## Objective

Make the existing Reddit analysis pipeline reliable from launch through PDF and Q&A artifacts, while preserving its current coordinator, SQLite ledger, subprocess workers, and browser UI.

## Scope

This change covers the failures observed in the live `gm_vehicle_on_demand` run and the immediately adjacent lifecycle paths:

1. Eliminate the new-run status race that briefly returns `404 Run not found`, including concurrent launch and orphan cleanup behavior.
2. Clear stale UI notices after status polling recovers.
3. Prevent or clearly explain overlapping retry requests.
4. Generate trend PDFs containing Unicode punctuation and symbols.
5. Avoid requesting more KMeans clusters than distinct embedding vectors.
6. Add runtime-focused regression tests and verify the fixes against the existing live dataset.
7. Allow the verified `main` checkout to use the existing live runtime through an explicit environment variable, without copying or merging SQLite databases.

The design does not replace the coordinator, change artifact schemas, change the LLM prompts, or rerun successful paid LLM steps unnecessarily.

## Architecture

### Durable run registration

`POST /api/analyze` will generate the run ID and coordinator job/owner ID, then use one `BEGIN IMMEDIATE` SQLite transaction to reserve the tag writer lock, create a `pending` run row, and create pending step rows. A concurrent request for the same tag will see the reserved lock and return the existing `409 run_active` response before it can create another run row. `POST /api/pipeline/retry` will validate that its source run is terminal, then use the same reservation protocol to transition that run back to `pending` while atomically resetting only the affected steps.

The API will pass the same owner ID to `start_job` and `scripts/analyze_run.py`. `start_job` will accept an explicit job ID so the CLI receives one unambiguous `--job_id` value. After `Popen` succeeds, the API records the child PID on both the run and reserved lock before returning the run ID.

On startup, the coordinator must find all three matching handoff records: the requested run row, the tag, and the lock owned by its job/owner ID. A fresh coordinator may adopt only a `pending` run; a retry coordinator may adopt only the existing run explicitly reserved by the retry endpoint. It updates the lock PID to its own PID and transitions the run to `running`. A missing row, mismatched tag/lock, or terminal run is a hard adoption failure: the coordinator reports failure through its job status and never creates a replacement row or revives a terminal run.

If `Popen` raises, the API marks the pending run failed and releases the reservation before returning an error. If the child starts but exits before adoption, run/list/status and the next writer request will reconcile the pending run against its recorded PID and heartbeat, mark it failed when the PID is dead or the startup heartbeat is stale, and release the lock. This provides cleanup without a separate daemon.

This preserves the current subprocess boundary while making the API response contract truthful: once a run ID is returned, `/api/pipeline/status` can resolve it immediately.

### Browser status recovery and retry behavior

The pipeline view will treat a successful status response as recovery. It will clear a prior run-status error, refresh the matching run summary in the dropdown, and keep polling from the authoritative status response.

The backend already returns `active_run_id` in its frozen `409 run_active` payload. The retry change is therefore UI-only at the response layer: all retry buttons will be disabled for the request lifecycle, and the client will parse the existing payload, select the active run when it is present in the run list, and resume polling instead of displaying only `Request failed with 409`. The synchronous backend reservation described above ensures two different retry buttons cannot both launch coordinators before either child acquires the lock.

### Runtime-root rollout configuration

`app.py` will resolve `RUNTIME` from `REDDITGM_RUNTIME_ROOT` when set and otherwise retain the current `<repo>/runtime` default. The path is local-only configuration and does not enter run metadata or browser responses beyond the existing health endpoint. This lets the `main` checkout serve the current live ledger and artifacts without copying databases between worktrees.

### Unicode PDF rendering

The PDF exporter will register DejaVu Sans regular, bold, and italic font files located through Matplotlib's font manager, then use that family throughout the trend briefing. DejaVu Sans supports the em dash, check mark, and user-generated Unicode text already emitted by the report.

Font setup will live in one helper so future PDF builders can adopt it without duplicating path lookup and registration. Failure to locate the fonts will raise a direct dependency/configuration error before page rendering begins.

### Distinct-vector cluster cap

KMeans will calculate the number of distinct embedding rows and cap the requested cluster count to that value as well as the existing row-count limits. One distinct vector will produce one cluster; two distinct vectors will produce at most two clusters. Metadata and progress totals will report the actual cluster count.

This removes `ConvergenceWarning` for duplicate embeddings and avoids presenting empty or duplicate cluster themes as meaningful findings.

## Error handling

- A worker-spawn failure leaves a visible failed run instead of an orphaned ID.
- A child that dies before adoption is reconciled to failed and cannot leave a permanent pending row or writer lock.
- Concurrent analyze/retry requests create at most one reserved run or retry writer for a tag.
- A recovered status poll clears only pipeline status errors; unrelated notices remain under their existing lifecycle.
- A retry conflict preserves the active run and directs the user to it.
- Missing Unicode fonts fail with a precise message before partial PDF output is published.
- Degenerate embeddings produce one valid cluster instead of forcing an invalid minimum of two.

## Testing

Regression tests will be written before production changes and must fail for the observed reasons.

- API test: a returned analyze run ID is immediately available from the status endpoint.
- API test: concurrent analyze requests create one pending run and return one `409`, with no orphan row.
- API test: spawn failure marks the synchronously registered run failed and releases its lock.
- Coordinator tests: a matching pending reservation is adopted; a missing, mismatched, or terminal reservation is rejected without creating/reviving a run.
- Reconciliation test: an immediately dead pre-adoption child becomes a failed run and releases its lock.
- Browser test: a failed status poll followed by success clears the stale notice.
- Browser test: the existing retry-conflict payload exposes/selects the active run and does not launch a second retry.
- PDF test: trend briefing renders em dashes, check marks, and Unicode cluster labels.
- Clustering test: duplicate vectors cap KMeans at the number of distinct vectors, including the one-vector case.
- Configuration test: `REDDITGM_RUNTIME_ROOT` overrides the default runtime location.
- Existing focused suites and the full pytest suite remain green.

## Live rollout and verification

Implementation will be completed and verified on `codex/pipeline-hardening`, then integrated into the current `main` branch. The live server will restart from `/Users/ricopichardo/Claude/redditgmv2` with `REDDITGM_RUNTIME_ROOT` set to the existing live runtime at `/Users/ricopichardo/Claude/redditgmv2/.claude/worktrees/angry-volhard-f4b947/runtime`. No SQLite or artifact directories will be copied or merged.

Live verification will use tag `gm_vehicle_on_demand` and run `c477e905fced4163aebdc1af89db2d10`. It will reuse the completed classification, briefing, trend, and Q&A artifacts and retry only the failed trend-PDF step. The browser must show a completed PDF step, a downloadable PDF artifact, no stale run-not-found notice, and no active tag lock.
