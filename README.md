# redditgm v2 — GM Signal Desk

**Demo:** [Google Drive video](https://drive.google.com/file/d/1TfbjPVLoxWn3EyOI2TwecMgoKOOJQX4f/view?usp=drive_link)

**Turn raw Reddit chatter about General Motors vehicles into prioritized, evidence-backed product signals.**

GM Signal Desk is a web app that runs one continuous workflow: collect Reddit posts and comments, use an AI model to label every comment, surface the headline numbers and complaint themes, flag which themes are heating up, and let you ask plain-English questions answered straight from the evidence. You can export the whole thing as a written briefing or PDF.

> **Course project** — Built for **AI Methods for Social and Visual Data (25-26 M5)** by **Rico Pichardo Abreu, Eduardo Acevedo Figueroa, Daniel Jahanian, and Cielo Vasquez Mellado.**

---

## Table of contents

- [About this project](#about-this-project)
- [Screenshots](#screenshots)
- [What's in each tab](#whats-in-each-tab)
- [What the numbers mean](#what-the-numbers-mean)
- [How trend detection works (velocity and z-score)](#how-trend-detection-works-velocity-and-z-score)
- [Reports you can export](#reports-you-can-export)
- [Run it](#run-it)

---

## About this project

**The problem.** General Motors doesn't have a data *capture* problem — it has a data *synthesis* problem. Customer complaints, quality issues, dealer feedback, and social chatter already exist, but they're scattered across systems and read differently depending on who's looking. Product Managers end up hunting across tools and stitching sources together instead of acting on what they find — *"like finding a needle in a haystack."* Most existing tooling is descriptive: it says what happened, not what changed or what to do next.

**The idea.** Rather than replace every system, this project proves a narrower claim: that an AI layer can take **one** high-value public source — Reddit — and turn it into a faster signal view for PMs. Reddit fits because the conversations are public, detailed, and naturally organized around specific vehicles, features, and owner experiences.

**Why Reddit needs a purpose-built tool.** The working dataset — roughly **2,867 posts and ~7,900 comments** across nine GM subreddits (Corvette, Silverado, Camaro, Cadillac, Chevy, GMC, Buick, GeneralMotors, Chevrolet) — has two problems that defeat manual review or keyword search:

- **~66% of posts have no text.** They're an image, gallery, or link, so the real product signal lives in the *comments*, not the post. The tool reads at the **comment level**, using the original post only as context.
- **Raw exports double-count.** One download repeats the parent post's title and score on every comment row, so naive analysis over-weights popular posts. The tool dedupes and treats the comment — not the post — as the thing being analyzed.

**From notebook to app.** The original prototype was a notebook that classified comments against a GM-specific labeling scheme, refined over four rounds, with the model chosen by comparing GPT-OSS-120B, Llama-4-Scout, and Gemma against the same sample. This repository is the productionized version of that notebook, and it closes the gaps our user testing flagged:

| Gap in the notebook prototype | How this app fixes it |
|---|---|
| No way to trace a finding back to its source | Every signal links to the exact source comment and Reddit permalink |
| Themes outside the fixed category list got missed | Comments are grouped into themes automatically, surfacing patterns we never predefined |
| Output was purely descriptive | Velocity and z-score signals flag what's *changing* and what's *new vs. already known* |
| No way to know which results to trust | Each theme gets a confidence rating and a priority ranking |

It also adds things the original proposal never scoped: natural-language Q&A grounded in the evidence, and exportable PDF briefings.

---

## Screenshots

Live captures of the app running on a real classified run (449 analyzed Reddit comments).

### Dashboard — the signals, first

Headline numbers, an AI-written briefing, the main charts, a "discovered from your data" theme explorer, the velocity × z-score quadrant, a trend leaderboard, and trend lines over time.

![Dashboard](docs/screenshots/01-dashboard.png)

### Q&A — ask your evidence

Type a question and get an answer written only from the classified Reddit comments, with the source comments listed below it (each ranked by how closely it matches the question).

![Q&A](docs/screenshots/02-qa.png)

### Data Explorer — every chart plus the receipts

The full chart grid (sentiment, severity, complaint themes, EV vs. non-EV, competitors, per-vehicle and per-subreddit breakdowns, a priority scatter) followed by the individual comments behind it, ranked by upvotes and linking back to Reddit.

![Data Explorer](docs/screenshots/03-data-explorer.png)

### Data Gathering — collect or upload, then analyze

Upload a CSV or pull live from Reddit using a saved list of subreddits, set how many posts and comments to grab, then run everything with one **Analyze data** click.

![Data Gathering](docs/screenshots/04-data-gathering.png)

### Settings — provider, tuning, and prompts

Your API key and model, collection defaults, a few tuning knobs, and the editable AI prompts. A **Pipeline runs** sub-tab shows progress and lets you re-run any step.

![Settings](docs/screenshots/05-settings.png)

---

## What's in each tab

| Tab | What it's for |
|-----|---------------|
| **Dashboard** | The at-a-glance overview: headline numbers, the AI briefing, top charts, and which themes are rising or falling. |
| **Q&A** | Ask a question in plain English; get an answer grounded in the actual comments. |
| **Data Explorer** | Every chart, plus the underlying comments with filters (vehicle, sentiment, subreddit, severity, date, search). |
| **Data Gathering** | Upload a CSV or collect from Reddit, then launch the analysis. |
| **Settings** | API key, model, tuning, editable prompts, and run history. |

---

## What the numbers mean

Every comment that gets analyzed is labeled by an AI model for things like sentiment (positive / neutral / negative), whether it's a complaint, how severe the issue is, which GM vehicle it's about, whether it mentions a competitor, and whether it's about EVs. Those labels drive every number in the app.

An **analyzed comment** is one that was actually labeled — junk, deleted, and empty comments are dropped first, so the rates aren't watered down by noise.

**Headline rates** (shown across the top of the Dashboard) are all the same simple calculation — the share of analyzed comments that match:

| Number | Plain-English meaning |
|--------|----------------------|
| **Complaint rate** | % of comments that describe a problem or frustration |
| **Negative rate** | % of comments with negative sentiment |
| **Competitor rate** | % of comments that mention Ford, Toyota, Tesla, etc. |
| **EV rate** | % of comments about GM electric vehicles |

In the screenshot above: 449 analyzed comments, 18.3% complaint rate, 12.7% negative, 2.4% competitor, 8.0% EV.

**Priority matrix.** Complaint themes are sorted into a simple 2×2 based on how *common* they are (volume) and how *negative* they are, relative to the other themes:

| | Common | Rare |
|---|---|---|
| **Very negative** | **Fix now** | **Monitor** |
| **Less negative** | **Watch** | **Low priority** |

**A few other measures:** themes can be ranked by *engagement* (the total upvotes behind them, so a few highly-upvoted comments can outweigh a lot of quiet ones); and the same complaint-rate breakdown is available per vehicle, per subreddit, and per competitor. Charts built from very small samples (fewer than 5 comments) are hidden so a 1-of-2 result never shows up as "50%."

---

## How trend detection works (velocity and z-score)

This is the part that makes the tool more than a snapshot. Comments are first grouped into **themes** by meaning. Then each theme gets scored by two **independent** measures of momentum. Keeping them independent is the point: when both agree, you can trust the signal; when they disagree, the app says so instead of guessing.

> Both measures treat "now" as the most recent comment *in that theme*, not today's date — so the math stays honest on data that may not include the current day.

### Velocity — is this theme speeding up?

Velocity compares how fast comments are coming in **recently** versus the **baseline** before that.

```
recent rate   = comments in the last 7 days  ÷ 7
baseline rate = comments in the prior 30 days ÷ 30

velocity = (recent rate − baseline rate) ÷ baseline rate
```

Read it as a percent change: **+0.5 means the recent pace is 50% above baseline**, −0.3 means 30% below. The app calls a theme **rising** above +0.2, **falling** below −0.2, and **stable** in between. If a theme barely has any history (almost no baseline comments), the app shows a direction but marks it "directional only" rather than treating the number as reliable.

### Z-score — is the latest week unusually busy?

The z-score asks how far the **most recent week** sits from the theme's own normal weekly volume, measured in standard deviations (a standard deviation is just "the typical week-to-week swing").

```
1. Split the theme's comments into weekly buckets.
2. Take the average and the typical swing across those weeks.
3. zscore = (this week's count − average week) ÷ typical swing
```

Read it as: **+2.0 means this week is two standard deviations above normal** — a genuine spike. The app calls it **rising** above +1.0, **falling** below −1.0, **stable** in between. It needs at least four weeks of history to treat the spike as statistically reliable; with less, it's shown but flagged as directional.

### Putting them together

Each theme gets a **confidence banner**:

- **High** — both velocity and z-score agree (both rising, or both falling).
- **Medium** — only one is reliable, or they partly agree.
- **Low** — they point in opposite directions, or there isn't enough data.

That's what drives the **velocity × z-score quadrant** and the **trend leaderboard** on the Dashboard. Each theme also gets its own confidence rating based on how tight and on-topic the group of comments is, so a small or messy theme is automatically marked less trustworthy.

The Dashboard also charts **negative share over time** (the % of comments that are negative each day or week) rather than raw volume — so collecting more comments doesn't masquerade as rising negativity.

---

## Reports you can export

- **Strategy briefing** — an AI-written, multi-section summary (executive summary, top themes, complaints by model, EV findings, what to fix first, competitor signal, recommended actions). Available as Markdown or PDF.
- **Trend briefing** — the rising/falling themes written up, as Markdown or PDF.
- **Data** — the full labeled comments as a CSV, the original source data, and a chart bundle.

PDFs handle accented names and special characters correctly.

---

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

Then open <http://127.0.0.1:8000>.

**AI key.** Labeling, the briefing, and Q&A all need an API key. Set one before launching, or paste a temporary one in the app's **Settings** tab:

```bash
export OPENROUTER_API_KEY="..."     # default provider
# or
export OPENAI_API_KEY="..."
```

Without a key, the app still runs and falls back to a basic keyword classifier, but the AI briefing, theme grouping, and Q&A won't be available.

**Live Reddit collection** is optional and uses a separate collector tool; if it isn't configured, you can still upload a collector CSV from the **Data Gathering** tab and use everything else.
