# Pipeline Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make pipeline launch, retry, clustering, and trend-PDF generation reliable while preserving the current coordinator and artifact contracts.

**Architecture:** API endpoints synchronously reserve a pending run and tag lock in SQLite, then launch a coordinator that strictly adopts that reservation. The browser recovers from transient status errors and interprets the existing retry-conflict payload. Clustering derives its effective K from distinct vectors, and PDFs use embedded DejaVu Sans fonts supplied by Matplotlib.

**Tech Stack:** Python 3.11, FastAPI, SQLite, subprocess workers, NumPy, scikit-learn, fpdf2, Matplotlib font manager, vanilla JavaScript, pytest, pytest-playwright.

---

## File map

- `src/run_store.py`: atomic new-run/retry reservations, strict coordinator adoption, pending-run reconciliation.
- `src/jobs.py`: caller-supplied job identity so API lock owner and worker job ID are identical.
- `scripts/analyze_run.py`: strict reservation adoption on API-launched fresh/retry coordinators.
- `app.py`: runtime-root resolution, synchronous reservation/spawn handoff, reconciliation at read/write boundaries.
- `web/js/views/pipeline.js`: status recovery, run-summary refresh, global retry busy state, structured 409 handling.
- `src/trend_insights.py`: distinct-vector cluster cap and one-vector support.
- `src/pdf_export.py`: DejaVu Sans registration and use in trend briefing PDFs.
- `tests/test_m15_run_store.py`: reservation/adoption/reconciliation contracts.
- `tests/test_m15_coordinator.py`: strict coordinator adoption behavior.
- `tests/test_m2_backend.py`: immediate status, concurrency, spawn failure, and retry reservation API behavior.
- `tests/test_m5.py`: browser recovery and retry-conflict behavior.
- `tests/test_phase4.py`: duplicate-vector and one-vector clustering behavior.
- `tests/test_phase5.py`: Unicode trend-PDF generation.

### Task 1: Atomic run reservation and strict coordinator adoption

**Files:**
- Modify: `src/run_store.py`
- Modify: `src/jobs.py`
- Modify: `scripts/analyze_run.py`
- Modify: `app.py`
- Test: `tests/test_m15_run_store.py`
- Test: `tests/test_m15_coordinator.py`
- Test: `tests/test_m2_backend.py`

- [ ] **Step 1: Write failing RunStore reservation tests**

Add tests that demonstrate the desired transaction contracts:

```python
def test_reserve_new_run_creates_pending_run_steps_and_lock(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.db")
    now = time.time()
    result = store.reserve_new_run(
        "gm", run_id="run-1", owner_id="job-1", pid=os.getpid(),
        config={"provider": "openrouter"}, step_names=["prepare", "classify"], now=now,
    )
    assert result.acquired is True
    assert store.get_run("run-1")["state"] == "pending"
    assert [row["state"] for row in store.get_steps("run-1")] == ["pending", "pending"]
    assert store.get_tag_lock("gm")["owner_id"] == "job-1"


def test_second_reservation_returns_active_run_without_orphan(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.db")
    now = time.time()
    first = store.reserve_new_run(
        "gm", run_id="run-1", owner_id="job-1", pid=os.getpid(),
        config={}, step_names=["prepare"], now=now,
    )
    second = store.reserve_new_run(
        "gm", run_id="run-2", owner_id="job-2", pid=os.getpid(),
        config={}, step_names=["prepare"], now=now + 1,
    )
    assert first.acquired is True
    assert second.acquired is False
    assert second.active_run_id == "run-1"
    assert store.get_run("run-2") is None


def test_adopt_reserved_run_requires_matching_pending_row_and_owner(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.db")
    now = time.time()
    store.reserve_new_run(
        "gm", run_id="run-1", owner_id="job-1", pid=os.getpid(),
        config={}, step_names=["prepare"], now=now,
    )
    assert store.adopt_reserved_run(
        "gm", run_id="run-1", owner_id="job-1", pid=1234, now=now + 1
    ) is True
    assert store.get_run("run-1")["state"] == "running"
    assert store.get_tag_lock("gm")["pid"] == 1234
    assert store.adopt_reserved_run(
        "gm", run_id="missing", owner_id="job-1", pid=1234, now=now + 2
    ) is False


def test_reconcile_dead_pending_run_marks_failed_and_releases_lock(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.db")
    now = time.time()
    store.reserve_new_run(
        "gm", run_id="run-1", owner_id="job-1", pid=999_999_999,
        config={}, step_names=["prepare"], now=now,
    )
    assert store.reconcile_pending_run("run-1", now=now + 1) is True
    assert store.get_run("run-1")["state"] == "failed"
    assert store.get_tag_lock("gm") is None
```

