# UI Redesign Phase 0 — Foundation + Contract — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the 2,024-line `web/app.js` into focused ES modules, replace all hand-drawn charts with Apache ECharts (one light theme + dispose discipline), establish a light design-token system with dual color semantics, and freeze the integration contract — with browser tests proving the app still works.

**Architecture:** Native ES modules (no build step) served from `/static/js/`. ECharts vendored as a global (`window.echarts`) loaded via a plain `<script>` before the module entry. A single `charts.js` owns ECharts: a registered light theme, a `Map`-based instance registry with `dispose()` on every re-render, a pure `buildChartOption(spec, data)` mapper for the 6 existing chart types, and a `mountViewCharts(root, payload)` pass that renders ECharts into placeholder `<div data-chart="id">` nodes after each view's HTML is injected. The 7 current tabs and all backend endpoints are untouched.

**Tech Stack:** Vanilla JS ES modules, Apache ECharts (vendored), CSS custom properties, FastAPI (unchanged), pytest + Playwright (Python, sync API) for browser tests.

**Conventions for this plan:**
- This is largely a *refactor that moves existing code*. For moved functions the plan gives the exact source line range in `web/app.js` and the destination file — copy the body verbatim, then add `export`/`import` as specified. Full code is given for everything genuinely new or rewritten (charts.js, design tokens, tests, the chart-placeholder rewiring, bootstrap).
- Test tag is the throwaway `redesign_test_fixture` (under gitignored `runtime/`); never touch the real default tag.
- Commit after each task. Branch is `claude/epic-diffie-417c31` (already a worktree) — do not commit to `main`.

---

## File structure

```
web/
  index.html                 # MODIFY: add echarts vendor <script>; entry stays type=module
  vendor/echarts.min.js       # CREATE: vendored ECharts (no runtime CDN)
  styles.css                  # MODIFY: add :root design tokens + status classes
  js/
    state.js                  # CREATE: shared `state` + pure helpers ($, $$, fmt, pct, fileSize, esc, debounce, compactText, humanLabel, topItem, formatValue)
    api.js                    # CREATE: apiUrl(), request()
    charts.js                 # CREATE: ECharts theme + registry + buildChartOption + renderChartInto + mountViewCharts + dispose + resize
    components.js             # CREATE: setNotice, setBusy, panel, chartPanel(rewritten→placeholder), metricGrid, emptyState, jobStatusCard
    nav.js                    # CREATE: setView, render (view registry + mount pass), bindViewEvents, handleSaveKindClick
    app.js                    # CREATE: loadRun, updateDownloads, export-job controllers, saveExport, sourceDownloadsPanel, bindGlobalEvents, bootstrap
    views/
      dashboard.js            # CREATE: dashboard()
      collect.js              # CREATE: collectView + collect controllers/polling
      classify.js             # CREATE: classifyView + classify/upload controllers/polling
      explore.js              # CREATE: exploreView + filter helpers + evidence helpers
      briefing.js             # CREATE: briefingView + briefing()
      trends.js               # CREATE: trendsView + trend controllers/polling/badges
      qa.js                   # CREATE: qaView + qa controllers/polling
  app.js                      # DELETE at end of Task 5 (replaced by js/app.js)
docs/redesign/integration-contract.md   # CREATE (Task 6)
tests/
  conftest.py                 # CREATE: live_server + browser_page fixtures
  test_redesign_foundation.py # CREATE: characterization + contract + tokens + smoke tests
```

Cross-module imports follow this rule: each module imports the named symbols it uses from `state.js`, `api.js`, `components.js`, `nav.js`, `charts.js`. The `nav.js ↔ views/*` cycle is safe because the imported functions (`render`, `setView`) are only *called* at runtime, never during module initialization (native ESM resolves this).

---

## Task 1: Test harness + characterization safety net

Establishes the regression net BEFORE refactoring: a real server seeded with the golden fixture, plus HTTP-level assertions that pass against the current monolith and must keep passing.

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_redesign_foundation.py`

- [ ] **Step 1: Write `tests/conftest.py`**

```python
"""Fixtures for redesign browser/integration tests.

live_server: boots a real uvicorn against ROOT, seeded with the golden fixture
under a throwaway tag (runtime/ is gitignored). browser_page: a Playwright
Chromium page; skips (does not fail) when Playwright/Chromium is unavailable.
"""
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
TEST_TAG = "redesign_test_fixture"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Server:
    def __init__(self, url: str, tag: str):
        self.url = url
        self.tag = tag


@pytest.fixture(scope="session")
def live_server():
    # Seed classified data so /api/run returns chart payloads for the test tag
    dest = ROOT / "runtime" / TEST_TAG / "classified" / "classified_posts.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "golden_classified.csv", dest)

    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT),
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/", timeout=1)
                break
            except Exception:
                time.sleep(0.1)
        else:
            proc.terminate()
            pytest.fail("live_server did not become ready")
        yield _Server(base, TEST_TAG)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        shutil.rmtree(ROOT / "runtime" / TEST_TAG, ignore_errors=True)


@pytest.fixture
def browser_page():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        pytest.skip("playwright not installed")
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            pytest.skip("chromium not installed (run: playwright install chromium)")
        page = browser.new_page()
        try:
            yield page
        finally:
            browser.close()
```

- [ ] **Step 2: Write the characterization tests in `tests/test_redesign_foundation.py`**

```python
"""Phase 0 redesign foundation tests.

Separate from tests/test_phase0.py (the original project's correctness suite).
Characterization tests here guard behavior across the module-split refactor.
"""
import json
import urllib.request


