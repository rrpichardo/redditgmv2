# Plan B Standalone Project Task Plan

## Goal

Implement the complete standalone Plan B against commit `c7a02de88160eda8f3ed199abe2b4aa86df5e548`, with no Plan A dependencies and with isolated-data verification.

## Current Phase

1. [complete] Reconcile the attached Plan B with the actual checkout and preserve an implementation baseline.
2. [complete] Write and self-review the approved design specification.
3. [complete] Write the test-first implementation plan.
4. [complete] Implement B0-B9 in bounded TDD slices.
5. [complete] Run B10 backend, browser, full-suite, and isolated-runtime verification.

## Workstreams

- B0 date/trend truthfulness and empty states.
- B1 Explorer labels and Reddit score semantics.
- B2 evidence normalization, paginated API, and UI.
- B3 predefined-vs-discovered grouping.
- B4 complaint chart presentation filtering and counts.
- B5 remove `since_days` from UI and requests.
- B6 subreddit-list store, CRUD, collection snapshots, and editor.
- B7 cluster prompt configuration, validation, atomic saves, and run snapshots.
- B8 generation-aware Q&A status, recovery UX, and polling cleanup.
- B9 canonical trend-report model with Markdown and PDF outputs.

## Decisions

- The user's attached Plan B is the approved product design; do not reopen settled scope unless code proves a contradiction.
- This worktree is detached at the exact target commit and is clean; create a `codex/...` branch before implementation commits.
- Treat the pipeline-hardening planning files found here as stale context from another worktree, not part of this task.
- Do not build `/api/dashboard`, the Plan A card contract, or any Plan A evaluator.
- Never mutate the real runtime dataset during verification.

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| Worktree-detection probe stopped when the no-superproject check returned status 1 | 1 | Re-run each probe independently so the expected empty superproject result does not short-circuit later checks. |
| This linked worktree has no local `.venv` | 1 | Use the verified shared project environment at `/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python` after confirming required imports. |
| Sandboxed full-suite baseline could not bind `127.0.0.1` for 20 browser/API tests | 1 | Backend baseline still reached 284 passes; requested an unsandboxed rerun for localhost binding. |
| Unsandboxed baseline rerun did not yield output and was terminated after several minutes | 2 | Continue with the proven 284-test backend baseline; run targeted browser tests and the full suite again during B10 with explicit localhost permission. |
| Combined diff/add/commit command could not create the linked-worktree index lock | 1 | Run verification, staging, and commit as separate approved Git commands. |
| B6 focused regression run exposed empty-frame `KeyError: complaint` in the new chart metadata helper | 1 | Add an explicit zero-count empty-frame contract before rerunning the same focused slice. |
| B6 browser run found `SyntaxError: Unexpected identifier 'r'` | 1 | Remove unescaped backticks around `r/` inside the Gathering template literal and rerun syntax/browser checks. |
| B9 compatibility run patched the retired PDF-only renderer and expected one artifact | 1 | Route the test through the canonical model renderer and assert independent Markdown and PDF artifacts. |
| Q&A worker fixture mocked CSV loading without creating bytes for the new input fingerprint | 1 | Create a minimal classified CSV in the fixture so the provenance contract is exercised honestly. |
| Initial isolated-runtime static probe requested `/web/js/app.js` | 1 | Correct the verification URL to the mounted `/static/js/app.js`; the corrected probe returned HTTP 200. |