- [ ] **Step 2: Run the new RunStore tests and verify RED**

Run:

```bash
/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_m15_run_store.py -k 'reserve_new_run or adopt_reserved_run or reconcile_dead_pending' -q
```

Expected: failures because `pending`, `reserve_new_run`, `adopt_reserved_run`, and `reconcile_pending_run` do not exist.

- [ ] **Step 3: Implement RunStore reservation/adoption/reconciliation**

Add `pending` to `RUN_STATES`. Implement `reserve_new_run(...)` and `reserve_retry(...)` with `BEGIN IMMEDIATE`; each method must reconcile an existing lock, return `LockAcquireResult(False, ...)` without inserting anything when active, and commit the lock/run/step changes together. `reserve_retry(...)` must validate tag and terminal source state, set the run to `pending`, clear terminal fields, and reset only passed step names.

Implement strict adoption:

```python
def adopt_reserved_run(self, tag: str, *, run_id: str, owner_id: str, pid: int,
                       now: float | None = None) -> bool:
    timestamp = time.time() if now is None else float(now)
    self._conn.execute("BEGIN IMMEDIATE")
    try:
        run = self._conn.execute(
            "SELECT * FROM runs WHERE run_id=? AND tag=? AND state='pending'",
            (run_id, tag),
        ).fetchone()
        lock = self._conn.execute(
            "SELECT * FROM tag_locks WHERE tag=? AND run_id=? AND owner_id=?",
            (tag, run_id, owner_id),
        ).fetchone()
        if not run or not lock:
            self._conn.execute("COMMIT")
            return False
        self._conn.execute(
            "UPDATE runs SET state='running', coordinator_pid=?, heartbeat_at=?, ended_at=NULL, warning=NULL WHERE run_id=?",
            (pid, timestamp, run_id),
        )
        self._conn.execute(
            "UPDATE tag_locks SET pid=?, heartbeat_at=? WHERE tag=? AND owner_id=?",
            (pid, timestamp, tag, owner_id),
        )
        self._conn.execute("COMMIT")
        return True
    except Exception:
        self._conn.execute("ROLLBACK")
        raise
```

Implement `reconcile_pending_run(run_id, now=None)` by feeding the pending row's `coordinator_pid` and `heartbeat_at` into `src.jobs.reconcile`; when interrupted, atomically set the run to failed with an adoption-failure warning and remove the matching tag lock.

- [ ] **Step 4: Run RunStore tests and verify GREEN**

Run:

```bash
/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_m15_run_store.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Write failing job identity and coordinator adoption tests**

Add a `start_job` test proving an explicit `job_id="job-fixed"` is written to the status file and appears exactly once in the child command. Add coordinator tests using `strict_adoption=True`: a reserved row completes adoption, while missing and terminal rows return failed status without creating or reviving a run.

Key assertions:

```python
status = start_job(runtime, "gm", "analyze", script, [], job_id="job-fixed")
assert status["job_id"] == "job-fixed"
assert status["cmd"].count("--job_id") == 1

coordinator = AnalysisCoordinator(..., job_id="job-1", strict_adoption=True)
result = coordinator.run()
assert store.get_run("run-1")["state"] != "pending"