def _get(url: str):
    return urllib.request.urlopen(url, timeout=10).read().decode()


def test_api_run_serves_chart_payload(live_server):
    raw = _get(f"{live_server.url}/api/run?tag={live_server.tag}")
    data = json.loads(raw)
    assert "chart_specs" in data, "run payload must expose chart_specs at top level"
    assert "chart_data" in data, "run payload must expose chart_data at top level"
    assert "sentiment" in data["chart_data"], "sentiment chart data expected from golden fixture"


def test_index_serves_seven_tabs(live_server):
    html = _get(f"{live_server.url}/")
    for view in ["dashboard", "collect", "classify", "explore", "briefing", "trends", "qa"]:
        assert f'data-view="{view}"' in html, f"tab {view} missing from index.html"
```

- [ ] **Step 3: Run the characterization tests — expect PASS against the current app**

Run: `cd /Users/ricopichardo/Claude/redditgmv2/.claude/worktrees/epic-diffie-417c31 && python -m pytest tests/test_redesign_foundation.py -v`
Expected: 2 passed (these capture current behavior; they must stay green through the refactor).

- [ ] **Step 4: Ensure Playwright Chromium is available (one-time)**

Run: `python -m playwright install chromium`
Expected: Chromium downloads (or "is already installed"). If this environment forbids the download, the `browser_page` fixture skips browser tests gracefully — that is acceptable, but try to install so the smoke tests actually run.

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_redesign_foundation.py
git commit -m "test(phase0): live-server + characterization safety net for UI refactor"
```

---

## Task 2: Vendor ECharts + build charts.js (theme, registry, mapping)

Creates the ECharts module in isolation and proves the spec→option mapping and render/dispose work — before any view depends on it.

**Files:**
- Create: `web/vendor/echarts.min.js`
- Create: `web/js/state.js` (needed by charts.js for formatting helpers)
- Create: `web/js/charts.js`
- Test: `tests/test_redesign_foundation.py` (append)

- [ ] **Step 1: Vendor ECharts**

Run:
```bash
mkdir -p web/vendor
curl -fsSL https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js -o web/vendor/echarts.min.js
```
Expected: `web/vendor/echarts.min.js` ~1 MB, first bytes are a minified UMD bundle. Verify: `head -c 40 web/vendor/echarts.min.js` shows `(function (global, factory)`.

- [ ] **Step 2: Create `web/js/state.js`** (pure helpers + shared state; copy bodies from `web/app.js` at the cited lines, add `export`)

```javascript
// state.js — shared mutable app state + pure helpers (no app-module imports).
export const state = {
  tag: "gm_vehicle_on_demand",
  view: "dashboard",
  data: null,
  report: "",
  filters: {},
  classifyJobStatus: null,
  exportJobStatus: null,
  collectPollTimer: null,
  classifyPollTimer: null,
  exportPollTimer: null,
  trendsData: null,
  trendJobStatus: null,
  trendBriefingJobStatus: null,
  trendPollTimer: null,
  trendBriefingPollTimer: null,
  qaJobStatus: null,
  qaHits: null,
  qaAnswer: null,
  qaPollTimer: null,
};

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
export const fmt = new Intl.NumberFormat("en-US");
export const pct = (value) => `${Number(value || 0).toFixed(1)}%`;
// fileSize, esc, debounce, compactText, humanLabel, topItem, formatValue:
// copy verbatim from web/app.js lines 30-41, 43-49, 93-110, 216-225 and add `export`.
```

Source line ranges to copy into state.js and prefix with `export`: `fileSize` (30-35), `esc` (36-41), `debounce` (43-49), `compactText` (93-97), `humanLabel` (98-101), `topItem` (102-110), `formatValue` (216-225).

- [ ] **Step 3: Create `web/js/charts.js`** (new code — full)

