# redditgm v2 — GM Signal Desk

**Demo:** [Google Drive video](https://drive.google.com/file/d/1TfbjPVLoxWn3EyOI2TwecMgoKOOJQX4f/view?usp=drive_link)

**Turn raw Reddit chatter about General Motors vehicles into prioritized, evidence-backed product and strategy signals.**

redditgm v2 is a self-contained FastAPI web app that runs one continuous workflow: collect Reddit posts and comments, classify every comment with an LLM, surface KPIs and complaint themes, detect rising/falling trends with statistical signals, answer natural-language questions grounded in the evidence, and export briefings as Markdown/PDF. Everything runs locally and writes durable artifacts under `runtime/`.

The UI brands itself **"GM Signal Desk."** Under the hood it's a durable, resumable, single-tag analysis pipeline.

> **Course project** — Built for **AI Methods for Social and Visual Data (25-26 M5)** by **Rico Pichardo Abreu, Eduardo Acevedo Figueroa, Daniel Jahanian, and Cielo Vasquez Mellado.** See [About this project](#about-this-project) for the business context and how this app relates to the original notebook MVP.

---

## Table of contents

- [About this project](#about-this-project)
- [Screenshots](#screenshots)
- [What it does](#what-it-does)
- [Architecture at a glance](#architecture-at-a-glance)
- [The five-tab workspace](#the-five-tab-workspace)
- [The analysis pipeline](#the-analysis-pipeline)
- [Classification schema](#classification-schema)
- [KPIs and how they are calculated](#kpis-and-how-they-are-calculated)
- [Trend signals: velocity and z-score](#trend-signals-velocity-and-z-score)
- [Trend clustering and confidence](#trend-clustering-and-confidence)
- [Time series (negative share over time)](#time-series-negative-share-over-time)
- [Q&A (retrieval-augmented answers)](#qa-retrieval-augmented-answers)
- [Reports and exports](#reports-and-exports)
- [Run it](#run-it)
- [Configuration](#configuration)
- [API reference](#api-reference)
- [Repository layout](#repository-layout)
- [Testing](#testing)

---

## About this project

This repository is a course project for **AI Methods for Social and Visual Data (25-26 M5)**, built by **Rico Pichardo Abreu, Eduardo Acevedo Figueroa, Daniel Jahanian, and Cielo Vasquez Mellado.**

**The problem.** General Motors doesn't have a data *capture* problem — it has a data *synthesis* problem. Customer complaints, quality issues, dealer feedback, and social chatter already exist, but they're scattered across systems, owned by different teams, and read differently depending on who's looking. Product Managers end up hunting across tools and stitching sources together instead of acting on what they find — *"like finding a needle in a haystack."* Most existing tooling is descriptive and reactive: it says what happened, not what changed or what to do next.

**The MVP idea.** Rather than replace every source system, this project proves a narrower claim: that an AI layer can take **one** high-value public source — Reddit — and turn it into a faster signal view for PMs. Reddit fits because the conversations are public, API-accessible, detailed, and naturally organized around specific vehicles, features, and owner experiences.

**Why Reddit needs a purpose-built tool.** The working dataset — roughly **2,867 unique posts and ~7,900 comments** across nine GM subreddits (Corvette, Silverado, Camaro, Cadillac, Chevy, GMC, Buick, GeneralMotors, Chevrolet) — has two structural problems that defeat manual review or keyword search:

- **~66% of posts have no body text.** They're an image, gallery, or outbound link, so the real product signal lives in the *comments*, not the post. That's why the pipeline classifies at the **comment level**, with the parent post supplied only as context.
- **Raw exports double-count.** One pull returns one row per comment with the parent post's title/score repeated on every row, so naive analysis over-weights popular posts and misattributes sentiment to the wrong unit of text. The pipeline dedupes and separates "post as context" from "comment as the unit being classified."

**From notebook MVP to this app.** The original prototype was a Jupyter notebook that classified posts, then comments, against a schema refined over four revisions (bare sentiment → eight binary flags → explicit post/comment separation → a structured GM-analyst schema with severity, vehicle, and comment-type fields). Model selection came from a structured comparison across **GPT-OSS-120B, Llama-4-Scout, and Gemma** — the lesson being that no single model's output is trustworthy until you have something to check it against. This repository is the **productionized evolution** of that notebook. The app is provider-agnostic (any OpenAI-compatible model via OpenRouter; it ships defaulting to `gpt-oss-120b`), and it closes several gaps the team's user testing flagged:

| User-testing gap (notebook MVP) | How this app addresses it |
|---|---|
| No traceability from a finding back to its source posts | Score-ranked **evidence cards** link every signal back to the source comment and Reddit permalink |
| Emergent themes outside the fixed taxonomy were missed | **FAISS clustering** surfaces themes beyond the predefined complaint categories |
| Output was descriptive ("what happened") only | **Velocity + z-score** trend signals show what's *changing* and what's *new vs. known* |
| No way to prioritize which results to trust | **Theme-level confidence** (deterministic cluster confidence + high/medium/low trend banners) and a **priority matrix** |

It also adds capabilities the original proposal didn't scope: a durable, resumable orchestration pipeline; natural-language **Q&A** grounded in the evidence (RAG); and Unicode-safe PDF briefings.

---

## Screenshots

All screenshots below are live captures of the app running against a real classified run (449 analyzed Reddit rows for the `gm_vehicle_on_demand` tag).

### Dashboard — signals first

KPI strip, LLM synthesis briefing, sentiment / signal-flag / complaint-theme charts, a "discovered from your data" signal explorer, the trend briefing, the **velocity × z-score quadrant**, the trend leaderboard, and truthful time-series previews.

![Dashboard](docs/screenshots/01-dashboard.png)

### Data Explorer — every chart plus the evidence

The full 18-chart analytics grid (sentiment, severity, complaint themes, EV vs. non-EV, competitor breakdown, per-vehicle and per-subreddit detail, priority scatter, stacked-bar model comparisons, and lazy-loaded heatmaps) followed by paginated, score-ranked evidence cards that link back to the source comment.

![Data Explorer](docs/screenshots/03-data-explorer.png)

### Data Gathering — collect or upload, then analyze

Upload a collector CSV or pull live from Reddit using a named subreddit list, set posts-per-subreddit and comments-per-post depth, then run the full pipeline with one **Analyze data** click.

![Data Gathering](docs/screenshots/04-data-gathering.png)

### Q&A — ask your evidence

Natural-language questions answered from the current classified evidence via FAISS retrieval. The index is built automatically by the pipeline; live querying needs an LLM API key (the screenshot shows the honest "index built, key required" state).

![Q&A](docs/screenshots/02-qa.png)

### Settings — provider, tuning, and prompts

API key / provider / model, collection defaults, analysis tuning (k-means cluster count, classify limit, Q&A top-k), the editable classification / synthesis / cluster-labeling prompts, and a **Pipeline runs** sub-tab with per-step status, logs, and retries.

![Settings](docs/screenshots/05-settings.png)

---

## What it does

1. **Collect** additive, duplicate-safe Reddit data via an external collector, **or** upload a collector CSV you already have.
2. **Classify** every comment with an LLM against an 18-field GM-specific schema (sentiment, complaint, severity, vehicle, competitor, EV topic, and more). A deterministic keyword heuristic is the fallback when no key is set.
3. **Summarize** the corpus into headline KPIs, complaint themes, EV vs. non-EV differences, competitor signal, and a priority matrix.
4. **Detect trends** by clustering comments into themes, then scoring each theme's momentum with two independent statistical signals (velocity and z-score) plus an agreement-based confidence banner.
5. **Answer questions** in natural language, grounded in the classified evidence (RAG over a FAISS index).
6. **Export** an LLM strategy briefing, a trend briefing, classified CSVs, chart bundles, and Unicode-safe PDFs.

The app is intentionally **single-tag and additive**: collection depth comes from accumulating duplicate-safe runs over time, not from a lookback window.

---

## Architecture at a glance

```
┌─────────────────────────────────────────────────────────────────────┐
│  Browser SPA (web/)                                                   │
│  Vanilla JS modules + ECharts. Five tabs, no build step.             │
│  index.html · js/views/*.js · js/charts.js · js/api.js · js/state.js │
└───────────────────────────────┬─────────────────────────────────────┘
                                 │  JSON over HTTP (/api/*)
┌───────────────────────────────┴─────────────────────────────────────┐
│  FastAPI app (app.py)                                                 │
│  Endpoints for run snapshot, evidence, charts, collect, classify,    │
│  trends, Q&A, exports, and pipeline orchestration.                   │
└───────────────────────────────┬─────────────────────────────────────┘
                                 │
        ┌────────────────────────┼────────────────────────────┐
        │                        │                            │
┌───────┴────────┐   ┌───────────┴───────────┐   ┌────────────┴─────────┐
│ Analytics (src)│   │ Orchestrator (src)    │   │ Workers (scripts/)   │
│ gm_insights    │   │ run_store (SQLite)    │   │ analyze_run drives:  │
│ trend_insights │   │ jobs (file status)    │   │ prepare→classify→    │
│ timeseries     │   │ run record + steps +  │   │ briefing→synthesis→  │
│ charts         │   │ attempts + tag locks  │   │ trends→trend_pdf→    │
│ qa_retrieval   │   │ atomic reservation    │   │ qa_index             │
│ briefing       │   │ heartbeats + retry    │   │ (each step a process)│
│ pdf_export     │   └───────────────────────┘   └──────────────────────┘
└────────────────┘
                                 │
                    ┌────────────┴─────────────┐
                    │  runtime/<tag>/...        │
                    │  classified · trends · qa │
                    │  reports · downloads      │
                    │  generations/ (published) │
                    │  runs.db (SQLite state)   │
                    └───────────────────────────┘
```

**Key design choices**

- **Durable orchestration.** Every pipeline run is a row in a SQLite database (`runtime/runs.db`) with per-step state, attempt history, atomic run reservation, heartbeats, and a per-tag lock. Runs survive crashes and are resumable/retryable from any step. A worker that goes silent past its liveness budget is reconciled, not left hanging. See `src/run_store.py` and `scripts/analyze_run.py`.
- **Generation-aware publishing.** Completed artifacts are published into immutable `generations/<run-id>-<fingerprint>/` folders. The dashboard, downloads, and Q&A all read the latest *published* generation, so a failed retry never corrupts what's already on screen.
- **Truthful-by-default analytics.** Rates (not raw counts) are the primary measure, so collecting more data doesn't masquerade as a rising trend. Small samples are suppressed (`MIN_CELL = 5`) or labeled directional rather than conclusive.
- **No build step.** The frontend is plain ES modules and a vendored ECharts. Open the page, it talks to `/api/*`.

---

## The five-tab workspace

The information architecture is **analysis first, operations in Settings**:

| Tab | Purpose |
|-----|---------|
| **Dashboard** | The signals-first overview: KPI strip, synthesis briefing, headline charts, a discovered-signal explorer, the trend briefing, the velocity × z-score quadrant, the trend leaderboard, and time-series previews. |
| **Q&A** | Ask natural-language questions answered from the classified evidence (FAISS retrieval + LLM). |
| **Data Explorer** | The full 18-chart analytics grid plus paginated, score-ranked evidence cards with filters (sentiment, vehicle, subreddit, severity, comment type, competitor, date range, free-text search, min score). |
| **Data Gathering** | Upload a CSV or collect from Reddit (named subreddit lists, posts/comments depth), then launch the full pipeline. |
| **Settings** | API key / provider / model, collection and analysis tuning, editable prompts, and a **Pipeline runs** sub-tab with live per-step logs and retries. |

---

## The analysis pipeline

One **Analyze data** click runs an ordered, dependency-aware pipeline. Each step is its own process so a single hung LLM call can't freeze the rest, and each step is independently retryable.

```
prepare ──▶ classify ──┬─▶ briefing ──▶ synthesis_pdf
                       ├─▶ trends   ──▶ trend_pdf
                       └─▶ qa_index
```

(`STEP_ORDER` and `DEPENDENCIES` in `scripts/analyze_run.py`)

| Step | What it does | Produces |
|------|--------------|----------|
| **prepare** | Loads the source frame, normalizes Reddit columns, flags junk/ineligible rows, merges any already-classified labels. | A working classified frame in the run's staging dir. |
| **classify** | Sends each eligible comment to the LLM (or the heuristic fallback), writes the 18-field label per row. Per-row LLM calls are time-bounded with a heartbeat so a stalled provider can't stall the worker. | `classified/classified_posts.csv` |
| **briefing** | Builds the summary payload and asks the LLM for a multi-section markdown strategy briefing (deterministic fallback if no key). | `reports/` markdown |
| **synthesis_pdf** | Renders the briefing to a Unicode-safe PDF. Non-fatal: a PDF failure still keeps the valid Markdown and marks the run `completed_with_warnings`. | `reports/` PDF |
| **trends** | Embeds comments, clusters them (k-means), labels each cluster with the LLM, and computes velocity / z-score / agreement signals. | `trends/clusters.json`, `cluster_labels.json`, `trend_signals.json`, `faiss.index`, … |
| **trend_pdf** | Renders the trend briefing to Markdown + Unicode PDF. | `downloads/` trend report |
| **qa_index** | Builds the FAISS Q&A index over the classified evidence. | `qa/index.faiss`, `qa/docs.json`, … |

---

## Classification schema

Each comment is classified into this exact JSON schema (the original Reddit post is supplied as context, but the **comment itself** is what's classified). The system prompt lives in `src/gm_insights.py` (`CLASSIFICATION_SYSTEM_PROMPT`) and is editable in **Settings → Prompts**.

**Ten binary flags (`1`/`0`):**

`complaint` · `competitor_mention` · `dealer_experience` · `reliability_concern` · `software_tech_issue` · `purchase_intent` · `loyalty_signal` · `ev_topic` · `enthusiast_mod` · `classic_vintage`

**Categorical fields:**

- `sentiment` — `positive` / `neutral` / `negative`
- `issue_severity` — `critical` / `major` / `minor` / `none`
- `comment_type` — `complaint` / `advice_recommendation` / `shared_experience` / `question` / `praise` / `comparison` / `off_topic_noise`
- `vehicle_mentioned` — one of ~30 GM nameplates (Silverado, Sierra, Tahoe, Corvette, Equinox EV, Lyriq, Escalade, …) or `other_gm` / `unknown`
- `competitor_brand` — `ford` / `toyota` / `ram` / `tesla` / `honda` / `hyundai_kia` / `nissan` / `jeep` / `other` / `none`
- `top_complaint_category` and `multi_complaint_categories` — from a fixed list of 12 GM-relevant categories: transmission/torque-converter, engine lifter/AFM/DOD, electrical/battery/alternator, software/infotainment/OTA, AC/HVAC, suspension/steering, rust/body/paint, dealership/service wait, pricing/fees/financing, subscription model, parts availability/backorder, recall/warranty/lemon law (plus `other` / `not_applicable`).
- `description` — a one-line, actionable insight for GM.

**Heuristic fallback.** When no API key is set, a deterministic keyword classifier (`heuristic_label`) assigns sentiment, complaint flags, vehicle/competitor inference, and severity so the pipeline always produces a result. Classified rows record a `classifier_mode` so the analytics layer can tell real classification from skipped/errored rows.

---

## KPIs and how they are calculated

Every percentage below is computed by one helper:

```python
percent(numerator, denominator) = round(numerator / denominator * 100, 1)   # 0.0 if denominator == 0
```

…and every rate is over **analyzed rows**, not raw rows. An *analyzed row* (`analyzed_frame` in `src/gm_insights.py`) is one that:

- was **not** flagged `skip_classification` (junk: deleted/removed or under 15 characters),
- actually went through a classifier (`classifier_mode` is set), and
- has a real sentiment (not `skipped` / `error` / empty).

`score_norm` is the Reddit score (net upvotes) of the comment, used for engagement weighting and evidence ranking.

### Headline KPI strip (Dashboard)

`summary_metrics()` returns:

| KPI | Formula |
|-----|---------|
| **Analyzed rows** | count of analyzed rows (of total source rows) |
| **Complaint rate** | `complaints / analyzed × 100` where `complaint == 1` |
| **Negative rate** | `negative / analyzed × 100` where `sentiment == "negative"` |
| **Competitor rate** | `competitor_mentions / analyzed × 100` where `competitor_mention == 1` |
| **EV rate** | `ev_topic / analyzed × 100` where `ev_topic == 1` |

In the live screenshot above: 449 analyzed rows, 18.3% complaint rate, 12.7% negative, 2.4% competitor, 8.0% EV.

### Engagement level

`add_engagement_level()` ranks every analyzed row by `score_norm` and splits it into three equal-size buckets (`pandas.qcut` terciles): **low / medium / high**. Needs at least 3 non-null scores, otherwise every row is `unknown`. This is a *relative* measure within the current dataset, not an absolute upvote threshold.

### Priority matrix (what to fix first)

`priority_matrix()` groups complaint rows by `top_complaint_category` and computes, per theme:

- `volume` — number of complaints,
- `pct_negative` — share of those complaints with negative sentiment,
- `avg_score` — mean Reddit score,
- `critical_count` — number tagged `critical` severity.

It then takes the **median volume** and **median pct_negative** across themes and bins each theme into a 2×2 quadrant:

| | High volume (≥ median) | Low volume (< median) |
|---|---|---|
| **High negativity (≥ median)** | **Fix now** | **Monitor** |
| **Low negativity (< median)** | **Watch** | **Low priority** |

This is the colored scatter on the Dashboard and Data Explorer (volume on x, % negative on y).

### Engagement-weighted themes

`engagement_weighted_themes()` ranks complaint themes by the **sum of `score_norm`** across their complaints, not by raw count — so a theme with a few highly-upvoted comments can outrank a noisier one. Themes with fewer than 3 complaints are dropped.

### Per-segment breakdowns

The same `count → complaint_rate → negative_rate` pattern is computed per **vehicle** (`vehicle_breakdown`), per **subreddit** (`subreddit_breakdown`), per **competitor brand** (`competitor_breakdown_detail`), and for **EV vs. non-EV** (`ev_comparison`, which also reports reliability-concern, software-issue, and competitor-mention rates side by side).

### Small-sample suppression

`MIN_CELL = 5`. Any chart that cross-tabs noisy dimensions (vehicle × category, vehicle × severity, per-subreddit, per-vehicle rates) hides cells or whole rows below this threshold and shows a "chart suppressed" note, so a 1-of-2 sample never renders as "50%."

---

## Trend signals: velocity and z-score

Trend detection runs **per cluster** (theme). After comments are clustered (next section), each cluster's timestamps are scored with two **independent** momentum signals. They're independent on purpose: when both agree, confidence is high; when they diverge, the app says so rather than picking a winner. Source: `src/trend_insights.py`.

> **"Now" is data-relative.** Both signals define "now" as the most recent timestamp *in that cluster*, not wall-clock time. This keeps the math honest on additive datasets that may not include today.

### Velocity — is the posting rate accelerating?

Velocity compares the **recent daily posting rate** against the **baseline daily rate**.

```
recent window   = last 7 days        (VELOCITY_RECENT_DAYS)
baseline window = the 30 days before that  (VELOCITY_BASELINE_DAYS)

recent_rate   = recent_count   / 7
baseline_rate = baseline_count / 30

velocity = (recent_rate − baseline_rate) / baseline_rate
```

Interpretation: `velocity = +0.5` means the recent rate is 50% above baseline; `−0.3` means 30% below.

**Direction:** `rising` if `velocity > 0.2`, `falling` if `velocity < −0.2`, else `stable`.

**Validity guardrails** (so tiny samples don't produce fake momentum):

- Empty cluster or no recent/baseline posts → `insufficient_data`, `valid = false`.
- Baseline has fewer than 2 posts → returns a **direction only** (`rising`/`falling`), `valid = false` — too little history to trust a ratio.
- No posts in the recent window → `falling`, directional only.
- A numeric velocity is marked `valid = true` only when `recent_count ≥ 2` **and** `baseline_count ≥ 3`.

### Z-score — is the latest period unusually busy?

The z-score asks how far the **most recent weekly period** sits from the cluster's own historical average, measured in standard deviations.

```
period = 7 days

1. Bucket the cluster's timestamps into consecutive weekly periods
   from earliest → now.
2. period_counts = [posts in week 1, posts in week 2, … , posts in last week]
3. mean = average(period_counts), std = standard deviation(period_counts)
4. zscore = (last_period_count − mean) / std
```

Interpretation: `zscore = +2.0` means the latest week is two standard deviations above the cluster's normal weekly volume — a genuine spike.

**Direction:** `rising` if `zscore > 1.0`, `falling` if `zscore < −1.0`, else `stable`.

**Validity guardrails:**

- Fewer than 2 periods → `insufficient_data`.
- A z-score is `valid = true` only when there are at least **4 weekly periods** (`MIN_PERIODS`); with 2–3 periods it's returned but flagged `directional_only`.
- Zero variance (uniform activity) → `zscore = 0`, direction `stable`.

### Agreement and the confidence banner

`trend_confidence_banner()` combines the two signals into a single, human-readable confidence label that drives the colored banner on each trend card:

| Condition | Banner | Meaning |
|-----------|--------|---------|
| Both valid **and** same direction | **High** | "Both velocity and z-score agree: rising/falling activity." |
| One valid (or partial agreement) | **Medium** | e.g. "Velocity shows rising. Too few time periods for z-score reliability." |
| Directions diverge (one rising, one falling) | **Low** | "Signals diverge — treat with caution." |
| Neither usable | **Low** | "Insufficient data for reliable trend detection." |

This is what powers the **velocity × z-score quadrant** and the **trend leaderboard** on the Dashboard.

---

## Trend clustering and confidence

Before signals can be computed, comments are grouped into themes. The clustering pipeline (`run_clustering` in `src/trend_insights.py`):

```
embed → KMeans → FAISS → label (LLM) → deterministic confidence
```

1. **Embed.** Build a short, classification-rich string per row (`description | category | vehicle | sentiment`) and embed it via an OpenAI-compatible embeddings endpoint (`text-embedding-3-small`, 1536-dim, batched).
2. **Cluster.** Run **k-means** (default 10 clusters, capped by distinct vectors and dataset size; configurable in Settings).
3. **Index.** Build a **FAISS** `IndexFlatIP` over L2-normalized embeddings, so inner product = **cosine similarity**.
4. **Representatives.** For each cluster, find the comments nearest the centroid (most characteristic), the highest-engagement comments, and the most recent ones.
5. **Label.** Ask the LLM for a structured label per cluster: `short_label`, `detailed_label`, `theme_type`, `confidence`, `rationale`, `label_risk`.

### Deterministic confidence (don't trust the model's self-rating)

LLMs are over-confident, so `_deterministic_confidence()` recomputes a confidence score from **objective** cluster-quality signals and downgrades the model when warranted:

```
coherence        = average cosine similarity of members to the cluster centroid (0–1)
category_dominance = share of complaint members in the cluster's top category
size_score       = clamp((cluster_size − 5) / 15, 0, 1)      # rewards larger clusters

det_score = 0.4·coherence + 0.3·category_dominance + 0.3·size_score
```

**Caps applied to the model's stated confidence:**

- `cluster_size < 5` → forced to **low** (too small to trust),
- `det_score < 0.35` → **low**,
- `det_score < 0.55` and the model said "high" → downgraded to **medium**.

So a tight, on-theme, reasonably large cluster keeps a high label; a small, incoherent one is automatically demoted regardless of what the LLM claimed.

---

## Time series (negative share over time)

`build_timeseries()` (`src/timeseries.py`) produces the truthful trend lines on the Dashboard. The **primary measure is negative share, not volume** — so collecting more comments doesn't look like rising negativity.

```
negative_share_pct[bucket] = negative[bucket] / total[bucket] × 100
```

- **Adaptive granularity:** daily buckets when the data spans **< 14 days**, otherwise Monday-based **weekly** buckets.
- **UTC, zero-filled:** buckets are built across the full span in UTC and empty buckets are filled with zero so gaps are visible.
- **Honest refusal:** if all timestamps fall on a single calendar day, or there are fewer than 2 non-empty buckets, the endpoint returns a structured `ok: false` with a reason code instead of drawing a misleading line.
- Also returns per-bucket positive / neutral / total counts and per-cluster volume, plus a caveat reminding you to read shares alongside bucket totals.

---

## Q&A (retrieval-augmented answers)

`src/qa_retrieval.py` implements RAG over the classified evidence:

```
build docs → embed → FAISS index → (query) embed → cosine search → LLM answer
```

- **Index** (`build_qa_index`): one document per classified comment with its text and metadata, embedded and stored in a FAISS `IndexFlatIP` (L2-normalized → cosine). Built automatically by the `qa_index` pipeline step.
- **Retrieve** (`retrieve`): embed the question, search the index, return the top-`k` comments (default `qa_k = 8`, configurable) each with a `retrieval_score` (cosine similarity).
- **Answer** (`answer_question`): format the hits into a capped context block (with vehicle / sentiment / category / subreddit / permalink / similarity per hit) and ask the LLM to answer **only** from that evidence.
- **Generation-aware:** Q&A is tied to the published generation's fingerprint, so answers always reflect the current classified data and the index is flagged stale when the data changes.

Live querying needs an LLM API key for the query embedding (the Q&A screenshot shows the "index built, key required" state).

---

## Reports and exports

| Output | How it's built | Download |
|--------|----------------|----------|
| **Strategy briefing** | `summary_payload` → LLM synthesis prompt → multi-section Markdown (deterministic `fallback_synthesis` when no key). | `/api/download/report` (MD), `/api/download/briefing-pdf` |
| **Synthesis PDF** | Unicode-safe render of the briefing; non-fatal step. | `/api/download/briefing-pdf` |
| **Trend briefing** | Cluster labels + signals → Markdown + PDF. | `/api/download/trend-md`, `/api/download/trend-pdf` |
| **Classified data** | The full labeled CSV. | `/api/download/classified` |
| **Source data** | Combined / per-source collector CSVs, zipped. | `/api/download/source` |
| **Charts** | Chart data bundle. | `/api/download/charts` |

PDFs use Unicode fonts so non-ASCII content (em dashes, accented names) renders correctly.

---

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

Open <http://127.0.0.1:8000>. The app writes all local artifacts under `runtime/<tag>/` and tracks orchestration state in `runtime/runs.db`.

### LLM keys

Classification, synthesis, clustering labels, embeddings, and Q&A all use an OpenAI-compatible endpoint. Set one of:

```bash
export OPENROUTER_API_KEY="..."     # default provider (openrouter)
export OPENAI_API_KEY="..."
```

You can also paste a temporary key in **Settings → API & Provider** (it's kept in `runtime/secrets.json`, never committed). With no key, classification falls back to the keyword heuristic, the briefing falls back to a deterministic template, and clustering / Q&A (which need embeddings) are unavailable.

### Collector bridge

Live Reddit collection uses an **external** duplicate-safe collector as an explicit dependency. Point the app at its repo with either the `REDDITGM_LEGACY_ROOT` environment variable or `collection.legacy_root` in `config.json`. If neither is set, collection is disabled and `/api/health` reports that state — there's no machine-specific fallback.

Collection is **additive**: depth accumulates across duplicate-safe runs rather than from a lookback window. The web UI deliberately does **not** expose a `since_days` control (the backend still accepts it only for legacy direct-API clients).

### Runtime root override

Set `REDDITGM_RUNTIME_ROOT` to point the app at a different runtime directory (used, for example, to render these screenshots against a copy of real data without touching it).

---

## Configuration

`config.json` (and live overrides from Settings, persisted to `runtime/`):

```jsonc
{
  "provider": {
    "name": "openrouter",                     // openrouter | openai
    "base_url": "https://openrouter.ai/api/v1",
    "model": "gpt-oss-120b",                  // classify / briefing / labels
    "embedding_model": "text-embedding-3-small" // clustering + Q&A vectors
  },
  "collection": {
    "default_tag": "gm_vehicle_on_demand",    // the single workspace tag
    "legacy_root": "",                        // path to the external collector repo
    "listing_limit": 100,                     // posts per subreddit
    "comments_limit": 5                       // top comments per post
  },
  "analysis": {
    "n_clusters": 10,                         // k-means cluster count
    "classify_limit": 0,                      // 0 = classify all pending rows
    "qa_k": 8                                 // Q&A retrieval depth (top-k)
  },
  "prompts": {
    "classification": "…",                    // editable in Settings → Prompts
    "synthesis": "…",
    "cluster_label": "…"
  }
}
```

The three prompts are editable in the UI and take effect on the next run; a **Validate prompt** / **Reset to default** control checks the cluster-label prompt's required placeholders and JSON output contract before saving.

---

## API reference

Selected endpoints (full set in `app.py`):

**Run / analytics**
- `GET /api/health` — provider, collector, and runtime status
- `GET /api/config`, `PATCH /api/config` — read / update configuration
- `GET /api/run?tag=…` — the dashboard snapshot (metrics, chart specs + data, filter options)
- `GET /api/charts/detail?tag=…` — lazy heavy charts (category-by-model heatmap, co-occurrence)
- `GET /api/evidence?tag=…` — paginated, score-ranked evidence rows

**Collect / classify**
- `POST /api/upload` — upload a collector CSV
- `POST /api/collect`, `GET /api/collect/status` — live Reddit collection
- `POST /api/classify/preview`, `POST /api/classify/job`, `GET /api/classify/status`
- `GET /api/subreddit-lists`, `POST /api/subreddit-lists`, `PUT /api/subreddit-lists/{id}` — named subreddit lists

**Pipeline orchestration**
- `POST /api/analyze` — run the full ordered pipeline
- `GET /api/pipeline/status?tag=…&run_id=…` — per-step status, attempts, artifact URLs
- `GET /api/pipeline/log` — tail of a step's log; retry endpoints for individual steps

**Trends**
- `POST /api/trends/run`, `GET /api/trends/status`, `GET /api/trends` — clustering + signals
- `GET /api/trends/timeseries` — truthful UTC negative-share series
- `POST /api/trends/briefing`, `GET /api/trends/briefing/status`

**Q&A**
- `POST /api/qa/build-index`, `GET /api/qa/status`
- `POST /api/qa/search`, `POST /api/qa/answer`

**Exports**
- `GET /api/download/classified | source | report | charts | briefing-pdf | trend-pdf | trend-md`
- `POST /api/export/save`, `GET /api/reports/preview`

---

## Repository layout

```
app.py                     FastAPI app: all HTTP endpoints + request orchestration
run.py                     Convenience launcher
config.json                Provider, collection, analysis, and prompt defaults
requirements.txt

src/
  gm_insights.py           Classification schema, heuristic + LLM labeling, all KPI/aggregation math
  trend_insights.py        Clustering (embed→KMeans→FAISS→label) + velocity/z-score/confidence
  timeseries.py            Adaptive UTC negative-share time series
  charts.py                CHART_SPECS (18 charts) + chart data builders
  qa_retrieval.py          FAISS RAG: build index, retrieve, answer
  briefing.py              Strategy briefing assembly
  pdf_export.py            Unicode-safe PDF rendering
  run_store.py             SQLite orchestrator: runs, steps, attempts, locks, reservation
  jobs.py                  File-based job status + reconciliation
  app_config.py            Config load/merge + prompt helpers
  subreddit_lists.py       Named subreddit-list store
  trend_report.py          Trend briefing / report model

scripts/
  analyze_run.py           Pipeline driver: STEP_ORDER, DEPENDENCIES, retry logic
  classify_job.py · trend_job.py · briefing_job.py · faiss_qa_job.py
  synthesis_pdf_job.py · trend_briefing_job.py · pdf_export_job.py

web/
  index.html               SPA shell (five tabs)
  styles.css
  js/views/*.js            dashboard · qa · explore · gathering · settings · trends · pipeline · …
  js/charts.js js/api.js js/state.js js/nav.js js/components.js
  vendor/echarts.min.js

docs/
  screenshots/             the live captures referenced above
  redesign/ · superpowers/ design specs and implementation plans

tests/                     pytest suite (unit + live-server Playwright/Chromium browser tests)
runtime/                   generated artifacts + runs.db (git-ignored)
```

---

## Testing

The suite mixes unit tests with live-server browser tests that boot a real uvicorn instance and drive Chromium via Playwright (`tests/conftest.py` provides the `live_server` and `browser_page` fixtures).

```bash
.venv/bin/pytest -q                       # full suite
.venv/bin/pytest tests/test_plan_b_browser.py -q   # browser/UI contracts
```

Playwright's Chromium build is required for the browser tests:

```bash
.venv/bin/python -m playwright install chromium
```