missing = AnalysisCoordinator(..., run_id="missing", job_id="job-1", strict_adoption=True)
assert missing.run()["state"] == "failed"
assert store.get_run("missing") is None
```

- [ ] **Step 6: Run coordinator/job tests and verify RED**

Run:

```bash
/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_m15_workers.py tests/test_m15_coordinator.py -k 'job_id or adopt or reservation' -q
```

Expected: failures because `start_job` cannot receive an explicit ID and the coordinator has no strict-adoption mode.

- [ ] **Step 7: Implement explicit job identity and strict adoption**

Change `start_job(..., job_id: str | None = None)` to use the supplied ID or generate one. Include the final `cmd` in its returned status for tests and diagnostics.

Add `strict_adoption: bool = False` to `AnalysisCoordinator`. At the beginning of `run()`, strict mode calls `store.adopt_reserved_run(...)` and raises a clear `RuntimeError` if it returns false. Strict mode never calls `create_run` or initializes steps. Keep the legacy direct-construction path for existing unit tests and standalone use. Add CLI `--strict_adoption`; the API will pass it for new and retry coordinators.

- [ ] **Step 8: Run coordinator/job tests and verify GREEN**

Run:

```bash
/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_m15_workers.py tests/test_m15_coordinator.py -q
```

Expected: all tests pass.

- [ ] **Step 9: Write failing API handoff tests**

Extend `tests/test_m2_backend.py`:

```python
def test_analyze_run_is_resolvable_before_response_returns():
    tag = f"immediate_{uuid.uuid4().hex[:8]}"
    with patch("app.start_job", return_value={"job_id": "job-1", "pid": 12345, "state": "running"}):
        response = client.post("/api/analyze", json={"tag": tag})
    run_id = response.json()["run_id"]
    assert client.get(f"/api/pipeline/status?tag={tag}&run_id={run_id}").status_code == 200


def test_analyze_second_request_gets_409_without_orphan_row():
    tag = f"concurrent_{uuid.uuid4().hex[:8]}"
    with patch("app.start_job", return_value={"job_id": "job-1", "pid": os.getpid(), "state": "running"}):
        first = client.post("/api/analyze", json={"tag": tag})
        second = client.post("/api/analyze", json={"tag": tag})
    assert first.status_code == 200
    assert second.status_code == 409
    with _store() as store:
        assert len(store.list_runs(tag)) == 1


def test_analyze_spawn_failure_marks_run_failed_and_releases_lock():
    tag = f"spawn_failure_{uuid.uuid4().hex[:8]}"
    with patch("app.start_job", side_effect=OSError("spawn failed")):
        response = client.post("/api/analyze", json={"tag": tag})
    assert response.status_code == 500
    with _store() as store:
        run = store.list_runs(tag)[0]
        assert run["state"] == "failed"
        assert store.get_tag_lock(tag) is None
```

Update retry tests to assert the run and affected steps are `pending` before the mocked child starts and that a second retry receives the existing top-level `active_run_id` payload.

- [ ] **Step 10: Run API tests and verify RED**

Run:

```bash
/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_m2_backend.py -q
```

Expected: immediate status remains 404 and concurrent calls can both return 200.

- [ ] **Step 11: Implement API reservation/spawn handoff**

In `app.py`, generate `run_id` and `owner_id`, call `reserve_new_run` before `start_job`, pass `job_id=owner_id` and `--strict_adoption`, then attach the child PID to the reservation. Remove the duplicate `--job_id` entry from `extra_args`. On spawn exception, mark the run failed and release its lock before raising HTTP 500.

Apply the same sequence to retry with `reserve_retry`. Call `reconcile_pending_run` from run status/list reads and before writer-lock checks so dead pre-adoption children are surfaced and released.

- [ ] **Step 12: Run API and coordinator suites and verify GREEN**

Run:

```bash
/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_m2_backend.py tests/test_m15_run_store.py tests/test_m15_coordinator.py tests/test_m15_workers.py -q
```

Expected: all tests pass.

- [ ] **Step 13: Commit Task 1**

```bash
git add src/run_store.py src/jobs.py scripts/analyze_run.py app.py tests/test_m15_run_store.py tests/test_m15_coordinator.py tests/test_m15_workers.py tests/test_m2_backend.py
git commit -m "fix: make pipeline launch reservation atomic"
```

### Task 2: Browser recovery and retry-conflict UX

**Files:**
- Modify: `web/js/views/pipeline.js`
- Test: `tests/test_m5.py`

- [ ] **Step 1: Write failing browser tests**

Add one route-driven test where the first status request returns 404 and the next returns a valid run. Assert the error notice appears after the first response and disappears after clicking Refresh. Add a retry test where the endpoint returns the existing top-level payload:

```json
{"error":"run_active","message":"Tag 'fixture' has an active run: run-2","active_run_id":"run-2"}
```

Assert all `.retry-btn` controls are disabled while the request is pending, `state.pipelineRunId`/the select move to `run-2`, and the notice names the active run rather than `Request failed with 409`.

- [ ] **Step 2: Run browser tests and verify RED**

Run:

```bash
/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_m5.py -k 'status_recovers or retry_conflict' -q
```

Expected: stale notice remains and retry conflict shows the generic message.

- [ ] **Step 3: Implement recovery and retry handling**

On successful `loadStatus`, call `setNotice()` and replace the matching cached run summary with authoritative `status.state`, `started_at`, and `ended_at`; rebuild/select the option without resetting the selected run.

In `retryStep`, disable every `.retry-btn` before fetch. Use raw `fetch` so the response body and status remain available. For 409 with `active_run_id`, reload runs, select the active run, display `A run is already active (run <id>).`, and resume status polling. Re-enable controls only when no active run was selected or the view re-renders.

- [ ] **Step 4: Run browser tests and verify GREEN**

Run:

```bash
/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_m5.py -k 'status_recovers or retry_conflict' -q
```

Expected: both tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add web/js/views/pipeline.js tests/test_m5.py
git commit -m "fix: recover pipeline status and retry conflicts"
```