```javascript
// charts.js — ECharts rendering: light theme, instance registry (dispose on
// re-render), pure spec->option mapping for the 6 chart types, and a mount pass.
// ECharts is a global (window.echarts) loaded via <script> in index.html.
import { fmt, pct, humanLabel, debounce } from "./state.js";

const THEME_NAME = "redditgm";
let themeRegistered = false;

function ensureTheme() {
  if (themeRegistered) return;
  echarts.registerTheme(THEME_NAME, {
    color: ["#2563eb", "#0d9488", "#d97706", "#7c3aed", "#db2777", "#64748b"],
    backgroundColor: "transparent",
    textStyle: { fontFamily: "inherit", color: "#475569" },
    categoryAxis: { axisLine: { lineStyle: { color: "#cbd5e1" } }, axisTick: { show: false }, axisLabel: { color: "#475569" }, splitLine: { show: false } },
    valueAxis: { axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: "#475569" }, splitLine: { lineStyle: { color: "#eef2f6" } } },
    tooltip: { backgroundColor: "#0f172a", borderWidth: 0, textStyle: { color: "#f8fafc" } },
    legend: { textStyle: { color: "#475569" } },
  });
  themeRegistered = true;
}

const instances = new Map();
const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export function disposeChart(el) {
  const inst = instances.get(el);
  if (inst) { inst.dispose(); instances.delete(el); }
}

export function disposeAll() {
  for (const inst of instances.values()) inst.dispose();
  instances.clear();
}

let resizeBound = false;
export function bindResize() {
  if (resizeBound) return;
  window.addEventListener("resize", debounce(() => {
    for (const inst of instances.values()) inst.resize();
  }, 150));
  resizeBound = true;
}

function fmtValue(v, format) {
  const n = Number(v || 0);
  switch (format) {
    case "pct": return pct(n);
    case "score": return n.toFixed(1);
    case "correlation": return n.toFixed(2);
    default: return fmt.format(Math.round(n));
  }
}

function optBar(spec, rows) {
  const xf = spec.x_field, yf = spec.y_field || "count";
  const cm = spec.color_map || {};
  const slice = rows.slice(0, 20);
  const cats = slice.map((r) => humanLabel(r[xf]));
  const data = slice.map((r) => {
    const key = String(r[xf] ?? "");
    return { value: Number(r[yf] || 0), itemStyle: cm[key] ? { color: cm[key] } : undefined };
  });
  return {
    grid: { left: 8, right: 24, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: "value", axisLabel: { formatter: (v) => fmtValue(v, spec.value_format) } },
    yAxis: { type: "category", data: cats, inverse: true },
    tooltip: { trigger: "axis", valueFormatter: (v) => fmtValue(v, spec.value_format) },
    series: [{ type: "bar", data, barMaxWidth: 22 }],
  };
}

function optGroupedBar(spec, rows) {
  const xf = spec.x_field, keys = spec.series_keys || [];
  const slice = rows.slice(0, 8);
  const cats = slice.map((r) => humanLabel(r[xf]));
  const series = keys.map((k) => ({ name: humanLabel(k), type: "bar", data: slice.map((r) => Number(r[k] || 0)) }));
  return {
    legend: {}, grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
    xAxis: { type: "category", data: cats },
    yAxis: { type: "value", axisLabel: { formatter: (v) => fmtValue(v, spec.value_format) } },
    tooltip: { trigger: "axis", valueFormatter: (v) => fmtValue(v, spec.value_format) },
    series,
  };
}

function optStackedLong(spec, rows) {
  const xf = spec.x_field || "vehicle_mentioned";
  const sf = spec.series_field || "issue_severity";
  const vf = spec.value_field || "count";
  const order = spec.series_order || [];
  const cm = spec.color_map || {};
  const groups = {}; const found = new Set(order);
  for (const r of rows) {
    const x = String(r[xf] ?? ""), s = String(r[sf] ?? ""), v = Number(r[vf] || 0);
    (groups[x] = groups[x] || {})[s] = (groups[x][s] || 0) + v;
    found.add(s);
  }
  const names = [...order, ...[...found].filter((s) => !order.includes(s))];
  const cats = Object.keys(groups);
  const series = names.map((s) => ({
    name: humanLabel(s), type: "bar", stack: "total",
    itemStyle: cm[s] ? { color: cm[s] } : undefined,
    data: cats.map((x) => groups[x][s] || 0),
  }));
  return {
    legend: {}, grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
    xAxis: { type: "category", data: cats.map(humanLabel) },
    yAxis: { type: "value" },
    tooltip: { trigger: "axis" },
    series,
  };
}

function optStacked100(spec, rows) {
  const xf = spec.x_field || "vehicle";
  const order = spec.series_order || [];
  const cm = spec.color_map || {};
  const slice = rows.slice(0, 15);
  const keys = Object.keys(slice[0] || {}).filter((k) => k !== xf);
  const names = [...order.filter((s) => keys.includes(s)), ...keys.filter((s) => !order.includes(s))];
  const cats = slice.map((r) => humanLabel(r[xf]));
  const totals = slice.map((r) => names.reduce((a, s) => a + Number(r[s] || 0), 0) || 1);
  const series = names.map((s) => ({
    name: humanLabel(s), type: "bar", stack: "total",
    itemStyle: cm[s] ? { color: cm[s] } : undefined,
    data: slice.map((r, i) => Number(((Number(r[s] || 0) / totals[i]) * 100).toFixed(1))),
  }));
  return {
    legend: {}, grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
    xAxis: { type: "category", data: cats },
    yAxis: { type: "value", max: 100, axisLabel: { formatter: "{value}%" } },
    tooltip: { trigger: "axis", valueFormatter: (v) => pct(v) },
    series,
  };
}

function optScatter(spec, rows) {
  const xf = spec.x_field || "volume", yf = spec.y_field || "pct_negative";
  const lf = spec.label_field || "theme", cf = spec.color_field || "priority";
  const cm = spec.color_map || {};
  const slice = rows.slice(0, 40);
  const data = slice.map((r) => ({
    value: [Number(r[xf] || 0), Number(r[yf] || 0)],
    name: humanLabel(r[lf]),
    itemStyle: { color: cm[String(r[cf] ?? "")] || "#64748b" },
  }));
  return {
    grid: { left: 8, right: 24, top: 16, bottom: 36, containLabel: true },
    xAxis: { type: "value", name: humanLabel(xf), nameLocation: "middle", nameGap: 26 },
    yAxis: { type: "value", name: humanLabel(yf), nameLocation: "middle", nameGap: 40, axisLabel: { formatter: "{value}%" } },
    tooltip: { trigger: "item", formatter: (p) => `${p.data.name}<br/>${fmt.format(p.value[0])} / ${pct(p.value[1])}` },
    series: [{ type: "scatter", data, symbolSize: (d) => Math.max(10, Math.min(40, d[0] * 4)) }],
  };
}

function optHeatmap(spec, data) {
  if (data && data.rows !== undefined && data.columns !== undefined) {
    const { rows, columns, values } = data;
    const cells = []; let max = 1;
    rows.forEach((_, i) => columns.forEach((__, j) => {
      const v = values[i] && values[i][j];
      if (v === null || v === undefined) return;
      cells.push([j, i, Number(v)]);
      if (Number(v) > max) max = Number(v);
    }));
    return {
      grid: { left: 8, right: 8, top: 8, bottom: 60, containLabel: true },
      xAxis: { type: "category", data: columns.map(humanLabel), axisLabel: { rotate: 45 } },
      yAxis: { type: "category", data: rows.map(humanLabel) },
      visualMap: { min: 0, max, calculable: true, orient: "horizontal", left: "center", bottom: 0, inRange: { color: ["#fdeceb", "#9d2d25"] } },
      tooltip: { position: "top" },
      series: [{ type: "heatmap", data: cells }],
    };
  }
  const cols = Object.keys(data || {});
  const cells = [];
  cols.forEach((rk, i) => cols.forEach((ck, j) => {
    const v = data[rk] && data[rk][ck];
    if (v === null || v === undefined) return;
    cells.push([j, i, Number(Number(v).toFixed(2))]);
  }));
  return {
    grid: { left: 8, right: 8, top: 8, bottom: 60, containLabel: true },
    xAxis: { type: "category", data: cols.map(humanLabel), axisLabel: { rotate: 45 } },
    yAxis: { type: "category", data: cols.map(humanLabel) },
    visualMap: { min: -1, max: 1, calculable: true, orient: "horizontal", left: "center", bottom: 0, inRange: { color: ["#9d2d25", "#f1efe8", "#166044"] } },
    tooltip: { position: "top" },
    series: [{ type: "heatmap", data: cells }],
  };
}

export function buildChartOption(spec, data) {
  const rows = Array.isArray(data) ? data : [];
  switch (spec.type) {
    case "bar": return optBar(spec, rows);
    case "grouped_bar": return optGroupedBar(spec, rows);
    case "stacked_bar": return optStackedLong(spec, rows);
    case "stacked_bar_100": return optStacked100(spec, rows);
    case "scatter": return optScatter(spec, rows);
    case "heatmap": return optHeatmap(spec, data);
    default: return null;
  }
}

export function renderChartInto(el, spec, data) {
  if (!el) return;
  disposeChart(el);
  const rows = Array.isArray(data) ? data : [];
  const minRows = spec.minimum_rows == null ? 1 : spec.minimum_rows;
  if (spec.type !== "heatmap" && rows.length < minRows) {
    el.innerHTML = `<div class="notice">${spec.fallback || "Insufficient data."}</div>`;
    return;
  }
  const option = buildChartOption(spec, data);
  if (!option) { el.innerHTML = `<div class="notice">Unsupported chart type.</div>`; return; }
  ensureTheme();
  const inst = echarts.init(el, THEME_NAME, { renderer: "canvas" });
  inst.setOption(Object.assign({ animation: !reduceMotion }, option));
  instances.set(el, inst);
}

export function mountViewCharts(root, payload) {
  if (!root || !payload) return;
  const specs = payload.chart_specs || {};
  const cdata = payload.chart_data || {};
  root.querySelectorAll("[data-chart]").forEach((el) => {
    const id = el.getAttribute("data-chart");
    const spec = specs[id];
    if (!spec) { el.innerHTML = `<div class="notice">Chart spec not found: ${id}.</div>`; return; }
    renderChartInto(el, spec, cdata[id]);
  });
}
```

