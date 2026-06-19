# M5 Polish and Test Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish M5 with accessible, restrained UI motion and contract-level regression coverage for the merged pipeline backend.

**Architecture:** Keep the no-build vanilla JavaScript app and merged SQLite/subprocess coordinator intact. Add boundary-focused endpoint tests, centralize pipeline resource validation in small app helpers, add semantic tab and chart fallback behavior in existing modules, and verify through pytest plus a real browser.

**Tech Stack:** FastAPI, stdlib SQLite, pytest, Playwright, vanilla ES modules, CSS, Apache ECharts.

---

### Task 1: Pipeline API boundary hardening

**Files:**
- Create: `tests/test_m5.py`
- Modify: `app.py`
- Modify: `web/js/views/gathering.js`

- [x] Add failing endpoint tests that require the frozen top-level 409 payload, reject cross-tag status/log/artifact/retry access, reject logs outside `runtime/<tag>/runs/<run_id>/`, reject negative artifact indices, and successfully serve a valid immutable artifact.
- [x] Run `python3 -m pytest tests/test_m5.py -q` and confirm those tests fail for the expected contract/security reasons.
- [x] Add focused helpers in `app.py` for top-level `run_active` responses, tag/run ownership validation, and snapshot-contained paths; use them in pipeline endpoints.
- [x] Keep artifact URLs opaque to filesystem paths and make the Gathering 409 parser accept the frozen payload.
- [x] Re-run `python3 -m pytest tests/test_m5.py tests/test_m2_backend.py -q` and confirm the endpoint tests pass without regressing M2.

### Task 2: Manifest and orchestrator regression expansion

**Files:**
- Modify: `tests/test_m5.py`
- Modify only if a test exposes a defect: `scripts/analyze_run.py`, `src/run_store.py`

- [x] Add failing or characterization tests for canonical manifest step order/fields, no secret or filesystem-path leakage, latest-attempt artifact selection, retry attempt preservation, and cancellation projection for running steps.
- [x] Run the focused tests and record whether each new test is a true RED regression or a passing characterization of merged behavior.
- [x] Apply only minimal coordinator/store fixes for genuine failing guarantees; retain immutable attempts and hardcoded dependency semantics.
- [x] Re-run `python3 -m pytest tests/test_m5.py tests/test_m15_coordinator.py tests/test_m15_run_store.py tests/test_m2_backend.py -q`.

### Task 3: Semantic navigation, focus, and forms

**Files:**
- Modify: `tests/test_m5.py`
- Modify: `web/index.html`
- Modify: `web/js/nav.js`
- Modify: `web/js/views/settings.js`
- Modify: `web/js/views/gathering.js`
- Modify: `web/js/views/explore.js`
- Modify: `web/styles.css`

- [x] Add browser/static tests for tablist/tab/tabpanel semantics, roving `tabindex`, `aria-selected`, Arrow/Home/End keyboard activation, visible `:focus-visible`, associated filter labels, and meaningful active-view form names/autocomplete.
- [x] Run the focused tests and confirm RED failures.
- [x] Implement the WAI-ARIA tab interaction pattern in `nav.js`, associated labels/names in active forms, and robust focus-visible styling.
- [x] Replace ASCII loading ellipses in active M5 surfaces with `…` and give Settings save an announced confirmation.
- [x] Re-run the focused tests at desktop and mobile viewports.

### Task 4: Accessible chart equivalents and system-status cues

**Files:**
- Modify: `tests/test_m5.py`
- Modify: `web/js/components.js`
- Modify: `web/js/charts.js`
- Modify: `web/js/views/dashboard.js`
- Modify: `web/js/views/pipeline.js`
- Modify: `web/styles.css`

- [x] Add browser tests requiring accessible names on every chart, ECharts ARIA enabled, user-openable data-table equivalents for the chart catalog and custom dashboard charts, decorative sparklines hidden from assistive tech, and icon-plus-text pipeline state badges.
- [x] Run the focused tests and confirm RED failures.
- [x] Add reusable accessible-table rendering for array and heatmap data, wire it into generic and custom charts, and keep tables compact but equivalent to displayed chart rows.
- [x] Move pipeline styles into `web/styles.css`, use existing system-status tokens, and render explicit `aria-hidden` status icons beside readable state labels.
- [x] Re-run chart contract and browser tests.

### Task 5: Motion polish and warning cleanup

**Files:**
- Modify: `tests/test_m5.py`
- Modify: `web/js/nav.js`
- Modify: `web/styles.css`
- Modify: `tests/test_phase5.py`
- Modify: `src/trend_insights.py`

- [x] Add tests for a short transform/opacity view transition, no ECharts/view animation under reduced motion, no motion-producing hover transforms under reduced motion, and warning-free mixed timestamp parsing.
- [x] Confirm the new tests fail for missing behavior or the current pandas warning.
- [x] Add an interruptible 180-220 ms view transition that checks `prefers-reduced-motion`, strengthen the reduced-motion CSS override, and parse mixed timestamps explicitly.
- [x] Re-run the motion tests, `tests/test_phase5.py`, and browser smoke suite.

### Task 6: Full browser and suite verification

**Files:**
- Modify: `task_plan.md`
- Modify: `findings.md`
- Modify: `progress.md`

- [x] Start the local app and verify `/api/health` returns HTTP 200.
- [x] Use the in-app browser to inspect all five tabs, keyboard navigation, focus states, data tables, Pipeline states/log controls, and 1440/768/375 layouts with no console errors or overflow.
- [x] Run `python3 -m pytest -q` and require zero failures; record warnings separately.
- [x] Run targeted static searches for `transition: all`, inaccessible active controls, and stale M5 TODOs.
- [x] Review `git diff --check`, `git status --short -uall`, and the final diff against the attached M5 contract.
