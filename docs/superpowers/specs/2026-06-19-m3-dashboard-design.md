# M3 Dashboard Redesign — Design Spec

**Date:** 2026-06-19
**Status:** Approved
**Milestone:** M3 (follows M2 — orchestrator + Pipeline tab, merged to main 5b8e633)

---

## What this is

The Dashboard is the landing tab. Right now it shows a generic run summary (5 metric cards + 3 charts). M3 makes it signals-first: the moment you open the app, the top section answers "what should I pay attention to right now?" — which topics are accelerating — before you have to go digging.

---

## Layout (top to bottom)

### 1. Signals section

**Hero card (always visible)**
- Shows the single highest-priority cluster — defined as: rising direction + highest confidence first, then tiebreak by cluster size.
- If no clusters are rising: shows the largest cluster by size, with a banner note: "No accelerating signals detected — sorted by volume."
- Displays: cluster label, direction arrow, confidence badge (HIGH / MED / LOW), velocity %, cluster size (post count), confidence note text.

**2-column signal list (remaining clusters)**
- Default sort: rising HIGH → rising MED → rising LOW → stable → falling → N/A (no timestamp data).
- When no rising signals: sorted by cluster size descending.
- Each row shows: direction arrow, cluster label, velocity % (or "stable" / "no data"), confidence badge, sparkline (see below).

**Sparklines (inline, in each signal row)**
- Tiny weekly volume line, one per cluster. Shows the shape of the trend at a glance (spike vs. steady climb vs. fading).
- Requires the same `/api/trends/timeseries` data as the time-series charts (per-cluster weekly bucket).
- If no timestamp data: sparkline slot is empty (no error shown).

**Empty state (no trends run yet)**
- Signals section shows: "Run analysis to see trend signals." Hero card is hidden; 2-col list is hidden.

---

### 2. Run summary (5 metric cards — unchanged)

Analyzed rows · Complaint rate · Negative rate · Competitor signal · EV topic mix.
Already implemented in `components.js → metricGrid()`. No changes.

---

### 3. Analysis charts

**Existing (3 charts, unchanged):**
- Sentiment distribution (bar)
- Signal flags (bar)
- Complaint themes (bar)

**New trend charts (4 charts):**
- Velocity × Z-score quadrant (scatter) — each cluster as a dot; x = velocity, y = z-score; color = confidence. Top-right quadrant = rising + statistically unusual = act on these.
- Trend leaderboard (horizontal bar) — clusters ranked by velocity descending, color = confidence.
- Negative mentions over time (line) — weekly negative-sentiment post count.
- Sentiment over time (stacked area) — weekly positive / neutral / negative volumes.

**Existing chart that stays:**
- Priority map (scatter: volume × negativity) — stays on Dashboard. Shows which topics are both large AND negative.

**Existing panel that moves:**
- Evidence feed (last 8 raw Reddit rows) — removed from Dashboard, deferred to M4 Data Explorer.

**Chart layout:**
- Row 1: Sentiment distribution · Signal flags · Complaint themes (3 columns)
- Row 2: Velocity × Z-score quadrant · Trend leaderboard (2 columns)
- Row 3: Negative mentions over time · Sentiment over time (2 columns)
- Row 4: Priority map (1 column, left-aligned — half-width, consistent with Row 2/3)

---

## Data sources

| Data | Source | When fetched |
|------|--------|--------------|
| Run summary + existing charts | `GET /api/run` | App init (parallel) |
| Trend signals (signals panel + quadrant + leaderboard + sparklines) | `GET /api/trends` | App init (parallel) |
| Time-series buckets (time charts + sparklines) | `GET /api/trends/timeseries` | App init (parallel) |

All three fetches fire in parallel inside `loadRun()` in `app.js`. Results stored in `state.data`, `state.trendsData`, `state.timeseriesData`.

---

## New backend endpoint

**`GET /api/trends/timeseries?tag=`**

Reads the classified CSV for the given tag. Groups rows by ISO week. Returns:

```json
{
  "ok": true,
  "buckets": ["2025-W01", "2025-W02", "..."],
  "negative": [12, 18, "..."],
  "positive": [40, 35, "..."],
  "neutral": [30, 28, "..."],
  "by_cluster": {
    "0": [5, 8, 3, "..."],
    "1": [2, 1, 4, "..."]
  }
}
```

- `buckets` — ISO week strings, one per period.
- `negative / positive / neutral` — weekly post counts by sentiment label across all clusters.
- `by_cluster` — weekly post counts per cluster ID (used for sparklines).
- Returns `{ "ok": false, "detail": "..." }` if: no classified file, no timestamp column, or fewer than 2 weeks of data.
- Granularity: **weekly** (daily is too noisy for typical GM dataset spans).

---

## Frontend changes

**`web/js/app.js`** — `loadRun()` fires three parallel fetches; stores results; calls `render()` once all three settle (Promise.allSettled so one failure doesn't block the others).

**`web/js/state.js`** — add `timeseriesData: null` field.

**`web/js/views/dashboard.js`** — rewrite:
- `signalHero(trendsData)` — renders hero card or fallback.
- `signalGrid(trendsData, timeseriesData)` — renders 2-col list with inline sparklines.
- `dashboard()` — assembles all sections; gracefully degrades if any data source is null.

**`web/js/charts.js`** — add four new spec-to-ECharts renderers:
- `quadrant` — scatter, four-quadrant reference lines at x=0, y=0.
- `leaderboard` — horizontal bar, sorted by velocity desc.
- `line_timeseries` — line chart from `buckets` + one series array.
- `stacked_area_timeseries` — stacked area from `buckets` + negative/neutral/positive arrays.

Sparklines are rendered as tiny ECharts instances (80×32px, no axes, no tooltip) directly in each signal row, not via the `chartPanel()` component. ECharts is already loaded globally so no extra dependency. The frontend looks up per-cluster data as `timeseriesData.by_cluster[String(cluster.cluster_id)]` (string key, since JSON keys are always strings).

---

## Empty / degraded states

| Condition | What the user sees |
|-----------|-------------------|
| No pipeline run | Signals section: "Run analysis to see trend signals" |
| No timestamp data | Hero card shows top-by-size with note; sparklines hidden; time charts show "Date data required" |
| No classified data | Existing full-page empty state (unchanged) |
| `/api/trends/timeseries` fails | Time charts + sparklines silently hidden; rest of Dashboard renders normally |

---

## Tests (4 new)

1. `GET /api/trends/timeseries` — returns correct weekly buckets and per-cluster counts from a fixture CSV with timestamps.
2. `GET /api/trends/timeseries` — returns `ok: false` when the classified file has no timestamp column.
3. Dashboard signals panel — 10 clusters render in correct sort order (rising HIGH first, N/A last).
4. Dashboard signals panel — renders fallback hero card and "sorted by volume" note when no clusters have rising direction.

---

## Out of scope for M3

- Sparklines in Data Explorer (M4)
- Evidence feed (M4)
- Dark mode
- Any changes to trend signal math or thresholds