- [ ] **Step 4: Write the chart-contract test (append to `tests/test_redesign_foundation.py`)**

```python
SAMPLES = {
    "bar": ({"type": "bar", "x_field": "sentiment", "y_field": "count", "value_format": "count", "minimum_rows": 1},
            [{"sentiment": "positive", "count": 5}, {"sentiment": "negative", "count": 3}]),
    "grouped_bar": ({"type": "grouped_bar", "x_field": "powertrain", "series_keys": ["a", "b"], "value_format": "pct", "minimum_rows": 1},
                    [{"powertrain": "EV", "a": 10, "b": 4}, {"powertrain": "ICE", "a": 6, "b": 2}]),
    "stacked_bar": ({"type": "stacked_bar", "x_field": "vehicle_mentioned", "series_field": "issue_severity", "value_field": "count", "series_order": ["critical", "minor"], "minimum_rows": 1},
                    [{"vehicle_mentioned": "X", "issue_severity": "critical", "count": 3}, {"vehicle_mentioned": "X", "issue_severity": "minor", "count": 2}]),
    "stacked_bar_100": ({"type": "stacked_bar_100", "x_field": "vehicle", "series_order": ["negative", "positive"], "minimum_rows": 1},
                        [{"vehicle": "X", "negative": 4, "positive": 6}, {"vehicle": "Y", "negative": 1, "positive": 9}]),
    "scatter": ({"type": "scatter", "x_field": "volume", "y_field": "pct_negative", "label_field": "theme", "color_field": "priority", "color_map": {"Fix now": "#c2413b"}, "minimum_rows": 1},
                [{"volume": 12, "pct_negative": 80, "theme": "range", "priority": "Fix now"}]),
    "heatmap": ({"type": "heatmap", "minimum_rows": 1},
                {"rows": ["a", "b"], "columns": ["x", "y"], "values": [[1, 2], [3, None]]}),
}

EXPECTED_SERIES_TYPE = {
    "bar": "bar", "grouped_bar": "bar", "stacked_bar": "bar",
    "stacked_bar_100": "bar", "scatter": "scatter", "heatmap": "heatmap",
}


def test_chart_contract_all_types_map_to_echarts(live_server, browser_page):
    page = browser_page
    page.goto(live_server.url)
    for ctype, (spec, data) in SAMPLES.items():
        series_type = page.evaluate(
            """async ([spec, data]) => {
                const m = await import('/static/js/charts.js');
                const o = m.buildChartOption(spec, data);
                return (o && Array.isArray(o.series) && o.series.length) ? o.series[0].type : null;
            }""",
            [spec, data],
        )
        assert series_type == EXPECTED_SERIES_TYPE[ctype], f"{ctype} mapped to {series_type}"


def test_render_into_draws_canvas_and_disposes(live_server, browser_page):
    page = browser_page
    page.goto(live_server.url)
    spec, data = SAMPLES["bar"]
    result = page.evaluate(
        """async ([spec, data]) => {
            const m = await import('/static/js/charts.js');
            const el = document.createElement('div');
            el.style.width = '400px'; el.style.height = '300px';
            document.body.appendChild(el);
            m.renderChartInto(el, spec, data);
            const drawn = el.querySelector('canvas') !== null;
            m.disposeChart(el);
            const cleared = el.querySelector('canvas') === null;
            return { drawn, cleared };
        }""",
        [spec, data],
    )
    assert result["drawn"], "renderChartInto must create a canvas"
    assert result["cleared"], "disposeChart must remove the canvas"
```

