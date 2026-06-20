# Pipeline Hardening Task Plan

## Goal

Fix and verify the end-to-end pipeline failures observed on 2026-06-19 without rewriting the coordinator.

## Phases

1. [complete] Approve and commit the design specification.
2. [complete] Write a test-first implementation plan.
3. [in_progress] Add failing regression tests.
4. [pending] Implement the minimal fixes.
5. [pending] Run targeted, full-suite, runtime, and browser verification.

## Decisions

- Use `/Users/ricopichardo/.codex/worktrees/f3f8/redditgmv2` on `codex/pipeline-hardening` as the source of truth.
- Preserve the live runtime data currently under the `angry-volhard-f4b947` worktree.
- Fix the observed path plus adjacent lifecycle behavior; do not rewrite the coordinator.

## Errors Encountered

| Error | Status | Intended resolution |
|---|---|---|
| First status poll can return run-not-found | Confirmed | Create the run ledger entry before spawning the coordinator. |
| Successful polling leaves a stale error notice | Confirmed | Clear recovered notices and refresh run summaries. |
| Overlapping retries surface generic 409 | Confirmed | Lock retry UI immediately and show the active run clearly. |
| Helvetica rejects Unicode in trend PDF | Confirmed | Embed DejaVu Sans variants through fpdf2. |
| KMeans requests more clusters than distinct vectors | Confirmed | Cap cluster count by distinct embedding rows. |
| Baseline test command could not import pytest | Setup | Install pytest in the shared project virtual environment, then rerun baseline. |