### Task 3: Distinct-vector KMeans cap

**Files:**
- Modify: `src/trend_insights.py`
- Test: `tests/test_phase4.py`

- [ ] **Step 1: Write failing clustering tests**

```python
def test_n_clusters_capped_at_distinct_vectors(self):
    vecs = np.vstack([
        np.ones((6, 8), dtype=np.float32),
        -np.ones((6, 8), dtype=np.float32),
    ])
    with warnings.catch_warnings(record=True) as caught:
        labels = kmeans_cluster(vecs, n_clusters=10)
    assert len(set(labels.tolist())) == 2
    assert not any(isinstance(item.message, ConvergenceWarning) for item in caught)


def test_one_distinct_vector_uses_one_cluster(self):
    vecs = np.ones((5, 8), dtype=np.float32)
    labels = kmeans_cluster(vecs, n_clusters=10)
    assert set(labels.tolist()) == {0}
```

Add a `run_clustering` metadata assertion that `n_clusters` equals the distinct-vector cap.

- [ ] **Step 2: Run clustering tests and verify RED**

Run:

```bash
/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_phase4.py -k 'distinct_vectors or one_distinct' -q
```

Expected: a convergence warning is captured and metadata reports the requested cap rather than the realized cluster count.

- [ ] **Step 3: Implement the effective cluster-count helper**

```python
def effective_cluster_count(embeddings: np.ndarray, requested: int) -> int:
    if len(embeddings) == 0:
        raise ValueError("embeddings cannot be empty")
    distinct = int(np.unique(embeddings, axis=0).shape[0])
    return max(1, min(int(requested), len(embeddings), distinct))
```

Use this helper in `kmeans_cluster` and after embedding generation in `run_clustering`; metadata, progress, labels, and downstream artifact loops must use the effective value.

- [ ] **Step 4: Run Phase 4 tests and verify GREEN**

Run:

```bash
/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_phase4.py -q
```

Expected: all tests pass without `ConvergenceWarning`.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/trend_insights.py tests/test_phase4.py
git commit -m "fix: cap clusters by distinct embeddings"
```

### Task 4: Unicode trend-PDF rendering

**Files:**
- Modify: `src/pdf_export.py`
- Test: `tests/test_phase5.py`

- [ ] **Step 1: Write the failing Unicode PDF test**

Create minimal labels/examples/signals containing `—`, `✓`, and a Unicode label, call `build_trend_briefing_pdf`, and assert a non-empty `%PDF` file is produced:

```python
def test_trend_briefing_pdf_supports_unicode(tmp_path: Path) -> None:
    output = tmp_path / "trend.pdf"
    build_trend_briefing_pdf(
        labels={"0": {"short_label": "Battery — charging ✓", "detailed_label": "Résumé"}},
        examples={"0": {"examples": []}},
        signals={"signals": {"0": {"confidence_banner": "high", "velocity": {"direction": "rising"}}}},
        pdf_path=output,
    )
    assert output.read_bytes().startswith(b"%PDF")