- [ ] **Step 5: Run the contract tests — expect PASS (skips if Chromium absent)**

Run: `python -m pytest tests/test_redesign_foundation.py -k "contract or render_into" -v`
Expected: 2 passed (or skipped if Chromium not installed).

- [ ] **Step 6: Commit**

```bash
git add web/vendor/echarts.min.js web/js/state.js web/js/charts.js tests/test_redesign_foundation.py
git commit -m "feat(phase0): vendor ECharts + charts.js theme/registry/spec-mapping with contract tests"
```

---

## Task 3: Design tokens + dual color semantics (CSS)

Adds the token system and status classes. Verified by a Playwright assertion that the tokens resolve.

**Files:**
- Modify: `web/styles.css` (prepend a `:root` token block + status helper classes)
- Test: `tests/test_redesign_foundation.py` (append)

- [ ] **Step 1: Prepend the token block to `web/styles.css`**

Add at the very top of `web/styles.css` (keep all existing rules below it; existing `--blue`/`--green` etc. variables remain — these are additive):

```css
:root {
  /* neutral UI palette */
  --bg-page: #f6f7f9;
  --bg-surface: #ffffff;
  --bg-muted: #f1f5f9;
  --text-primary: #0f172a;
  --text-secondary: #475569;
  --text-tertiary: #94a3b8;
  --border-subtle: rgba(15, 23, 42, 0.08);
  --border-strong: rgba(15, 23, 42, 0.16);
  --radius-md: 8px;
  --radius-lg: 12px;
  --space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px; --space-6: 24px;
  --motion-fast: 150ms; --motion-base: 220ms;

  /* data-sentiment tokens (mirror src/charts.py color_maps; non-chart UI) */
  --data-negative: #ef4444;
  --data-neutral: #94a3b8;
  --data-positive: #22c55e;
  --data-critical: #dc2626;
  --data-major: #f59e0b;
  --data-minor: #3b82f6;
  --data-none: #94a3b8;

  /* system-status tokens (separate hue lane; red reserved for data-negative) */
  --status-pending: #94a3b8;
  --status-running: #2563eb;
  --status-done: #15803d;
  --status-warning: #b45309;
  --status-blocked: #475569;
  --status-skipped: #94a3b8;
  --status-failed: #be123c;
  --status-cancelled: #64748b;
}

@media (prefers-reduced-motion: reduce) {
  :root { --motion-fast: 0ms; --motion-base: 0ms; }
}

/* status pill: color + icon + label (never color alone) */
.status-pill { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; padding: 3px 9px; border-radius: var(--radius-md); }
.status-pill[data-status="pending"]   { color: var(--status-pending); }
.status-pill[data-status="running"]   { color: var(--status-running); }
.status-pill[data-status="completed"] { color: var(--status-done); }
.status-pill[data-status="completed_with_warnings"] { color: var(--status-warning); }
.status-pill[data-status="blocked"]   { color: var(--status-blocked); }
.status-pill[data-status="skipped"]   { color: var(--status-skipped); }
.status-pill[data-status="failed"]    { color: var(--status-failed); }
.status-pill[data-status="cancelled"] { color: var(--status-cancelled); }
```

- [ ] **Step 2: Write the token test (append to `tests/test_redesign_foundation.py`)**

```python
def test_design_tokens_defined(live_server, browser_page):
    page = browser_page
    page.goto(live_server.url)
    required = ["--status-failed", "--status-running", "--status-done", "--data-negative", "--radius-lg"]
    for token in required:
        value = page.evaluate(
            "t => getComputedStyle(document.documentElement).getPropertyValue(t).trim()", token
        )
        assert value, f"design token {token} is not defined"
    # Architecture-review invariant: failed != data-negative (separate hue lanes)
    failed = page.evaluate("() => getComputedStyle(document.documentElement).getPropertyValue('--status-failed').trim()")
    negative = page.evaluate("() => getComputedStyle(document.documentElement).getPropertyValue('--data-negative').trim()")
    assert failed != negative, "system 'failed' must not reuse the data-negative red"
```

- [ ] **Step 3: Run — expect PASS (skips if Chromium absent)**

Run: `python -m pytest tests/test_redesign_foundation.py -k tokens -v`
Expected: 1 passed (or skipped).

