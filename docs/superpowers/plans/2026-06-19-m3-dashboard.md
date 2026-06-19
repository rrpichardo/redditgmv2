# M3 Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the Dashboard tab to be signals-first — hero card + 2-column signal grid at the top, followed by run metrics and 7 chart panels (3 existing + 4 new trend charts).

**Architecture:** Add a `GET /api/trends/timeseries` backend endpoint that buckets classified rows by ISO week. Eagerly fetch trends + timeseries alongside the existing `/api/run` call on app init. Rewrite `dashboard.js` to render the signals section and mount new ECharts charts via exported helpers.

**Tech Stack:** Python/FastAPI (backend), vanilla JS ES modules, Apache ECharts (already vendored at `web/vendor/echarts.min.js`), pytest + FastAPI TestClient (tests).

---

## File Map

| File | Change |
|------|--------|
| `app.py` | Add `GET /api/trends/timeseries` endpoint (after line 1131) |
| `tests/test_m3.py` | New — 4 tests for the timeseries endpoint and signal sort logic |
| `web/js/state.js` | Add `timeseriesData: null` field |
| `web/js/app.js` | Fetch `/api/trends` + `/api/trends/timeseries` in parallel inside `loadRun()` |
| `web/js/charts.js` | Export `renderIntoEl` + 4 new option builders + `renderSparkline` |
| `web/js/views/dashboard.js` | Full rewrite — signals hero, 2-col grid, sparklines, all chart sections |
| `web/js/nav.js` | Import `mountDashboardCharts` from dashboard.js; call it after `render()` |

---

## Task 1 — Backend: `/api/trends/timeseries` endpoint

**Files:**
- Modify: `app.py` (after the `@app.get("/api/trends")` block, ~line 1131)
- Create: `tests/test_m3.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_m3.py`:

```python
"""Tests for M3 Dashboard: /api/trends/timeseries endpoint."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

_tmp = tempfile.mkdtemp()

import app as app_module  # noqa: E402

app_module.RUNTIME = Path(_tmp)

from app import app  # noqa: E402

client = TestClient(app, raise_server_exceptions=True)

TAG = "m3_test"


def _write_classified(df: pd.DataFrame) -> None:
    path = Path(_tmp) / TAG / "classified" / "classified_posts.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def test_timeseries_returns_weekly_buckets():
    """Endpoint buckets rows by ISO week and counts sentiment correctly."""
    df = pd.DataFrame({
        "source_id": ["r1", "r2", "r3", "r4"],
        "created_at_norm": [
            "2024-01-10 12:00:00",  # 2024-W02
            "2024-01-11 09:00:00",  # 2024-W02
            "2024-01-18 10:00:00",  # 2024-W03
            "2024-01-25 11:00:00",  # 2024-W04
        ],
        "sentiment": ["negative", "positive", "negative", "neutral"],
        "classifier_mode": ["llm", "llm", "llm", "llm"],
        "skip_classification": [False, False, False, False],
    })
    _write_classified(df)
    r = client.get(f"/api/trends/timeseries?tag={TAG}")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert len(data["buckets"]) >= 3
    assert sum(data["negative"]) == 2
    assert sum(data["positive"]) == 1
    assert sum(data["neutral"]) == 1
    assert "by_cluster" in data


def test_timeseries_no_timestamp_column_returns_not_ok():
    """Returns ok=False when classified CSV has no created_at_norm column."""
    df = pd.DataFrame({
        "source_id": ["r1", "r2"],
        "sentiment": ["negative", "positive"],
        "classifier_mode": ["llm", "llm"],
        "skip_classification": [False, False],
    })
    _write_classified(df)
    r = client.get(f"/api/trends/timeseries?tag={TAG}")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert "timestamp" in data["detail"].lower()


def test_timeseries_per_cluster_counts():
    """by_cluster contains weekly counts keyed by string cluster_id."""
    df = pd.DataFrame({
        "source_id": ["r1", "r2", "r3", "r4"],
        "created_at_norm": [
            "2024-01-10 12:00:00",
            "2024-01-10 13:00:00",
            "2024-01-18 10:00:00",
            "2024-01-18 11:00:00",
        ],
        "sentiment": ["negative", "negative", "positive", "neutral"],
        "cluster_id": [0, 1, 0, 1],
        "classifier_mode": ["llm", "llm", "llm", "llm"],
        "skip_classification": [False, False, False, False],
    })
    _write_classified(df)
    r = client.get(f"/api/trends/timeseries?tag={TAG}")
    data = r.json()
    assert data["ok"] is True
    bc = data["by_cluster"]
    assert "0" in bc and "1" in bc
    assert sum(bc["0"]) == 2
    assert sum(bc["1"]) == 2


def test_timeseries_missing_classified_returns_not_ok():
    """Returns ok=False when no classified file exists for the tag."""
    r = client.get("/api/trends/timeseries?tag=nonexistent_tag_xyz")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/ricopichardo/Claude/redditgmv2
pytest tests/test_m3.py -v 2>&1 | tail -20
```

