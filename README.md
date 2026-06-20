# redditgm v2

An interactive FastAPI webapp that turns the existing duplicate-safe Reddit collector and the enhanced GM classification notebook into a single workflow:

1. Collect additive Reddit data with the original `/Users/ricopichardo/Claude/redditgm` collector.
2. Upload or reuse collector CSVs.
3. Classify comment-level GM signals with the enhanced notebook schema.
4. Explore sentiment, complaints, EV/non-EV differences, model risk, competitor mentions, and source evidence.
5. Export classified CSVs, summary payloads, and strategy briefings.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

The app writes local artifacts under `runtime/<run-tag>/`.

## LLM keys

For classification and synthesis, set one of:

```bash
export OPENROUTER_API_KEY="..."
export OPENAI_API_KEY="..."
```

You can also enter a temporary key override in the app sidebar.

## Collector bridge

The collector executable remains an explicit external dependency. Configure its repository with
`REDDITGM_LEGACY_ROOT` or `collection.legacy_root` in `config.json`. If neither is configured,
collection is disabled and `/api/health` reports that state; there is no machine-specific fallback.

The web app intentionally does not expose a `since_days` lookback control. Collection depth comes
from accumulated duplicate-safe runs. The backend still accepts `since_days` only for legacy
compatibility with older direct API clients; new browser requests do not send it.