- [ ] **Step 4: Commit**

```bash
git add web/styles.css tests/test_redesign_foundation.py
git commit -m "feat(phase0): light design tokens + dual color semantics (data vs system status)"
```

---

## Task 4: Write the integration smoke tests (test-first, expected to FAIL)

These define the target behavior for the split + ECharts wiring. The chart-render assertion FAILS against the current hand-drawn app; the no-error/no-overflow assertions guard the refactor.

**Files:**
- Test: `tests/test_redesign_foundation.py` (append)

- [ ] **Step 1: Append the smoke tests**

```python
def _load_app_with_data(page, live_server):
    page.goto(live_server.url)
    page.fill("#tagInput", live_server.tag)
    page.dispatch_event("#tagInput", "change")
    page.wait_for_response(lambda r: "/api/run" in r.url and r.status == 200, timeout=15000)


def test_explore_charts_render_as_echarts_canvas(live_server, browser_page):
    page = browser_page
    _load_app_with_data(page, live_server)
    page.click('.tab[data-view="explore"]')
    page.wait_for_selector("[data-chart] canvas", timeout=15000)
    canvas_count = page.eval_on_selector_all("[data-chart] canvas", "els => els.length")
    assert canvas_count >= 5, f"expected >=5 ECharts canvases in Explore, got {canvas_count}"


def test_all_tabs_no_console_errors(live_server, browser_page):
    page = browser_page
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    _load_app_with_data(page, live_server)
    for view in ["dashboard", "collect", "classify", "explore", "briefing", "trends", "qa"]:
        page.click(f'.tab[data-view="{view}"]')
        page.wait_for_timeout(200)
    assert errors == [], f"console/page errors: {errors}"


def test_no_horizontal_overflow_three_viewports(live_server, browser_page):
    page = browser_page
    for width, height in [(1440, 900), (768, 1024), (375, 812)]:
        page.set_viewport_size({"width": width, "height": height})
        _load_app_with_data(page, live_server)
        page.click('.tab[data-view="explore"]')
        page.wait_for_timeout(300)
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
        )
        assert not overflow, f"horizontal overflow at {width}px"
```

- [ ] **Step 2: Run — expect FAIL on the canvas test (current charts are hand-drawn, no `<canvas>`)**

Run: `python -m pytest tests/test_redesign_foundation.py -k "echarts_canvas or console_errors or overflow" -v`
Expected: `test_explore_charts_render_as_echarts_canvas` FAILS (no canvas yet). The other two may pass against the current monolith — that is fine; they are the refactor guard.

- [ ] **Step 3: Commit (failing target tests are intentional)**

```bash
git add tests/test_redesign_foundation.py
git commit -m "test(phase0): integration smoke tests for ECharts render + refactor guard (red)"
```

---

## Task 5: Split the monolith + wire ECharts into the views

The big mechanical task. Move every function from `web/app.js` into its destination module per the manifest, rewire chart rendering to placeholders + `mountViewCharts`, update `index.html`, delete the old file. Make Task 4's canvas test pass and keep everything else green.

**Files:**
- Create: `web/js/api.js`, `web/js/components.js`, `web/js/nav.js`, `web/js/app.js`
- Create: `web/js/views/{dashboard,collect,classify,explore,briefing,trends,qa}.js`
- Modify: `web/index.html`
- Delete: `web/app.js`

**Move manifest** (function → source lines in `web/app.js` → destination). Copy bodies verbatim; add `export` to each; add `import` lines for symbols a module references.

| Function(s) | Source lines | Destination |
|---|---|---|
| `apiUrl`, `request` | 51-77 | `js/api.js` |
| `setNotice`, `setBusy` | 78-92 | `js/components.js` |
| `panel`, `metricGrid`, `emptyState`, `jobStatusCard` | 658-666, 625-642, 617-624, 446-478 | `js/components.js` |
| `setView`, `render`, `bindViewEvents`, `handleSaveKindClick` | 603-616, 1209-1222, 1235-1276, 1228-1234 | `js/nav.js` |
| `dashboard` | 785-820 | `js/views/dashboard.js` |
| `collectView`, `collect`, `updateCollectUi`, `collectProgressText`, `startCollectPolling`, `pollCollectStatus`, `clearCollectPolling` | 821-870, 1449-1482, 495-529, 479-487, 1278-1303, 488-494 | `js/views/collect.js` |
| `classifyView`, `previewClassify`, `llmClassify`, `upload`, `startClassifyJob`, `refreshClassifyJobStatus`, `startClassifyPolling`, `pollClassifyJobStatus`, `clearClassifyPolling` | 871-921, 1687-1758, 1663-1686, 1337-1379, 1311-1336, 1304-1310 | `js/views/classify.js` |
| `exploreView`, `filterPanel`, `buildFilterParams`, `hasActiveFilters`, `bindFilterEvents`, `applyFilters`, `clearFilters`, `evidenceFeed`, `evidenceTable` | 922-983, 139-215, 712-752 | `js/views/explore.js` |
| `briefingView`, `briefing` | 984-1034, 1729-1758 | `js/views/briefing.js` |
| `trendsView`, `loadTrends`, `renderTrendCluster`, `trendConfidenceBadge`, `trendDirectionIcon`, `trendJobBar`, `trendBriefingBar`, and all trend/trend-briefing controllers + polling | 1035-1208, 1515-1662 | `js/views/trends.js` |
| `qaView`, `qaJobBar`, `qaEvidenceCard`, and all qa controllers + polling | 1759-2009 | `js/views/qa.js` |
| `loadRun`, `updateDownloads`, `sourceDownloadsPanel`, `saveExport`, export-job controllers (`startExportJob`, `refreshExportJobStatus`, `pollExportJobStatus`, `startExportPolling`, `clearExportPolling`), `bindGlobalEvents` | 530-602, 753-784, 1483-1514, 1380-1448, 2010-2021 | `js/app.js` |