```

- [ ] **Step 2: Run the PDF test and verify RED**

Run:

```bash
/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_phase5.py -k unicode -q
```

Expected: `FPDFUnicodeEncodingException` for the em dash.

- [ ] **Step 3: Implement DejaVu font registration**

Add a helper that resolves regular, bold, and italic files with `matplotlib.font_manager.findfont(FontProperties(...), fallback_to_default=False)`, verifies each path exists, registers them with `pdf.add_font("DejaVu", style, path)`, and returns family `DejaVu`. Use that family for every regular/bold/italic `set_font` call in `build_trend_briefing_pdf`. Keep the existing atomic temp-file replacement.

- [ ] **Step 4: Run Phase 5 tests and verify GREEN**

Run:

```bash
/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_phase5.py -q
```

Expected: all tests pass and the Unicode PDF test writes a valid PDF.

- [ ] **Step 5: Commit Task 4**

```bash
git add src/pdf_export.py tests/test_phase5.py
git commit -m "fix: render trend PDFs with Unicode fonts"
```

### Task 5: Runtime-root configuration and complete verification

**Files:**
- Modify: `app.py`
- Test: `tests/test_m5.py`

- [ ] **Step 1: Write the failing runtime-root resolution test**

Extract a pure resolver and test it directly:

```python
def test_runtime_root_env_override(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    override = tmp_path / "shared-runtime"
    assert app_module.resolve_runtime_root(root, {"REDDITGM_RUNTIME_ROOT": str(override)}) == override.resolve()
    assert app_module.resolve_runtime_root(root, {}) == (root / "runtime").resolve()
```

- [ ] **Step 2: Run the resolver test and verify RED**

Run:

```bash
/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_m5.py -k runtime_root_env_override -q
```

Expected: failure because `resolve_runtime_root` does not exist.

- [ ] **Step 3: Implement runtime-root resolution**

```python
def resolve_runtime_root(root: Path, env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    configured = values.get("REDDITGM_RUNTIME_ROOT", "").strip()
    return Path(configured).expanduser().resolve() if configured else (root / "runtime").resolve()

RUNTIME = resolve_runtime_root(ROOT)
```

- [ ] **Step 4: Run focused and full automated verification**

Run:

```bash
/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest tests/test_m2_backend.py tests/test_m15_run_store.py tests/test_m15_coordinator.py tests/test_m15_workers.py tests/test_phase4.py tests/test_phase5.py tests/test_m5.py -q
/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pytest -q
/Users/ricopichardo/Claude/redditgmv2/.venv/bin/python -m pip check
git diff --check
```

Expected: all tests pass, pip reports no broken requirements, and git reports no whitespace errors.

- [ ] **Step 5: Commit Task 5**

```bash
git add app.py tests/test_m5.py
git commit -m "chore: finalize pipeline hardening verification"
```

- [ ] **Step 6: Restart the live server temporarily from the verified feature branch**

Stop the server on port `52285`, then launch from the feature worktree:

```bash
REDDITGM_RUNTIME_ROOT=/Users/ricopichardo/Claude/redditgmv2/.claude/worktrees/angry-volhard-f4b947/runtime \
  /Users/ricopichardo/Claude/redditgmv2/.venv/bin/python \
  /Users/ricopichardo/.codex/worktrees/f3f8/redditgmv2/run.py --port 52285 --no-reload
```

Verify `/api/health` returns HTTP 200 and reports the explicit runtime path.

- [ ] **Step 7: Retry only the failed trend-PDF step and verify live artifacts**

For tag `gm_vehicle_on_demand`, run `c477e905fced4163aebdc1af89db2d10`, retry `trend_pdf` only. Verify:

- the run status endpoint resolves immediately;
- the step reaches `completed` and exposes a PDF artifact;
- the PDF begins with `%PDF` and renders without Unicode exceptions;
- the browser has no stale `Run not found` notice;
- `tag_locks` has no row after completion;
- classification, briefing, trends, and Q&A attempt counts do not increase.

- [ ] **Step 8: Finish the development branch**

Invoke `superpowers:finishing-a-development-branch`. Present the verified merge/PR/keep options. If the user chooses local merge, merge `codex/pipeline-hardening` into the clean `main` checkout and restart the live server from `main` with the same `REDDITGM_RUNTIME_ROOT`; do not copy or merge runtime databases.
