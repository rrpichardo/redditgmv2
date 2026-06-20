# Pipeline Hardening Progress

## 2026-06-19

- Diagnosed the dependency, authentication, run-status race, retry-lock, clustering, and Unicode PDF failures against live runtime artifacts.
- User selected Option 2: targeted end-to-end hardening.
- Created branch `codex/pipeline-hardening` from `c0ecddd`.
- Wrote and self-reviewed the approved design specification.
- Verified review feedback against the coordinator, lock store, retry client, branch graph, and both runtime ledgers.
- Revised the design with an atomic reservation/adoption protocol, orphan reconciliation, UI-only 409 handling, and a `main` rollout using an explicit live runtime root.
- Baseline test attempt stopped before collection because the shared `.venv` did not contain pytest; recorded as an environment setup issue.
- Installed pytest and pytest-playwright into the shared project `.venv`.
- Verified the untouched baseline: 286 tests passed with 3 pre-existing warnings in 44.63 seconds.
- Wrote the test-first implementation plan at `docs/superpowers/plans/2026-06-19-pipeline-hardening.md`.
- Task 1 RED: reservation, explicit job identity, strict adoption, immediate status, concurrency, and spawn-failure tests failed for the expected missing contracts.
- Task 1 GREEN: 58 focused tests passed after atomic reservation/adoption and API handoff changes.