Functions REMOVED entirely (replaced by `charts.js`): `formatValue` (moved to state.js), `renderChart`, `renderBarChart`, `renderStackedBarLong`, `renderStackedBarWide`, `renderGroupedBar`, `renderHeatmapChart`, `renderCompactTable`, `bars`, `scatter`, `chartPanel` (rewritten — see Step 4).

- [ ] **Step 1: Create `js/api.js`**

```javascript
// api.js — HTTP primitives.
export function apiUrl(path, params = {}) {
  // copy body from web/app.js lines 51-62
}
export async function request(path, options = {}) {
  // copy body from web/app.js lines 63-77
}
```

- [ ] **Step 2: Create `js/components.js`** (move per manifest; add imports)

Header:
```javascript
import { $, $$, esc, fmt, humanLabel } from "./state.js";
```
Then paste `setNotice`, `setBusy`, `panel`, `metricGrid`, `emptyState`, `jobStatusCard` (verbatim, each `export function ...`). Add the rewritten `chartPanel` here:

```javascript
// chartPanel emits an empty ECharts mount point; charts.js fills it after render.
export function chartPanel(id, title, note = "") {
  const body = `<div class="echart" data-chart="${esc(id)}" role="img" aria-label="${esc(title)} chart" style="height:320px"></div>`;
  return panel(title, body, note, "chart-panel");
}
```

- [ ] **Step 3: Create the seven `js/views/*.js`** (move per manifest)

For each view file: add an import header pulling the symbols it uses, e.g. for `js/views/explore.js`:
```javascript
import { state, $, $$, esc, fmt, pct, humanLabel } from "../state.js";
import { apiUrl, request } from "../api.js";
import { panel, chartPanel, setNotice, setBusy, emptyState } from "../components.js";
import { render, setView } from "../nav.js";
```
Then paste the functions listed for that file (`export function ...` for the view renderer; helpers can stay module-local but export any that another module imports). Apply the same pattern to the other six views, importing only what each uses (controllers that call `render()`/`setView()` import them from `../nav.js`; ones that show charts use `chartPanel` from `../components.js`).

- [ ] **Step 4: Rewire the two direct hand-drawn chart usages to `chartPanel`**

In `js/views/dashboard.js` (was app.js 807-812), replace the `panel(..., bars(...))` / `panel("Priority map", scatter(...))` calls with spec-driven placeholders:
```javascript
${chartPanel("sentiment", "Sentiment distribution")}
${chartPanel("flags", "Signal flags")}
${chartPanel("complaints", "Complaint themes")}
${chartPanel("priority", "Priority map", "volume × negativity")}
```
In `js/views/explore.js` (was app.js 936), replace `panel("Priority map", scatter(...))` with `${chartPanel("priority", "Priority map", "volume × negativity")}`.
Remove the now-unused `bars`/`scatter` imports if any were added.

- [ ] **Step 5: Create `js/nav.js`** (move `setView`/`bindViewEvents`/`handleSaveKindClick` verbatim; rewrite `render` to mount charts)

```javascript
import { state, $, $$ } from "./state.js";
import { mountViewCharts } from "./charts.js";
import { dashboard } from "./views/dashboard.js";
import { collectView } from "./views/collect.js";
import { classifyView } from "./views/classify.js";
import { exploreView } from "./views/explore.js";
import { briefingView } from "./views/briefing.js";
import { trendsView } from "./views/trends.js";
import { qaView } from "./views/qa.js";
// also import the per-view event binders those views export and use in bindViewEvents.

export function setView(view) {
  // copy body from web/app.js lines 603-616 (uses state, $$)
}

const views = {
  dashboard, collect: collectView, classify: classifyView,
  explore: exploreView, briefing: briefingView, trends: trendsView, qa: qaView,
};

export function render() {
  const root = $("#viewRoot");
  root.innerHTML = (views[state.view] || (() => ""))();
  bindViewEvents();
  mountViewCharts(root, state.data);
}

export function handleSaveKindClick(event) {
  // copy body from web/app.js lines 1228-1234
}

export function bindViewEvents() {
  // copy body from web/app.js lines 1235-1276
}
```
Note: any view-specific binder that lived inside the old `bindViewEvents` must be imported from its view module here.

- [ ] **Step 6: Create `js/app.js`** (bootstrap; move per manifest)

```javascript
import { state, $, $$ } from "./state.js";
import { apiUrl, request } from "./api.js";
import { setNotice } from "./components.js";
import { render, setView, handleSaveKindClick } from "./nav.js";
import { bindResize } from "./charts.js";

// paste: loadRun, updateDownloads, sourceDownloadsPanel, saveExport,
// startExportJob, refreshExportJobStatus, pollExportJobStatus,
// startExportPolling, clearExportPolling, bindGlobalEvents (verbatim, with `export` where imported elsewhere).

bindGlobalEvents();
bindResize();
loadRun();
```
Any function here that another module calls (e.g. `loadRun`, `saveExport`, `updateDownloads`) must be `export`ed and imported where used (views import them from `../app.js`). If that creates an undesirable cycle, leave them here and have the view trigger them through an existing DOM event instead — but the simple import works (runtime calls).