Expected: 4 FAILs (endpoint doesn't exist yet — 404 or AttributeError).

- [ ] **Step 3: Add the endpoint to `app.py`**

Insert after line 1131 (after the closing of `trends_results`), before `@app.post("/api/trends/briefing")`:

```python
@app.get("/api/trends/timeseries")
def trends_timeseries(tag: str = DEFAULT_TAG) -> JSONResponse:
    """Return weekly bucketed sentiment counts from the classified dataset."""
    from src.gm_insights import analyzed_frame
    tag = clean_tag(tag)
    cpath = classified_path(tag)
    if not cpath.exists():
        return safe_json({"ok": False, "detail": "No classified data found."})
    try:
        raw = load_classified(cpath)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read classified data: {exc}") from exc
    if "created_at_norm" not in raw.columns:
        return safe_json({"ok": False, "detail": "No timestamp column in classified data."})
    df = analyzed_frame(raw)
    df = df.copy()
    df["_ts"] = pd.to_datetime(df["created_at_norm"], errors="coerce")
    df = df.dropna(subset=["_ts"])
    if df.empty:
        return safe_json({"ok": False, "detail": "No rows with valid timestamps."})
    df["_week"] = df["_ts"].dt.strftime("%G-W%V")
    weeks = sorted(df["_week"].unique().tolist())
    if len(weeks) < 2:
        return safe_json({"ok": False, "detail": "Fewer than 2 weeks of data."})
    sent_counts = df.groupby(["_week", "sentiment"]).size().unstack(fill_value=0)

    def _week_series(col: str) -> list[int]:
        if col not in sent_counts.columns:
            return [0] * len(weeks)
        return [int(sent_counts[col].get(w, 0)) for w in weeks]

    by_cluster: dict[str, list[int]] = {}
    if "cluster_id" in df.columns:
        cluster_counts = df.groupby(["_week", "cluster_id"]).size().unstack(fill_value=0)
        for cid in cluster_counts.columns:
            by_cluster[str(int(cid))] = [int(cluster_counts[cid].get(w, 0)) for w in weeks]

    return safe_json({
        "ok": True,
        "buckets": weeks,
        "negative": _week_series("negative"),
        "positive": _week_series("positive"),
        "neutral": _week_series("neutral"),
        "by_cluster": by_cluster,
    })
```

- [ ] **Step 4: Run tests — all 4 must pass**

```bash
pytest tests/test_m3.py -v
```

Expected output:
```
tests/test_m3.py::test_timeseries_returns_weekly_buckets PASSED
tests/test_m3.py::test_timeseries_no_timestamp_column_returns_not_ok PASSED
tests/test_m3.py::test_timeseries_per_cluster_counts PASSED
tests/test_m3.py::test_timeseries_missing_classified_returns_not_ok PASSED
4 passed
```

- [ ] **Step 5: Run full suite to check for regressions**

```bash
pytest --tb=short -q 2>&1 | tail -10
```

Expected: all prior tests still pass.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_m3.py
git commit -m "feat(m3): add /api/trends/timeseries endpoint — weekly sentiment + per-cluster buckets"
```

---

## Task 2 — Frontend state + parallel data loading

**Files:**
- Modify: `web/js/state.js` (add `timeseriesData: null`)
- Modify: `web/js/app.js` (parallel fetch in `loadRun()`)

- [ ] **Step 1: Add `timeseriesData` to state**

In `web/js/state.js`, find the `export const state = {` block. Add `timeseriesData: null` after `trendsData: null`:

```js
// Before (around line 23):
  trendsData: null,
  trendJobStatus: null,

// After:
  trendsData: null,
  timeseriesData: null,
  trendJobStatus: null,
```

- [ ] **Step 2: Update `loadRun()` in `web/js/app.js`**

Import `loadTrends` from trends view at the top of `app.js` — add to existing imports:

```js
import { loadTrends } from "./views/trends.js";
```

Replace the entire `loadRun` function (lines 15–49) with this version that fires three fetches in parallel:

```js
export async function loadRun() {
  setNotice("Loading run...");
  try {
    const params = buildFilterParams();
    // Fire all three data fetches in parallel; a failed trends/timeseries fetch
    // is non-fatal — the dashboard degrades gracefully.
    const [runResult, trendsResult, timeseriesResult] = await Promise.allSettled([
      request(apiUrl("/api/run", { tag: state.tag, ...params })),
      request(apiUrl("/api/trends", { tag: state.tag })),
      request(apiUrl("/api/trends/timeseries", { tag: state.tag })),
    ]);

    if (runResult.status === "rejected") throw runResult.reason;
    state.data = runResult.value;
    state.trendsData = trendsResult.status === "fulfilled" ? trendsResult.value : null;
    state.timeseriesData = timeseriesResult.status === "fulfilled" ? timeseriesResult.value : null;

    const collectStatus = await request(
      apiUrl("/api/collect/status", { tag: state.tag })
    ).catch(() => null);

    const analyzed = state.data.summary.metrics.analyzed_rows ?? 0;
    const total = state.data.summary.metrics.total_rows ?? 0;
    const filterNote = hasActiveFilters() ? " [filtered]" : "";
    const subtitle = $("#runSubtitle");
    if (subtitle) {
      subtitle.textContent =
        `${state.data.tag} / ${fmt.format(total)} rows / ${fmt.format(analyzed)} analyzed / ` +
        `${state.data.status.has_classified ? "classified" : "source only"}${filterNote}`;
    }

    updateDownloads();
    render();

    if (collectStatus?.status === "running") {
      updateCollectUi(collectStatus);
      startCollectPolling(collectStatus.job_id);
    } else {
      setNotice("");
    }
  } catch (error) {
    setNotice(error.message, "error");
  }
}
```

- [ ] **Step 3: Verify app still loads without errors**

```bash
cd /Users/ricopichardo/Claude/redditgmv2
python -m uvicorn app:app --port 8765 &
sleep 2
curl -s http://localhost:8765/api/health | python3 -m json.tool
kill %1
```

Expected: `{"status": "ok"}` (or similar).

- [ ] **Step 4: Commit**

```bash
git add web/js/state.js web/js/app.js
git commit -m "feat(m3): parallel-fetch trends + timeseries on app init"
```

---

## Task 3 — New ECharts renderers in `charts.js`

**Files:**
- Modify: `web/js/charts.js`

- [ ] **Step 1: Add `renderIntoEl` export**

After the existing `renderChartInto` function (around line 253), add:

```js
// Render any pre-built ECharts option object directly into el.
// Disposes any existing instance first. Used by dashboard for trend charts.
export function renderIntoEl(el, option) {
  if (!el || !option) return;
  disposeChart(el);
  ensureTheme();
  const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  const inst = echarts.init(el, THEME_NAME, { renderer: "canvas" });
  inst.setOption(Object.assign({ animation: !reduceMotion }, option));
  instances.set(el, inst);
}
```

- [ ] **Step 2: Add the four new option builders**

After the `optHeatmap` function (around line 213), add:

```js
// Velocity × Z-score quadrant scatter. clusters = trendsData.clusters array.
// Dots colored by confidence_banner. Reference lines at x=0, y=0.
function optQuadrant(clusters) {
  const confColor = { high: "#16a34a", medium: "#d97706", low: "#94a3b8" };
  const data = clusters
    .filter((c) => c.trend_signal?.velocity?.valid && c.trend_signal?.zscore?.valid)
    .map((c) => {
      const sig = c.trend_signal;
      return {
        value: [Number(sig.velocity.velocity ?? 0), Number(sig.zscore.zscore ?? 0)],
        name: c.label?.short_label || `Cluster ${c.cluster_id}`,
        itemStyle: { color: confColor[sig.confidence_banner] || "#94a3b8" },
      };
    });
  return {
    grid: { left: 16, right: 24, top: 24, bottom: 36, containLabel: true },
    xAxis: { type: "value", name: "Velocity", nameLocation: "middle", nameGap: 28 },
    yAxis: { type: "value", name: "Z-score", nameLocation: "middle", nameGap: 40 },
    tooltip: {
      trigger: "item",
      formatter: (p) => `${esc(p.data.name)}<br/>Velocity: ${Number(p.value[0]).toFixed(2)}<br/>Z-score: ${Number(p.value[1]).toFixed(2)}`,
    },
    series: [{
      type: "scatter",
      data,
      symbolSize: 12,
      markLine: {
        silent: true,
        symbol: "none",
        lineStyle: { color: "#cbd5e1", type: "dashed" },
        data: [{ xAxis: 0 }, { yAxis: 0 }],
      },
    }],
  };
}

// Horizontal bar ranked by velocity descending. clusters = trendsData.clusters.
function optLeaderboard(clusters) {
  const confColor = { high: "#16a34a", medium: "#d97706", low: "#94a3b8" };
  const sorted = [...clusters]
    .filter((c) => c.trend_signal?.velocity?.valid)
    .sort((a, b) => (b.trend_signal.velocity.velocity ?? 0) - (a.trend_signal.velocity.velocity ?? 0))
    .slice(0, 15);
  const cats = sorted.map((c) => c.label?.short_label || `Cluster ${c.cluster_id}`);
  const data = sorted.map((c) => ({
    value: Number(((c.trend_signal.velocity.velocity ?? 0) * 100).toFixed(1)),
    itemStyle: { color: confColor[c.trend_signal.confidence_banner] || "#94a3b8" },
  }));
  return {
    grid: { left: 8, right: 48, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: "value", axisLabel: { formatter: "{value}%" } },
    yAxis: { type: "category", data: cats, inverse: true },
    tooltip: { trigger: "axis", valueFormatter: (v) => `${v}%` },
    series: [{ type: "bar", data, barMaxWidth: 20 }],
  };
}

// Line chart for a single weekly sentiment series.
// tsd = timeseriesData ({ buckets, negative, positive, neutral }).
// seriesKey = "negative" | "positive" | "neutral".
function optLineTimeseries(tsd, seriesKey) {
  return {
    grid: { left: 8, right: 24, top: 16, bottom: 8, containLabel: true },
    xAxis: { type: "category", data: tsd.buckets || [], axisLabel: { rotate: 30, fontSize: 10 } },
    yAxis: { type: "value" },
    tooltip: { trigger: "axis" },
    series: [{ type: "line", data: tsd[seriesKey] || [], smooth: true, areaStyle: { opacity: 0.12 } }],
  };
}

// Stacked area for positive / neutral / negative weekly volumes.
// tsd = timeseriesData.
function optStackedAreaTimeseries(tsd) {
  const palette = { positive: "#16a34a", neutral: "#94a3b8", negative: "#dc2626" };
  const series = ["positive", "neutral", "negative"].map((key) => ({
    name: key.charAt(0).toUpperCase() + key.slice(1),
    type: "line",
    stack: "sentiment",
    smooth: true,
    areaStyle: {},
    itemStyle: { color: palette[key] },
    data: tsd[key] || [],
  }));
  return {
    legend: {},
    grid: { left: 8, right: 24, top: 32, bottom: 8, containLabel: true },
    xAxis: { type: "category", data: tsd.buckets || [], axisLabel: { rotate: 30, fontSize: 10 } },
    yAxis: { type: "value" },
    tooltip: { trigger: "axis" },
    series,
  };
}
```

- [ ] **Step 3: Add exports for the four builders + `renderSparkline`**

After the existing `export function mountViewCharts(...)` block at the bottom of the file, add:

```js
// Exported option builders for dashboard trend charts.
export const buildQuadrantOption = optQuadrant;
export const buildLeaderboardOption = optLeaderboard;
export const buildLineTimeseriesOption = optLineTimeseries;
export const buildStackedAreaOption = optStackedAreaTimeseries;

// Render a tiny 80×32 sparkline into el. weeklyData = number[].
export function renderSparkline(el, weeklyData) {
  if (!el || !weeklyData?.length) return;
  disposeChart(el);
  ensureTheme();
  const inst = echarts.init(el, THEME_NAME, { renderer: "canvas", width: 80, height: 32 });
  inst.setOption({
    animation: false,
    grid: { left: 0, right: 0, top: 2, bottom: 2 },
    xAxis: { type: "category", show: false, data: weeklyData.map((_, i) => i) },
    yAxis: { type: "value", show: false },
    series: [{ type: "line", data: weeklyData, smooth: true, symbol: "none", lineStyle: { width: 1.5 } }],
  });
  instances.set(el, inst);
}
```

- [ ] **Step 4: Commit**

```bash
git add web/js/charts.js
git commit -m "feat(m3): add quadrant/leaderboard/timeseries ECharts renderers + renderSparkline"
```

---

## Task 4 — Dashboard view rewrite

**Files:**
- Modify: `web/js/views/dashboard.js` (full rewrite)
- Modify: `web/js/nav.js` (import + call `mountDashboardCharts`)

- [ ] **Step 1: Rewrite `web/js/views/dashboard.js`**

Replace the entire file contents:

```js
// dashboard.js — signals-first landing: hero card, 2-col signal grid,
// run metrics, and chart panels (3 existing + 4 new trend charts).
import { state, esc, fmt, humanLabel } from "../state.js";
import { panel, chartPanel, metricGrid, emptyState } from "../components.js";
import {
  renderIntoEl, renderSparkline,
  buildQuadrantOption, buildLeaderboardOption,
  buildLineTimeseriesOption, buildStackedAreaOption,
} from "../charts.js";

// ── Helpers ─────────────────────────────────────────────────────────────────

function dirArrow(dir) {
  if (dir === "rising") return "↑";
  if (dir === "falling") return "↓";
  if (dir === "stable") return "→";
  return "—";
}

function confBadge(level) {
  const styles = {
    high: "background:#dcfce7;color:#166534",
    medium: "background:#fef9c3;color:#713f12",
    low: "background:#f1f5f9;color:#475569",
  };
  const s = styles[level] || styles.low;
  return `<span style="${s};border-radius:3px;padding:1px 5px;font-size:10px;font-weight:700">${esc((level || "n/a").toUpperCase())}</span>`;
}

// Sort: rising HIGH→MED→LOW → stable → falling → N/A. Tiebreak by cluster_size.
function sortSignals(clusters) {
  const dirOrd = { rising: 0, stable: 1, falling: 2, insufficient_data: 3 };
  const confOrd = { high: 0, medium: 1, low: 2 };
  return [...clusters].sort((a, b) => {
    const aDir = a.trend_signal?.velocity?.direction || "insufficient_data";
    const bDir = b.trend_signal?.velocity?.direction || "insufficient_data";
    const aC = a.trend_signal?.confidence_banner || "low";
    const bC = b.trend_signal?.confidence_banner || "low";
    const d = (dirOrd[aDir] ?? 3) - (dirOrd[bDir] ?? 3);
    if (d !== 0) return d;
    const c = (confOrd[aC] ?? 2) - (confOrd[bC] ?? 2);
    if (c !== 0) return c;
    return (b.cluster_size || 0) - (a.cluster_size || 0);
  });
}

function hasRising(clusters) {
  return clusters.some((c) => c.trend_signal?.velocity?.direction === "rising");
}

// ── Signals section ──────────────────────────────────────────────────────────

function signalHero(clusters) {
  const noRising = !hasRising(clusters);
  // Top cluster: best rising signal, or largest by size if none rising.
  const top = noRising
    ? [...clusters].sort((a, b) => (b.cluster_size || 0) - (a.cluster_size || 0))[0]
    : sortSignals(clusters)[0];
  if (!top) return "";

  const sig = top.trend_signal || {};
  const vel = sig.velocity || {};
  const zsc = sig.zscore || {};
  const label = top.label || {};
  const dir = vel.direction || "insufficient_data";
  const conf = sig.confidence_banner || "";
  const velPct = vel.velocity != null ? ` · ${(vel.velocity * 100).toFixed(0)}% velocity` : "";
  const zNote = zsc.direction && zsc.direction !== "insufficient_data"
    ? ` · z-score ${dirArrow(zsc.direction)}`
    : "";
  const note = noRising
    ? "No accelerating signals detected — sorted by volume."
    : esc(sig.confidence_note || "");

  return `<div style="background:#fff;border:1px solid #bfdbfe;border-radius:10px;padding:14px 18px;display:flex;gap:16px;align-items:center;margin-bottom:8px;box-shadow:0 1px 4px rgba(37,99,235,.07)">
    <div style="flex:1">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:5px">
        <span style="font-size:17px">${dirArrow(dir)}</span>
        <span style="font-size:15px;font-weight:700;color:#0f172a">${esc(label.short_label || `Cluster ${top.cluster_id}`)}</span>
        ${conf ? confBadge(conf) : ""}
      </div>
      <div style="font-size:11px;color:#64748b">${note}${velPct}${zNote}</div>
    </div>
    <div style="text-align:right;border-left:1px solid #e2e8f0;padding-left:16px;min-width:60px">
      <div style="font-size:24px;font-weight:800;color:#2563eb">${fmt.format(top.cluster_size || 0)}</div>
      <div style="font-size:10px;color:#94a3b8">posts</div>
    </div>
  </div>`;
}

function signalGrid(clusters) {
  // When no rising signals exist, the full list sorts by cluster_size (spec).
  const noRising = !hasRising(clusters);
  const ordered = noRising
    ? [...clusters].sort((a, b) => (b.cluster_size || 0) - (a.cluster_size || 0))
    : sortSignals(clusters);
  const rest = ordered.slice(1); // first cluster is already in the hero card
  if (!rest.length) return "";
  const rows = rest.map((c) => {
    const sig = c.trend_signal || {};
    const vel = sig.velocity || {};
    const dir = vel.direction || "insufficient_data";
    const label = c.label || {};
    const velText = vel.velocity != null
      ? `${(vel.velocity * 100).toFixed(0)}%`
      : dir === "stable" ? "stable" : "no data";
    const dimColor = dir === "insufficient_data" ? "#94a3b8" : "#0f172a";
    return `<div style="background:#fff;border:1px solid #e2e8f0;border-radius:7px;padding:7px 12px;display:flex;align-items:center;gap:8px;font-size:12px">
      <span style="width:14px;text-align:center;font-size:14px">${dirArrow(dir)}</span>
      <span style="flex:1;font-weight:500;color:${dimColor}">${esc(humanLabel(label.short_label || `Cluster ${c.cluster_id}`))}</span>
      <span style="color:#64748b;font-size:11px">${esc(velText)}</span>
      ${sig.confidence_banner ? confBadge(sig.confidence_banner) : ""}
      <div id="sparkline-${c.cluster_id}" style="width:80px;height:32px;flex-shrink:0"></div>
    </div>`;
  });
  return `<div style="display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-bottom:20px">${rows.join("")}</div>`;
}

// ── Mount trend charts (called by nav.js after DOM is painted) ───────────────

export function mountDashboardCharts() {
  if (state.view !== "dashboard") return;
  const td = state.trendsData;
  const tsd = state.timeseriesData;
  const clusters = td?.ok && td.clusters?.length ? td.clusters : null;

  if (clusters) {
    const quadEl = document.getElementById("quadrantChart");
    if (quadEl) renderIntoEl(quadEl, buildQuadrantOption(clusters));
    const lbEl = document.getElementById("leaderboardChart");
    if (lbEl) renderIntoEl(lbEl, buildLeaderboardOption(clusters));
    // Sparklines: use same ordering as signalGrid so IDs match.
    const noRising = !hasRising(clusters);
    const gridOrder = noRising
      ? [...clusters].sort((a, b) => (b.cluster_size || 0) - (a.cluster_size || 0))
      : sortSignals(clusters);
    gridOrder.slice(1).forEach((c) => {
      const el = document.getElementById(`sparkline-${c.cluster_id}`);
      const data = tsd?.by_cluster?.[String(c.cluster_id)];
      if (el && data?.length) renderSparkline(el, data);
    });
  }

  if (tsd?.ok) {
    const negEl = document.getElementById("negTimeChart");
    if (negEl) renderIntoEl(negEl, buildLineTimeseriesOption(tsd, "negative"));
    const sentEl = document.getElementById("sentTimeChart");
    if (sentEl) renderIntoEl(sentEl, buildStackedAreaOption(tsd));
  } else {
    ["negTimeChart", "sentTimeChart"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = `<div class="notice">Date data required to render this chart.</div>`;
    });
  }
}

// ── Main view function ───────────────────────────────────────────────────────

export function dashboard() {
  const data = state.data;
  if (!data?.summary?.metrics?.total_rows) return emptyState();

  const td = state.trendsData;
  const clusters = td?.ok && td.clusters?.length ? td.clusters : null;

  const signalsSection = clusters
    ? `<div style="margin-bottom:4px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
          <span style="font-size:11px;font-weight:700;letter-spacing:.09em;color:#64748b;text-transform:uppercase">Signals to watch</span>
          <span style="font-size:11px;color:#94a3b8">${clusters.length} clusters</span>
        </div>
        ${signalHero(clusters)}
        ${signalGrid(clusters)}
      </div>`
    : `<div class="notice" style="margin-bottom:1rem">Run analysis to see trend signals.</div>`;

  return `
    ${signalsSection}
    <div style="border-top:1px solid #e2e8f0;margin:4px 0 16px"></div>
    <div>${metricGrid(data.summary.metrics)}</div>
    <div class="panel-grid" style="margin-top:1rem">
      ${chartPanel("sentiment", "Sentiment distribution")}
      ${chartPanel("flags", "Signal flags")}
      ${chartPanel("complaints", "Complaint themes")}
    </div>
    <div class="panel-grid two" style="margin-top:0.75rem">
      <section class="panel chart-panel">
        <div class="panel-head"><h3>Velocity × Z-score quadrant</h3><small>color = confidence</small></div>
        <div id="quadrantChart" style="height:280px"></div>
      </section>
      <section class="panel chart-panel">
        <div class="panel-head"><h3>Trend leaderboard</h3><small>by velocity</small></div>
        <div id="leaderboardChart" style="height:280px"></div>
      </section>
    </div>
    <div class="panel-grid two" style="margin-top:0.75rem">
      <section class="panel chart-panel">
        <div class="panel-head"><h3>Negative mentions over time</h3><small>weekly</small></div>
        <div id="negTimeChart" style="height:220px"></div>
      </section>
      <section class="panel chart-panel">
        <div class="panel-head"><h3>Sentiment over time</h3><small>weekly stacked</small></div>
        <div id="sentTimeChart" style="height:220px"></div>
      </section>
    </div>
    <div class="panel-grid two" style="margin-top:0.75rem">
      ${chartPanel("priority", "Priority map", "volume × negativity")}
    </div>`;
}
```

- [ ] **Step 2: Update `web/js/nav.js` to mount dashboard charts**

At the top of `nav.js`, update the dashboard import (line 6):

```js
// Before:
import { dashboard } from "./views/dashboard.js";

// After:
import { dashboard, mountDashboardCharts } from "./views/dashboard.js";
```

Update the `render()` function to call `mountDashboardCharts()` after mounting standard charts:

```js
export function render() {
  const root = $("#viewRoot");
  root.innerHTML = (views[state.view] || (() => ""))();
  bindViewEvents();
  mountViewCharts(root, state.data);
  mountDashboardCharts();  // no-op unless state.view === "dashboard"
}
```

- [ ] **Step 3: Run full test suite**

```bash
pytest --tb=short -q 2>&1 | tail -10
```

Expected: all tests pass (including 4 new M3 tests).

- [ ] **Step 4: Manual smoke test**

```bash
python -m uvicorn app:app --port 8765 &
```

Open `http://localhost:8765` in a browser. Verify:
1. Dashboard tab loads without console errors.
2. "Signals to watch" section appears (or "Run analysis" notice if no trends data).
3. If trends data exists: hero card shows top cluster; 2-col grid below it.
4. Metric cards (Analyzed rows, Complaint rate, etc.) appear below the divider.
5. Sentiment / Signal flags / Complaint themes charts render.
6. Quadrant + Leaderboard panels appear (empty or populated depending on data).
7. Negative over time + Sentiment over time panels appear.
8. Priority map chart appears.
9. Evidence feed is gone from Dashboard.

```bash
kill %1
```

- [ ] **Step 5: Commit**

```bash
git add web/js/views/dashboard.js web/js/nav.js
git commit -m "feat(m3): signals-first dashboard — hero card, 2-col grid, sparklines, trend charts"
```

---

## Task 5 — Push and verify

- [ ] **Step 1: Run full test suite one final time**

```bash
pytest --tb=short -q 2>&1 | tail -15
```

Expected: all tests pass.

- [ ] **Step 2: Push to origin**

```bash
git push origin main
```

- [ ] **Done.** M3 is complete. Remaining milestones: M4 (Data Explorer — all 18 charts + filters + Q&A folded in + sparklines in cluster cards) → M5 (animation/a11y pass, full suite).