- [ ] **Step 7: Update `web/index.html`** — load ECharts before the module entry

Replace the single script tag (line 116) with:
```html
    <script src="/static/vendor/echarts.min.js"></script>
    <script src="/static/js/app.js" type="module"></script>
```

- [ ] **Step 8: Delete the old monolith**

```bash
git rm web/app.js
```

- [ ] **Step 9: Run the full redesign suite — expect ALL PASS (skips only if Chromium absent)**

Run: `python -m pytest tests/test_redesign_foundation.py -v`
Expected: characterization (2) + contract (2) + tokens (1) + smoke (3) all PASS. In particular `test_explore_charts_render_as_echarts_canvas` now PASSES.
If a view throws (console error test fails), the message names the failing module — fix the import/export for that module and re-run.

- [ ] **Step 10: Run the entire test suite — expect existing 180 still green**

Run: `python -m pytest -q`
Expected: previous 180 pass + new redesign tests pass/skip. No regressions.

- [ ] **Step 11: Commit**

```bash
git add web/ tests/
git commit -m "feat(phase0): split app.js into ES modules; render all charts via ECharts"
```

---

## Task 6: Freeze the integration contract document

**Files:**
- Create: `docs/redesign/integration-contract.md`

- [ ] **Step 1: Create the contract doc**

Create `docs/redesign/integration-contract.md`. Open with this preamble, then copy section 6 (Contracts A, B, C — the API shapes, the step-state vocabulary table, and the chart_specs→ECharts mapping table) verbatim from `docs/superpowers/specs/2026-06-18-ui-redesign-phase0-design.md`:

```markdown
# redditgm v2 — Frozen Integration Contract (Phase 0)

Status: FROZEN as of 2026-06-18. Both the backend track (Track A) and the frontend
track (Track B) build against this. Changes require updating this doc first.

- Contract A (Pipeline / run API) and Contract B (step-state vocabulary) are
  frozen-on-paper here; they are implemented in Phase 1 Track A.
- Contract C (chart_specs → ECharts mapping) is implemented in Phase 0 (web/js/charts.js).

<paste Contracts A, B, C from the design spec §6 here>
```

- [ ] **Step 2: Sanity-check the doc contains all three contracts**

Run: `grep -E "Contract A|Contract B|Contract C|/api/analyze|completed_with_warnings|stacked_bar_100" docs/redesign/integration-contract.md`
Expected: matches for each contract heading, the analyze endpoint, the warnings state, and a chart type — confirming the three contracts are present.

- [ ] **Step 3: Commit**

```bash
git add docs/redesign/integration-contract.md
git commit -m "docs(phase0): freeze integration contract (API shapes, step states, chart mapping)"
```

---

## Task 7: Final verification + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Full suite green**

Run: `python -m pytest -q`
Expected: all pass (browser tests skip only if Chromium unavailable). Record the count.

- [ ] **Step 2: Manual smoke (run the app yourself)**

Run: `python run.py` (or `uvicorn app:app --reload`), open the served URL.
Verify by eye: all 7 tabs load; Dashboard + Explore charts now render as ECharts (hover tooltips work); no console errors (DevTools); resize the window — charts resize; no horizontal scrollbar at narrow widths.

- [ ] **Step 3: Confirm scope boundaries held**

Verify: `git diff --stat main` touches only `web/`, `tests/`, `docs/`. No changes to `app.py`, `src/`, or `scripts/` (Phase 0 is frontend + docs + tests only).

- [ ] **Step 4: Final commit if anything outstanding**

```bash
git status   # should be clean after Task 5/6 commits
```

---

## Self-review

**1. Spec coverage** (against `docs/superpowers/specs/2026-06-18-ui-redesign-phase0-design.md`):
- §2 module split → Task 5 (manifest). ✓
- §5.1 ECharts + theme + dispose + resize + reduced-motion → Task 2 (`charts.js`). ✓
- §5.2 / §6 Contract C chart mapping → Task 2 (`buildChartOption`) + Task 4/5 (render) + Task 6 (doc). ✓
- §5.3 design tokens + dual color semantics + non-color cue → Task 3. ✓
- §6 Contracts A & B (paper) → Task 6. ✓
- §7 verification (180 green, chart-contract, smoke, 3 viewports) → Tasks 1,2,4,5,7. ✓
- §2 out-of-scope (no app.py/src changes) → Task 7 Step 3 guard. ✓

**2. Placeholder scan:** Move-manifest cites exact source line ranges (code exists in repo); all new artifacts (charts.js, tokens, tests, fixtures, bootstrap, rewired chartPanel) are given in full. No "TBD"/"add error handling"/"similar to". ✓

**3. Type/name consistency:** `buildChartOption`, `renderChartInto`, `mountViewCharts`, `disposeChart`, `disposeAll`, `bindResize` are defined in Task 2 and referenced identically in Tasks 4/5. `chartPanel(id, title, note)` emits `data-chart="id"`; `mountViewCharts` reads `[data-chart]` — consistent. `live_server` exposes `.url`/`.tag`; tests use both — consistent. Test tag `redesign_test_fixture` used in conftest + asserted via UI fill — consistent. ✓

**Known follow-up (not a Phase 0 blocker):** lazy heatmaps (`category_by_model`, `cooccurrence`) live in `/api/charts/detail`, not in `state.data.chart_data`. If a view currently renders them, fetch detail and merge into the payload before `mountViewCharts`. Confirm whether the current Explore view shows them during Task 5 Step 3; if not, no action needed.
