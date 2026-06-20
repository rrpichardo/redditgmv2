"""FastAPI server for the redditgm v2 web application."""

from __future__ import annotations

import io
import json
import math
import mimetypes
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from scripts.analyze_run import STEP_ORDER, retry_steps
from src.app_config import (
    cluster_prompt_provenance,
    load_api_key,
    load_config,
    reset_cluster_label_prompt,
    save_api_key,
    save_config,
    validate_cluster_prompt,
    write_cluster_prompt_snapshot,
)
from src.briefing import write_briefing
from src.charts import build_chart_payload, build_detail_data
# Bare import so test mocks (patch "app.start_job", "app.find_active_job") resolve correctly
from src.jobs import find_active_job, job_path, read_status, start_job
from src.run_store import RunStore, latest_output_root
from src.subreddit_lists import (
    ListConflictError,
    ListNotFoundError,
    ListValidationError,
    SubredditListStore,
    write_collection_snapshot,
)
from src.timeseries import build_timeseries
from src.gm_insights import (
    MIN_CELL,
    ProviderConfig,
    all_complaint_mentions,
    analyzed_frame,
    apply_labels,
    classify_preview,
    classify_upload_kind,
    classify_with_llm,
    complete_label,
    cooccurrence,
    complaint_summary,
    ev_comparison,
    evidence_table,
    filter_analyzed,
    flag_summary,
    load_classified,
    load_runtime_frame,
    normalize_reddit_frame,
    priority_matrix,
    save_classified,
    severity_by_model,
    summary_metrics,
    summary_payload,
    value_counts_df,
    vehicle_breakdown,
)


ROOT = Path(__file__).resolve().parent


def resolve_runtime_root(root: Path, env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    configured = values.get("REDDITGM_RUNTIME_ROOT", "").strip()
    return Path(configured).expanduser().resolve() if configured else (root / "runtime").resolve()


RUNTIME = resolve_runtime_root(ROOT)
WEB = ROOT / "web"
SUBREDDIT_LISTS_ROOT = ROOT / "config" / "subreddit_lists"


def resolve_legacy_root(env: Mapping[str, str] | None = None) -> Path | None:
    values = os.environ if env is None else env
    configured = values.get("REDDITGM_LEGACY_ROOT", "").strip()
    if not configured:
        configured = str(load_config().get("collection", {}).get("legacy_root", "")).strip()
    return Path(configured).expanduser().resolve() if configured else None


LEGACY_ROOT = resolve_legacy_root()
DEFAULT_TAG = "gm_vehicle_on_demand"
COLLECT_JOBS: dict[str, dict[str, Any]] = {}
PIPELINE_CHILD_JOB_KINDS = ("classify", "briefing", "trend", "trend_briefing", "faiss_qa")

PROVIDERS = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "model": "gpt-oss-120b",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "model": "gpt-4o-mini",
    },
}

def _sanitize(obj: Any) -> Any:
    """Recursively replace float NaN/inf/-inf with None so JSON.dumps never errors."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def safe_json(data: Any, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(_sanitize(data), status_code=status_code)


def run_active_response(tag: str, run_id: str) -> JSONResponse:
    """Return the frozen top-level tag-lock conflict payload."""
    return safe_json(
        {
            "error": "run_active",
            "message": f"Tag {tag!r} has an active run: {run_id}",
            "active_run_id": run_id,
        },
        status_code=409,
    )


def require_pipeline_run(store: RunStore, tag: str, run_id: str) -> dict[str, Any]:
    """Load a run only when it belongs to the requested tag."""
    run = store.get_run(run_id)
    if not run or run["tag"] != tag:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")
    return run


def require_snapshot_path(tag: str, run_id: str, value: str | Path) -> Path:
    """Resolve one persisted resource path inside its immutable run snapshot."""
    path = Path(value).resolve()
    snapshot_root = (RUNTIME / tag / "runs" / run_id).resolve()
    try:
        path.relative_to(snapshot_root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Pipeline resource not found.") from exc
    return path


def cancel_run_steps(store: RunStore, run_id: str) -> None:
    """Project unfinished steps and the active immutable attempt to cancelled."""
    for step in store.get_steps(run_id, STEP_ORDER):
        if step["state"] == "running":
            attempts = store.get_attempts(run_id, step["name"])
            latest = attempts[-1] if attempts else None
            if latest and latest["state"] == "running":
                store.finish_attempt(
                    run_id,
                    step["name"],
                    latest["attempt_no"],
                    state="cancelled",
                    processed=latest["processed"],
                    total=latest["total"],
                    errors=latest["errors"],
                    error_rate=latest["error_rate"],
                    warning="Cancelled by user.",
                    artifacts=latest["artifacts"],
                    metadata=latest["metadata"],
                )
                continue
        if step["state"] in {"pending", "running"}:
            store.upsert_step(
                run_id,
                step["name"],
                state="cancelled",
                attempt_no=step["attempt_no"],
                started_at=step["started_at"],
                ended_at=time.time(),
                processed=step["processed"],
                total=step["total"],
                errors=step["errors"],
                error_rate=step["error_rate"],
                log_path=step["log_path"],
                warning="Cancelled by user.",
                artifacts=step["artifacts"],
            )


app = FastAPI(title="redditgm v2", version="2.0.0")
app.mount("/static", StaticFiles(directory=WEB), name="static")


class CollectRequest(BaseModel):
    tag: str = DEFAULT_TAG
    subreddit_list_id: str = ""
    source: str = "gm"
    subreddits: str = ""
    listing_limit: int = 100
    comments_limit: int = 5
    since_days: int = 0
    dry_run: bool = False


class SubredditListCreateRequest(BaseModel):
    display_name: str
    type: str = "custom"
    subreddits: list[str]


class SubredditListUpdateRequest(SubredditListCreateRequest):
    version: int


class ClusterPromptValidationRequest(BaseModel):
    prompt: str


class ClassifyRequest(BaseModel):
    tag: str = DEFAULT_TAG
    limit: int = 50


class LlmClassifyRequest(BaseModel):
    tag: str = DEFAULT_TAG
    provider: str = "openrouter"
    model: str = ""
    api_key: str = ""
    limit: int = 25


class BriefingRequest(BaseModel):
    tag: str = DEFAULT_TAG
    provider: str = "openrouter"
    model: str = ""
    api_key: str = ""
    use_llm: bool = False


class ClassifyJobRequest(BaseModel):
    tag: str = DEFAULT_TAG
    provider: str = "openrouter"
    model: str = ""
    api_key: str = ""
    limit: int = 0  # 0 = all pending rows


class PdfExportJobRequest(BaseModel):
    tag: str = DEFAULT_TAG
    kind: str = "charts"  # "charts" | "briefing"


class TrendJobRequest(BaseModel):
    tag: str = DEFAULT_TAG
    provider: str = "openrouter"
    model: str = ""
    api_key: str = ""
    n_clusters: int = 10


class TrendBriefingRequest(BaseModel):
    tag: str = DEFAULT_TAG
    provider: str = "openrouter"
    model: str = ""
    api_key: str = ""


class QaBuildIndexRequest(BaseModel):
    tag: str = DEFAULT_TAG
    provider: str = "openrouter"
    model: str = ""
    api_key: str = ""
    embedding_model: str = "text-embedding-3-small"


class QaSearchRequest(BaseModel):
    tag: str = DEFAULT_TAG
    query: str
    provider: str = "openrouter"
    model: str = ""
    api_key: str = ""
    k: int = 8


class QaAnswerRequest(BaseModel):
    tag: str = DEFAULT_TAG
    question: str
    provider: str = "openrouter"
    model: str = ""
    api_key: str = ""
    k: int = 8


class AnalyzeRequest(BaseModel):
    tag: str = DEFAULT_TAG
    provider: str = "openrouter"
    model: str = "gpt-oss-120b"
    api_key: str = ""
    n_clusters: int = 10


class PipelineCancelRequest(BaseModel):
    tag: str = DEFAULT_TAG
    run_id: str


class PipelineRetryRequest(BaseModel):
    tag: str = DEFAULT_TAG
    run_id: str
    step: str
    api_key: str = ""


def run_dir(tag: str) -> Path:
    return RUNTIME / clean_tag(tag)


def data_dir(tag: str) -> Path:
    return run_dir(tag) / "data"


def output_dir(tag: str) -> Path:
    return latest_output_root(RUNTIME, clean_tag(tag))


def classified_path(tag: str) -> Path:
    return output_dir(tag) / "classified" / "classified_posts.csv"


def report_path(tag: str) -> Path:
    return output_dir(tag) / "reports" / "gm_reddit_synthesis_report.md"


def collect_log_path(tag: str) -> Path:
    return run_dir(tag) / "collect" / "latest.log"


def manifest_path(tag: str) -> Path:
    return run_dir(tag) / "runs" / "run_manifest.jsonl"


def download_dir(tag: str) -> Path:
    return output_dir(tag) / "downloads"


def clean_tag(tag: str) -> str:
    safe = "".join(ch for ch in (tag or DEFAULT_TAG).strip() if ch.isalnum() or ch in "-_")
    return safe or DEFAULT_TAG


def read_subreddit_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    subreddits = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            subreddits.append(value.removeprefix("r/"))
    return subreddits


def subreddit_store() -> SubredditListStore:
    return SubredditListStore(SUBREDDIT_LISTS_ROOT, legacy_root=LEGACY_ROOT)


def read_manifest(tag: str) -> list[dict[str, Any]]:
    path = manifest_path(tag)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def tail_text(path: Path, max_chars: int = 12000) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as handle:
        try:
            handle.seek(-max_chars * 4, os.SEEK_END)
        except OSError:
            handle.seek(0)
        data = handle.read()
    return data.decode("utf-8", errors="replace")[-max_chars:]


def source_subreddits_file(source: str, tag: str, subreddits: str = "") -> Path:
    """Resolve legacy source names through the owned subreddit-list store."""
    key = source.lower().strip()
    list_id = {"gm": "gm-default", "competitor": "competitor-default"}.get(key)
    if not list_id:
        raise HTTPException(
            status_code=422,
            detail="Custom collection now requires a saved subreddit_list_id.",
        )
    store = subreddit_store()
    store.list_all()
    try:
        record = store.get(list_id)
    except (ListNotFoundError, ListValidationError) as exc:
        raise HTTPException(status_code=422, detail=f"Saved subreddit list {list_id!r} is unavailable.") from exc
    _, text_path = write_collection_snapshot(record, run_dir(tag) / "config" / "legacy-source")
    return text_path


def source_download_entries(tag: str) -> list[dict[str, Any]]:
    files = [
        ("combined", "Posts + comments CSV", data_dir(tag) / "gm_posts_with_comments.csv"),
        ("posts", "Posts CSV", data_dir(tag) / "gm_posts.csv"),
        ("comments", "Comments CSV", data_dir(tag) / "gm_comments.csv"),
    ]
    entries = []
    for kind, label, path in files:
        exists = path.exists()
        entries.append({
            "kind": kind,
            "label": label,
            "filename": path.name,
            "exists": exists,
            "bytes": path.stat().st_size if exists else 0,
        })
    return entries


def build_data_bundle(tag: str) -> Path:
    clean = clean_tag(tag)
    bundle = download_dir(clean) / f"{clean}_redditgm_data.zip"
    bundle.parent.mkdir(parents=True, exist_ok=True)
    members = [
        (data_dir(clean) / "gm_posts_with_comments.csv", "data/gm_posts_with_comments.csv"),
        (data_dir(clean) / "gm_posts.csv", "data/gm_posts.csv"),
        (data_dir(clean) / "gm_comments.csv", "data/gm_comments.csv"),
        (classified_path(clean), "classified/classified_posts.csv"),
        (report_path(clean), "reports/gm_reddit_synthesis_report.md"),
        (manifest_path(clean), "runs/run_manifest.jsonl"),
    ]
    existing = [(path, arcname) for path, arcname in members if path.exists()]
    if not existing:
        raise HTTPException(status_code=404, detail="No downloadable data exists for this run.")
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, arcname in existing:
            archive.write(path, arcname)
    return bundle


def source_file_path(tag: str, kind: str) -> Path:
    clean = clean_tag(tag)
    paths = {
        "combined": data_dir(clean) / "gm_posts_with_comments.csv",
        "posts": data_dir(clean) / "gm_posts.csv",
        "comments": data_dir(clean) / "gm_comments.csv",
    }
    selected = kind.lower().strip()
    if selected not in paths:
        raise HTTPException(status_code=400, detail="kind must be all, combined, posts, or comments.")
    path = paths[selected]
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"No {selected} CSV exists for this run.")
    return path


def collect_status_payload(tag: str, job_id: str = "") -> dict[str, Any]:
    clean = clean_tag(tag)
    job = COLLECT_JOBS.get(clean)
    if job_id and job and job.get("job_id") != job_id:
        job = None

    process = job.get("process") if job else None
    returncode = job.get("returncode") if job else None
    status = "idle"
    if process:
        returncode = process.poll()
        job["returncode"] = returncode
        if returncode is None:
            status = "running"
        elif returncode == 0:
            status = "completed"
        else:
            status = "failed"

    rows = read_manifest(clean)
    manifest_start = int(job.get("manifest_start", 0)) if job else 0
    run_rows = rows[manifest_start:] if job and manifest_start <= len(rows) else rows

    source_file = Path(job["subreddits_file"]) if job and job.get("subreddits_file") else None
    total_subreddits = int(job.get("total_subreddits", 0)) if job else (
        len(read_subreddit_file(source_file)) if source_file else 0
    )
    completed = len(run_rows)
    if total_subreddits:
        completed = min(completed, total_subreddits)
        if status == "idle" and rows and completed >= total_subreddits:
            status = "completed"

    last_row = run_rows[-1] if run_rows else (rows[-1] if rows else {})
    started_at = job.get("started_at") if job else None
    elapsed = time.time() - started_at if started_at else None

    return {
        "ok": status != "failed",
        "tag": clean,
        "job_id": job.get("job_id", "") if job else "",
        "status": status,
        "returncode": returncode,
        "started_at": started_at,
        "elapsed_seconds": round(elapsed, 1) if elapsed is not None else None,
        "completed_subreddits": completed,
        "total_subreddits": total_subreddits,
        "last_subreddit": last_row.get("subreddit", ""),
        "last_row": last_row,
        "manifest_tail": run_rows[-12:],
        "log": tail_text(collect_log_path(clean)),
        "cmd": job.get("cmd", []) if job else [],
        "log_path": str(collect_log_path(clean)),
    }


def provider_config(provider_name: str, model: str = "", api_key: str = "") -> ProviderConfig:
    key = provider_name.lower().strip()
    if key not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider_name}")
    info = PROVIDERS[key]
    # Fall back to the key saved in Settings (runtime/secrets.json) when the request
    # carries none — the GET /api/config endpoint never returns the raw key, so the
    # browser can't resend it. Server-side resolution keeps the key off the wire.
    resolved_key = api_key.strip() or load_api_key()
    return ProviderConfig(
        provider=key,
        model=model.strip() or info["model"],
        base_url=info["base_url"],
        api_key_env=info["api_key_env"],
        api_key=resolved_key,
    )


def read_upload(upload: UploadFile) -> pd.DataFrame:
    data = upload.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    try:
        return pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read CSV: {exc}") from exc


def save_upload(tag: str, upload: UploadFile, df: pd.DataFrame) -> Path:
    kind = classify_upload_kind(df)
    target = data_dir(tag)
    target.mkdir(parents=True, exist_ok=True)
    if kind == "combined":
        name = "gm_posts_with_comments.csv"
    elif kind == "posts":
        name = "gm_posts.csv"
    elif kind == "comments":
        name = "gm_comments.csv"
    elif kind == "classified":
        path = classified_path(tag)
        save_classified(normalize_reddit_frame(df), path)
        return path
    else:
        raise HTTPException(status_code=400, detail="CSV schema did not match collector or classified output.")
    path = target / name
    df.to_csv(path, index=False)
    return path


def load_frame(tag: str) -> pd.DataFrame:
    cpath = classified_path(tag)
    raw_files = list(data_dir(tag).glob("*.csv")) if data_dir(tag).exists() else []
    newest_raw_mtime = max((path.stat().st_mtime for path in raw_files), default=-1.0)
    if cpath.exists() and cpath.stat().st_mtime >= newest_raw_mtime:
        return load_classified(cpath)
    raw = load_runtime_frame(data_dir(tag))
    if raw.empty:
        return pd.DataFrame()
    return normalize_reddit_frame(raw)


def dataframe_records(df: pd.DataFrame, limit: int | None = None) -> list[dict[str, Any]]:
    if df.empty:
        return []
    work = df.head(limit) if limit else df
    text = work.where(pd.notna(work), "").to_json(orient="records", date_format="iso")
    return json.loads(text)


def run_snapshot(tag: str, filters: dict[str, Any] | None = None) -> dict[str, Any]:
    df = load_frame(tag)
    selected = filter_analyzed(df, filters or {}) if not df.empty else pd.DataFrame()
    payload = summary_payload(selected) if not selected.empty else {
        "metrics": summary_metrics(df) if not df.empty else {
            "total_rows": 0,
            "analyzed_rows": 0,
            "skipped_rows": 0,
            "complaints": 0,
            "complaint_rate": 0,
            "negative_rate": 0,
            "competitor_rate": 0,
            "ev_rate": 0,
        },
        "sentiment_distribution": [],
        "binary_flag_summary": [],
        "top_complaint_categories": [],
        "all_complaint_mentions": [],
        "ev_vs_non_ev": [],
        "vehicles": [],
        "priority_matrix": [],
        "competitor_brands": [],
        "issue_severity": [],
    }
    if selected.empty:
        # Legacy charts dict (used by existing frontend) — empty state.
        charts = {
            "sentiment": [],
            "flags": [],
            "complaints": [],
            "allComplaintMentions": [],
            "ev": [],
            "vehicles": [],
            "severityByModel": [],
            "priority": [],
            "competitors": [],
            "cooccurrence": {},
        }
        evidence = []
        chart_contract = build_chart_payload(pd.DataFrame())
    else:
        corr = cooccurrence(selected)
        # Legacy charts dict — kept for backward compat while frontend migrates.
        charts = {
            "sentiment": dataframe_records(value_counts_df(selected, "sentiment", "sentiment")),
            "flags": dataframe_records(flag_summary(selected)),
            "complaints": dataframe_records(complaint_summary(selected)),
            "allComplaintMentions": dataframe_records(all_complaint_mentions(selected)),
            "ev": dataframe_records(ev_comparison(selected)),
            "vehicles": dataframe_records(vehicle_breakdown(selected, min_rows=1)),
            "severityByModel": dataframe_records(severity_by_model(selected, min_complaints=1)),
            "priority": dataframe_records(priority_matrix(selected, min_complaints=1)),
            "competitors": dataframe_records(
                value_counts_df(
                    selected[selected["competitor_mention"] == 1], "competitor_brand", "brand"
                ) if "competitor_mention" in selected else pd.DataFrame()
            ),
            "cooccurrence": corr.where(pd.notna(corr), 0).to_dict() if not corr.empty else {},
        }
        evidence = dataframe_records(evidence_table(selected, limit=500), limit=500)
        # New Phase 1 chart contract: chart_specs + chart_data.
        chart_contract = build_chart_payload(selected)
    return {
        "tag": clean_tag(tag),
        "paths": {
            "runtime": str(run_dir(tag)),
            "data": str(data_dir(tag)),
            "classified": str(classified_path(tag)),
            "report": str(report_path(tag)),
            "collector": str(LEGACY_ROOT) if LEGACY_ROOT else "",
        },
        "status": {
            "has_source": not load_runtime_frame(data_dir(tag)).empty,
            "has_classified": classified_path(tag).exists(),
            "has_report": report_path(tag).exists(),
            "legacy_collector_configured": LEGACY_ROOT is not None,
            "legacy_collector_found": bool(LEGACY_ROOT and LEGACY_ROOT.exists()),
            "source_files": source_download_entries(tag),
        },
        "summary": payload,
        "charts": charts,
        # Phase 1 chart contract — browser and export renderers should prefer these.
        "chart_specs": chart_contract["chart_specs"],
        "chart_data": chart_contract["chart_data"],
        "chart_meta": chart_contract["chart_meta"],
        "evidence": evidence,
        "filterOptions": filter_options(df),
    }


def filter_options(df: pd.DataFrame) -> dict[str, list[str]]:
    if df.empty:
        return {}
    selected = filter_analyzed(df, {})
    options: dict[str, list[str]] = {}
    for key, column in {
        "sentiment": "sentiment",
        "vehicle": "vehicle_mentioned",
        "subreddit": "subreddit_norm",
        "severity": "issue_severity",
        "commentType": "comment_type",
        "competitor": "competitor_brand",
    }.items():
        if column in selected:
            values = selected[column].dropna().astype(str)
            options[key] = sorted([value for value in values.unique().tolist() if value and value != "nan"])
    if "created_at_norm" in selected and selected["created_at_norm"].notna().any():
        dates = pd.to_datetime(selected["created_at_norm"], errors="coerce").dropna().dt.date
        options["dateStart"] = [str(dates.min())]
        options["dateEnd"] = [str(dates.max())]
    return options


def request_filters(
    sentiment: list[str] | None,
    vehicle: list[str] | None,
    subreddit: list[str] | None,
    severity: list[str] | None,
    comment_type: list[str] | None,
    competitor: list[str] | None,
    search: str,
    min_score: float | None,
    date_start: str | None = None,
    date_end: str | None = None,
) -> dict[str, Any]:
    from datetime import date as _date

    def _parse(s: str | None) -> _date | None:
        try:
            return _date.fromisoformat(s) if s else None
        except ValueError:
            return None

    result: dict[str, Any] = {
        "sentiment": sentiment or [],
        "vehicle": vehicle or [],
        "subreddit": subreddit or [],
        "severity": severity or [],
        "comment_type": comment_type or [],
        "competitor": competitor or [],
        "search": search,
        "min_score": min_score,
    }
    start, end = _parse(date_start), _parse(date_end)
    if start or end:
        result["date_range"] = [start, end]
    return result


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((WEB / "index.html").read_text(encoding="utf-8"))


@app.get("/api/config")
def get_config_endpoint() -> JSONResponse:
    cfg = load_config()
    # Never expose the key in GET — just signal whether one is stored
    api_key = load_api_key()
    cfg["_api_key_set"] = bool(api_key)
    return JSONResponse(cfg)


@app.patch("/api/config")
async def patch_config_endpoint(request: Request) -> JSONResponse:
    body = await request.json()
    api_key = body.pop("api_key", None)
    cluster_prompt = body.get("prompts", {}).get("cluster_label") if isinstance(body.get("prompts"), dict) else None
    if cluster_prompt is not None:
        validation = validate_cluster_prompt(str(cluster_prompt))
        if not validation["valid"]:
            raise HTTPException(status_code=422, detail=validation)
    if api_key is not None:
        save_api_key(api_key)
    if body:
        save_config(body)
    return JSONResponse({"ok": True})


@app.post("/api/config/cluster-prompt/validate")
def validate_cluster_prompt_endpoint(request: ClusterPromptValidationRequest) -> JSONResponse:
    return safe_json(validate_cluster_prompt(request.prompt))


@app.post("/api/config/cluster-prompt/reset")
def reset_cluster_prompt_endpoint() -> JSONResponse:
    prompt = reset_cluster_label_prompt()
    return safe_json({"prompt": prompt, "validation": validate_cluster_prompt(prompt)})


@app.get("/api/subreddit-lists")
def list_subreddit_lists() -> JSONResponse:
    return safe_json({"items": subreddit_store().list_all()})


@app.get("/api/subreddit-lists/{list_id}")
def get_subreddit_list(list_id: str) -> JSONResponse:
    try:
        return safe_json(subreddit_store().get(list_id))
    except ListNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Subreddit list not found.") from exc
    except ListValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/subreddit-lists", status_code=201)
def create_subreddit_list(request: SubredditListCreateRequest) -> JSONResponse:
    try:
        record = subreddit_store().create(request.display_name, request.type, request.subreddits)
    except ListConflictError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "current": exc.current}) from exc
    except ListValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return safe_json(record, status_code=201)


@app.put("/api/subreddit-lists/{list_id}")
def update_subreddit_list(list_id: str, request: SubredditListUpdateRequest) -> JSONResponse:
    try:
        record = subreddit_store().update(
            list_id,
            version=request.version,
            display_name=request.display_name,
            list_type=request.type,
            subreddits=request.subreddits,
        )
    except ListNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Subreddit list not found.") from exc
    except ListConflictError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "current": exc.current}) from exc
    except ListValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return safe_json(record)


@app.get("/api/run")
def get_run(
    tag: str = DEFAULT_TAG,
    sentiment: list[str] | None = Query(default=None),
    vehicle: list[str] | None = Query(default=None),
    subreddit: list[str] | None = Query(default=None),
    severity: list[str] | None = Query(default=None),
    comment_type: list[str] | None = Query(default=None),
    competitor: list[str] | None = Query(default=None),
    search: str = "",
    min_score: float | None = None,
    date_start: str | None = None,
    date_end: str | None = None,
) -> JSONResponse:
    filters = request_filters(sentiment, vehicle, subreddit, severity, comment_type, competitor, search, min_score, date_start, date_end)
    return safe_json(run_snapshot(tag, filters))


@app.get("/api/evidence")
def get_evidence(
    tag: str = DEFAULT_TAG,
    sentiment: list[str] | None = Query(default=None),
    vehicle: list[str] | None = Query(default=None),
    subreddit: list[str] | None = Query(default=None),
    severity: list[str] | None = Query(default=None),
    comment_type: list[str] | None = Query(default=None),
    competitor: list[str] | None = Query(default=None),
    search: str = "",
    min_score: float | None = None,
    date_start: str | None = None,
    date_end: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
) -> JSONResponse:
    """Return stable, server-filtered evidence pages for Explorer."""
    frame = load_frame(clean_tag(tag))
    filters = request_filters(
        sentiment,
        vehicle,
        subreddit,
        severity,
        comment_type,
        competitor,
        search,
        min_score,
        date_start,
        date_end,
    )
    selected = filter_analyzed(frame, filters) if not frame.empty else pd.DataFrame()
    rows = evidence_table(selected, limit=None) if not selected.empty else pd.DataFrame()
    total_items = len(rows)
    total_pages = math.ceil(total_items / page_size) if total_items else 0
    start = (page - 1) * page_size
    items = dataframe_records(rows.iloc[start:start + page_size]) if total_items else []
    for item in items:
        item["score_unit"] = "reddit_score"
    return safe_json({
        "items": items,
        "page": page,
        "page_size": page_size,
        "total_items": total_items,
        "total_pages": total_pages,
        "score_unit": "reddit_score",
    })


@app.get("/api/charts/detail")
def charts_detail(
    tag: str = DEFAULT_TAG,
    sentiment: list[str] | None = Query(default=None),
    vehicle: list[str] | None = Query(default=None),
    subreddit: list[str] | None = Query(default=None),
    severity: list[str] | None = Query(default=None),
    comment_type: list[str] | None = Query(default=None),
    competitor: list[str] | None = Query(default=None),
    search: str = "",
    min_score: float | None = None,
    date_start: str | None = None,
    date_end: str | None = None,
) -> JSONResponse:
    """Serve lazy chart data (category_by_model heatmap and flag co-occurrence).
    Accepts the same filter params as /api/run so the browser can pass them through."""
    df = load_frame(tag)
    filters = request_filters(sentiment, vehicle, subreddit, severity, comment_type, competitor, search, min_score, date_start, date_end)
    selected = filter_analyzed(df, filters) if not df.empty else pd.DataFrame()
    return safe_json(build_detail_data(selected))


@app.post("/api/upload")
def upload_csv(tag: str = DEFAULT_TAG, file: UploadFile = File(...)) -> JSONResponse:
    tag = clean_tag(tag)
    # Reject upload if the tag has an active pipeline run to prevent data races
    with RunStore(RUNTIME / "runs.db") as store:
        store.reconcile_tag_lock(tag)
        lock = store.get_tag_lock(tag)
    if lock:
        raise HTTPException(status_code=409, detail={"error": "run_active", "message": f"Tag {tag!r} has an active run: {lock['run_id']}", "active_run_id": lock["run_id"]})
    df = read_upload(file)
    path = save_upload(tag, file, df)
    return safe_json({"ok": True, "path": str(path), "kind": classify_upload_kind(df), "rows": len(df)})


@app.post("/api/collect")
def collect_data(request: CollectRequest) -> JSONResponse:
    if LEGACY_ROOT is None:
        raise HTTPException(
            status_code=400,
            detail="Collector folder is not configured. Set REDDITGM_LEGACY_ROOT or collection.legacy_root.",
        )
    if not LEGACY_ROOT.exists():
        raise HTTPException(status_code=400, detail=f"Configured collector folder not found: {LEGACY_ROOT}")

    tag = clean_tag(request.tag)
    # Reject collect if the tag has an active pipeline run to prevent data races
    with RunStore(RUNTIME / "runs.db") as store:
        store.reconcile_tag_lock(tag)
        lock = store.get_tag_lock(tag)
    if lock:
        raise HTTPException(status_code=409, detail={"error": "run_active", "message": f"Tag {tag!r} has an active run: {lock['run_id']}", "active_run_id": lock["run_id"]})
    active = COLLECT_JOBS.get(tag)
    if active and active.get("process") and active["process"].poll() is None:
        payload = collect_status_payload(tag, active.get("job_id", ""))
        payload["started"] = False
        return safe_json(payload)

    job_id = uuid.uuid4().hex
    store = subreddit_store()
    store.list_all()
    list_id = request.subreddit_list_id.strip()
    if not list_id:
        list_id = {"gm": "gm-default", "competitor": "competitor-default"}.get(
            request.source.lower().strip(), ""
        )
    if not list_id:
        raise HTTPException(status_code=422, detail="Collection requires a saved subreddit_list_id.")
    try:
        selected_list = store.get(list_id)
    except ListNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Selected subreddit list not found.") from exc
    except ListValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    snapshot_root = run_dir(tag) / "collect" / job_id / "config"
    _, subreddits_file = write_collection_snapshot(selected_list, snapshot_root)

    target_data = data_dir(tag).resolve()
    target_state = (run_dir(tag) / "state" / "seen_posts.json").resolve()
    target_runs = (run_dir(tag) / "runs").resolve()
    target_data.mkdir(parents=True, exist_ok=True)
    target_runs.mkdir(parents=True, exist_ok=True)

    python_bin = LEGACY_ROOT / ".venv311" / "bin" / "python"
    if not python_bin.exists():
        python_bin = Path(sys.executable)

    cmd = [
        str(python_bin),
        "-u",
        "collect_incremental.py",
        "--subreddits-file",
        str(subreddits_file.resolve()),
        "--data-dir",
        str(target_data),
        "--state-file",
        str(target_state),
        "--runs-dir",
        str(target_runs),
        "--listing-limit",
        str(max(1, request.listing_limit)),
        "--comments-limit",
        str(max(0, request.comments_limit)),
        "--progress-every",
        "10",
    ]
    # Dormant legacy compatibility: the browser no longer sends `since_days`,
    # but older direct API clients may still opt into the collector flag.
    if request.since_days:
        cmd.extend(["--since-days", str(max(0, request.since_days))])
    if request.dry_run:
        cmd.append("--dry-run")

    log_path = collect_log_path(tag)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_start = len(read_manifest(tag))
    subreddits = read_subreddit_file(subreddits_file)
    header = [
        f"Collector job {job_id} started at {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Source file: {subreddits_file.resolve()}",
        f"Subreddits: {', '.join(subreddits) if subreddits else 'none'}",
        f"Command: {' '.join(cmd)}",
        "",
    ]
    log_path.write_text("\n".join(header), encoding="utf-8")

    log_handle = log_path.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            cmd,
            cwd=LEGACY_ROOT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    finally:
        log_handle.close()

    COLLECT_JOBS[tag] = {
        "job_id": job_id,
        "process": process,
        "returncode": None,
        "started_at": time.time(),
        "manifest_start": manifest_start,
        "total_subreddits": len(subreddits),
        "subreddits_file": str(subreddits_file.resolve()),
        "subreddit_list_id": selected_list["id"],
        "subreddit_list_version": selected_list["version"],
        "cmd": cmd,
    }
    payload = collect_status_payload(tag, job_id)
    payload["started"] = True
    return safe_json(payload)


@app.get("/api/collect/status")
def collect_status(tag: str = DEFAULT_TAG, job_id: str = "") -> JSONResponse:
    return safe_json(collect_status_payload(tag, job_id))


@app.post("/api/classify/preview")
def preview_classify(request: ClassifyRequest) -> JSONResponse:
    raw = load_frame(request.tag)
    if raw.empty:
        raw = normalize_reddit_frame(load_runtime_frame(data_dir(request.tag)))
    if raw.empty:
        raise HTTPException(status_code=400, detail="No source data is loaded for this run.")
    classified = classify_preview(raw, limit=request.limit)
    path = save_classified(classified, classified_path(request.tag))
    return safe_json({"ok": True, "path": str(path), "rows": len(classified)})


@app.post("/api/classify/llm")
def llm_classify(request: LlmClassifyRequest) -> JSONResponse:
    tag = clean_tag(request.tag)
    # Reject if the tag has an active pipeline run
    with RunStore(RUNTIME / "runs.db") as store:
        store.reconcile_tag_lock(tag)
        lock = store.get_tag_lock(tag)
    if lock:
        raise HTTPException(status_code=409, detail={"error": "run_active", "message": f"Tag {tag!r} has an active run: {lock['run_id']}", "active_run_id": lock["run_id"]})
    df = load_frame(request.tag)
    if df.empty:
        raise HTTPException(status_code=400, detail="No source data is loaded for this run.")
    pending = df[(~df["skip_classification"]) & (df["classifier_mode"].fillna("") == "")]
    work = pending.head(max(1, request.limit))
    if work.empty:
        return safe_json({"ok": True, "rows": 0, "message": "No pending rows."})

    provider = provider_config(request.provider, request.model, request.api_key)
    labels = []
    indices = []
    for idx, row in work.iterrows():
        labels.append(complete_label(classify_with_llm(str(row["combined_text"]), provider)))
        indices.append(idx)
    classified = apply_labels(df, labels, indices, mode="llm")
    path = save_classified(classified, classified_path(request.tag))
    return safe_json({"ok": True, "path": str(path), "rows": len(labels)})


@app.post("/api/briefing")
def briefing(request: BriefingRequest) -> JSONResponse:
    df = load_frame(request.tag)
    if df.empty:
        raise HTTPException(status_code=400, detail="No data is loaded for this run.")
    path = report_path(request.tag)
    result = write_briefing(
        df,
        path,
        provider=(
            provider_config(request.provider, request.model, request.api_key)
            if request.use_llm
            else None
        ),
        use_llm=request.use_llm,
        fallback_on_error=False,
    )
    return safe_json({"ok": True, "path": str(path), "report": result.report})


@app.post("/api/classify/job")
def classify_job(request: ClassifyJobRequest) -> JSONResponse:
    """Start a full-run LLM classify subprocess. Returns existing job if one is running."""
    tag = clean_tag(request.tag)
    # Reject if the tag has an active pipeline run
    with RunStore(RUNTIME / "runs.db") as store:
        store.reconcile_tag_lock(tag)
        lock = store.get_tag_lock(tag)
    if lock:
        raise HTTPException(status_code=409, detail={"error": "run_active", "message": f"Tag {tag!r} has an active run: {lock['run_id']}", "active_run_id": lock["run_id"]})
    # Check for an already-running job first — active job takes priority over CSV check
    active = find_active_job(RUNTIME, tag, "classify")
    if active:
        return safe_json({**active, "started": False})
    cpath = classified_path(tag)
    if not cpath.exists():
        raise HTTPException(
            status_code=400,
            detail="No classified CSV found for this tag. Run preview classification first.",
        )
    pcfg = provider_config(request.provider, request.model, request.api_key)
    # API key goes in env, never on the command line
    env: dict[str, str] = {}
    if pcfg.api_key:
        env[pcfg.api_key_env] = pcfg.api_key
    extra_args = [
        "--classified_path", str(cpath),
        "--provider", pcfg.provider,
        "--base_url", pcfg.base_url,
        "--model", pcfg.model,
        "--api_key_env", pcfg.api_key_env,
        "--limit", str(max(0, request.limit)),
    ]
    status = start_job(
        RUNTIME, tag, "classify",
        ROOT / "scripts" / "classify_job.py",
        extra_args,
        env=env,
        cwd=ROOT,
    )
    return safe_json({**status, "started": True})


@app.get("/api/classify/status")
def classify_status(tag: str = DEFAULT_TAG, job_id: str = "") -> JSONResponse:
    """Return the most recent classify job status. Reconciles dead PIDs on read."""
    tag = clean_tag(tag)
    if job_id:
        # Specific job requested — look it up by ID
        status = read_status(job_path(RUNTIME, tag, job_id))
    else:
        # No job_id: try active job first, then fall back to most recent finished job
        status = find_active_job(RUNTIME, tag, "classify")
        if not status:
            d = RUNTIME / tag / "jobs"
            if d.exists():
                all_jobs = []
                for p in d.glob("*.json"):
                    s = read_status(p)
                    if s and s.get("kind") == "classify":
                        all_jobs.append(s)
                if all_jobs:
                    status = max(all_jobs, key=lambda s: float(s.get("started_at", 0)))
    if not status:
        return safe_json({"state": "idle", "tag": tag})
    return safe_json(status)


@app.post("/api/export/pdf-job")
def pdf_export_job(request: PdfExportJobRequest) -> JSONResponse:
    """Start a PDF export subprocess job. Requires classified data."""
    tag = clean_tag(request.tag)
    active = find_active_job(RUNTIME, tag, "pdf_export")
    if active:
        return safe_json({**active, "started": False})
    cpath = classified_path(tag)
    if not cpath.exists():
        raise HTTPException(
            status_code=400,
            detail="No classified CSV found. Run preview or LLM classification first.",
        )
    extra_args = [
        "--kind", request.kind,
        "--classified_path", str(cpath),
    ]
    status = start_job(
        RUNTIME, tag, "pdf_export",
        ROOT / "scripts" / "pdf_export_job.py",
        extra_args,
        cwd=ROOT,
    )
    return safe_json({**status, "started": True})


@app.get("/api/export/status")
def pdf_export_status(tag: str = DEFAULT_TAG, job_id: str = "") -> JSONResponse:
    """Return the most recent pdf_export job status."""
    tag = clean_tag(tag)
    if job_id:
        status = read_status(job_path(RUNTIME, tag, job_id))
    else:
        status = find_active_job(RUNTIME, tag, "pdf_export")
    if not status:
        return safe_json({"state": "idle", "tag": tag})
    return safe_json(status)


@app.get("/api/download/classified")
def download_classified(tag: str = DEFAULT_TAG) -> Response:
    path = classified_path(tag)
    if not path.exists():
        raise HTTPException(status_code=404, detail="No classified CSV exists for this run.")
    return FileResponse(path, media_type="text/csv", filename=f"{clean_tag(tag)}_classified_posts.csv")


@app.get("/api/download/source")
def download_source(tag: str = DEFAULT_TAG, kind: str = "combined") -> Response:
    clean = clean_tag(tag)
    selected = kind.lower().strip()
    if selected == "all":
        bundle = build_data_bundle(clean)
        return FileResponse(bundle, media_type="application/zip", filename=f"{clean}_redditgm_data.zip")

    path = source_file_path(clean, selected)
    return FileResponse(path, media_type="text/csv", filename=f"{clean}_{path.name}")


@app.post("/api/export/save")
def save_export(tag: str = DEFAULT_TAG, kind: str = "all") -> JSONResponse:
    clean = clean_tag(tag)
    selected = kind.lower().strip()
    if selected == "all":
        source = build_data_bundle(clean)
        suffix = ".zip"
        label = "redditgm_data"
    elif selected == "classified":
        source = classified_path(clean)
        if not source.exists():
            raise HTTPException(status_code=404, detail="No classified CSV exists for this run.")
        suffix = ".csv"
        label = source.stem
    elif selected == "report":
        source = report_path(clean)
        if not source.exists():
            raise HTTPException(status_code=404, detail="No briefing exists for this run.")
        suffix = ".md"
        label = source.stem
    else:
        source = source_file_path(clean, selected)
        suffix = source.suffix
        label = source.stem

    downloads = Path.home() / "Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    destination = downloads / f"{clean}_{label}_{timestamp}{suffix}"
    shutil.copy2(source, destination)
    return safe_json({
        "ok": True,
        "path": str(destination),
        "filename": destination.name,
        "bytes": destination.stat().st_size,
    })


@app.get("/api/download/report")
def download_report(tag: str = DEFAULT_TAG) -> Response:
    path = report_path(tag)
    if not path.exists():
        raise HTTPException(status_code=404, detail="No briefing exists for this run.")
    return FileResponse(path, media_type="text/markdown", filename=f"{clean_tag(tag)}_gm_reddit_strategy_briefing.md")


@app.get("/api/download/charts")
def download_charts(tag: str = DEFAULT_TAG) -> Response:
    """Download the charts ZIP produced by a completed pdf_export job."""
    clean = clean_tag(tag)
    path = run_dir(clean) / "downloads" / f"{clean}_charts.zip"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Charts ZIP not ready. Start an export job with kind=charts first.",
        )
    return FileResponse(path, media_type="application/zip", filename=f"{clean}_charts.zip")


@app.get("/api/download/briefing-pdf")
def download_briefing_pdf(tag: str = DEFAULT_TAG) -> Response:
    """Download the briefing PDF produced by a completed pdf_export job."""
    clean = clean_tag(tag)
    path = run_dir(clean) / "downloads" / f"{clean}_briefing.pdf"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Briefing PDF not ready. Start an export job with kind=briefing first.",
        )
    return FileResponse(
        path, media_type="application/pdf",
        filename=f"{clean}_gm_reddit_briefing.pdf",
    )


@app.post("/api/trends/run")
def trends_run(request: TrendJobRequest) -> JSONResponse:
    """Start a KMeans+FAISS clustering job. Returns existing job if one is running."""
    tag = clean_tag(request.tag)
    # Reject if the tag has an active pipeline run
    with RunStore(RUNTIME / "runs.db") as store:
        store.reconcile_tag_lock(tag)
        lock = store.get_tag_lock(tag)
    if lock:
        raise HTTPException(status_code=409, detail={"error": "run_active", "message": f"Tag {tag!r} has an active run: {lock['run_id']}", "active_run_id": lock["run_id"]})
    active = find_active_job(RUNTIME, tag, "trend")
    if active:
        return safe_json({**active, "started": False})
    cpath = classified_path(tag)
    if not cpath.exists():
        raise HTTPException(
            status_code=400,
            detail="No classified CSV found for this tag. Run classification first.",
        )
    pcfg = provider_config(request.provider, request.model, request.api_key)
    env: dict[str, str] = {}
    if pcfg.api_key:
        env[pcfg.api_key_env] = pcfg.api_key
    extra_args = [
        "--classified_path", str(cpath),
        "--n_clusters", str(max(2, request.n_clusters)),
        "--provider", pcfg.provider,
        "--base_url", pcfg.base_url,
        "--model", pcfg.model,
        "--api_key_env", pcfg.api_key_env,
    ]
    status = start_job(
        RUNTIME, tag, "trend",
        ROOT / "scripts" / "trend_job.py",
        extra_args,
        env=env,
        cwd=ROOT,
    )
    return safe_json({**status, "started": True})


@app.get("/api/trends/status")
def trends_status(tag: str = DEFAULT_TAG, job_id: str = "") -> JSONResponse:
    """Return the most recent trend job status."""
    tag = clean_tag(tag)
    if job_id:
        status = read_status(job_path(RUNTIME, tag, job_id))
    else:
        status = find_active_job(RUNTIME, tag, "trend")
        if not status:
            d = RUNTIME / tag / "jobs"
            if d.exists():
                all_jobs = [
                    s for p in d.glob("*.json")
                    if (s := read_status(p)) and s.get("kind") == "trend"
                ]
                if all_jobs:
                    status = max(all_jobs, key=lambda s: float(s.get("started_at", 0)))
    if not status:
        return safe_json({"state": "idle", "tag": tag})
    return safe_json(status)


@app.get("/api/trends")
def trends_results(tag: str = DEFAULT_TAG) -> JSONResponse:
    """Return cluster labels, examples, and trend signals from the most recent clustering run."""
    from src.trend_insights import trends_dir
    tag = clean_tag(tag)
    tdir = trends_dir(RUNTIME, tag)
    labels_path = tdir / "cluster_labels.json"
    examples_path = tdir / "cluster_examples.json"
    metadata_path = tdir / "embedding_metadata.json"
    signals_path = tdir / "trend_signals.json"
    if not labels_path.exists():
        return safe_json({"ok": False, "detail": "No clustering results found. Run /api/trends/run first.", "clusters": []})
    try:
        labels = json.loads(labels_path.read_text(encoding="utf-8"))
        examples = json.loads(examples_path.read_text(encoding="utf-8")) if examples_path.exists() else {}
        metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
        signals_doc = json.loads(signals_path.read_text(encoding="utf-8")) if signals_path.exists() else {}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read clustering artifacts: {exc}") from exc
    cluster_signals = signals_doc.get("signals", {})
    clusters = []
    for cid_str, label in sorted(labels.items(), key=lambda x: int(x[0])):
        ctx = examples.get(cid_str, {})
        sig = cluster_signals.get(cid_str, {})
        clusters.append({
            "cluster_id": int(cid_str),
            "label": label,
            "cluster_size": ctx.get("cluster_size", 0),
            "sentiment_mix": ctx.get("sentiment_mix", {}),
            "severity_mix": ctx.get("severity_mix", {}),
            "top_vehicles": ctx.get("top_vehicles", []),
            "top_categories": ctx.get("top_categories", []),
            "trend_signal": sig,
        })
    return safe_json({
        "ok": True,
        "tag": tag,
        "metadata": metadata,
        "trend_summary": {
            "has_timestamps": signals_doc.get("has_timestamps", False),
            "data_span_days": signals_doc.get("data_span_days", 0),
            "computed_at": signals_doc.get("computed_at"),
        } if signals_doc else None,
        "clusters": clusters,
    })


@app.get("/api/trends/timeseries")
def trends_timeseries(tag: str = DEFAULT_TAG) -> JSONResponse:
    """Return an adaptive UTC time series with explicit availability reasons."""
    tag = clean_tag(tag)
    cpath = classified_path(tag)
    if not cpath.exists():
        return safe_json({
            "ok": False,
            "reason_code": "no_classified_data",
            "detail": "No classified data found for this analysis.",
            "valid_timestamp_count": 0,
            "invalid_timestamp_count": 0,
        })
    try:
        raw = load_classified(cpath)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read classified data: {exc}") from exc
    return safe_json(build_timeseries(analyzed_frame(raw)))


@app.post("/api/trends/briefing")
def trends_briefing(request: TrendBriefingRequest) -> JSONResponse:
    """Start a trend briefing PDF job. Returns existing job if one is running."""
    tag = clean_tag(request.tag)
    active = find_active_job(RUNTIME, tag, "trend_briefing")
    if active:
        return safe_json({**active, "started": False})
    from src.trend_insights import trends_dir
    tdir = trends_dir(RUNTIME, tag)
    if not (tdir / "cluster_labels.json").exists():
        raise HTTPException(
            status_code=400,
            detail="No clustering results found. Run /api/trends/run first.",
        )
    pcfg = provider_config(request.provider, request.model, request.api_key)
    env: dict[str, str] = {}
    if pcfg.api_key:
        env[pcfg.api_key_env] = pcfg.api_key
    extra_args = [
        "--provider", pcfg.provider,
        "--model", pcfg.model,
        "--api_key_env", pcfg.api_key_env,
    ]
    status = start_job(
        RUNTIME, tag, "trend_briefing",
        ROOT / "scripts" / "trend_briefing_job.py",
        extra_args,
        env=env,
        cwd=ROOT,
    )
    return safe_json({**status, "started": True})


@app.get("/api/trends/briefing/status")
def trends_briefing_status(tag: str = DEFAULT_TAG, job_id: str = "") -> JSONResponse:
    """Return the most recent trend briefing job status."""
    tag = clean_tag(tag)
    if job_id:
        status = read_status(job_path(RUNTIME, tag, job_id))
    else:
        status = find_active_job(RUNTIME, tag, "trend_briefing")
        if not status:
            d = RUNTIME / tag / "jobs"
            if d.exists():
                all_jobs = [
                    s for p in d.glob("*.json")
                    if (s := read_status(p)) and s.get("kind") == "trend_briefing"
                ]
                if all_jobs:
                    status = max(all_jobs, key=lambda s: float(s.get("started_at", 0)))
    if not status:
        return safe_json({"state": "idle", "tag": tag})
    return safe_json(status)


@app.get("/api/download/trend-pdf")
def download_trend_pdf(tag: str = DEFAULT_TAG) -> Response:
    """Download the trend briefing PDF produced by a completed trend_briefing job."""
    clean = clean_tag(tag)
    path = run_dir(clean) / "downloads" / f"{clean}_trend_briefing.pdf"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Trend briefing PDF not ready. Start a job with POST /api/trends/briefing first.",
        )
    return FileResponse(
        path, media_type="application/pdf",
        filename=f"{clean}_gm_trend_briefing.pdf",
    )


@app.post("/api/qa/build-index")
def qa_build_index(request: QaBuildIndexRequest) -> JSONResponse:
    """Start a FAISS Q&A index build job. Returns existing job if one is running."""
    tag = clean_tag(request.tag)
    # Reject if the tag has an active pipeline run
    with RunStore(RUNTIME / "runs.db") as store:
        store.reconcile_tag_lock(tag)
        lock = store.get_tag_lock(tag)
    if lock:
        raise HTTPException(status_code=409, detail={"error": "run_active", "message": f"Tag {tag!r} has an active run: {lock['run_id']}", "active_run_id": lock["run_id"]})
    active = find_active_job(RUNTIME, tag, "faiss_qa")
    if active:
        return safe_json({**active, "started": False})
    cpath = classified_path(tag)
    if not cpath.exists():
        raise HTTPException(
            status_code=400,
            detail="No classified CSV found for this tag. Run classification first.",
        )
    pcfg = provider_config(request.provider, request.model, request.api_key)
    env: dict[str, str] = {}
    if pcfg.api_key:
        env[pcfg.api_key_env] = pcfg.api_key
    extra_args = [
        "--classified_path", str(cpath),
        "--embedding_model", request.embedding_model,
        "--provider", pcfg.provider,
        "--base_url", pcfg.base_url,
        "--model", pcfg.model,
        "--api_key_env", pcfg.api_key_env,
    ]
    status = start_job(
        RUNTIME, tag, "faiss_qa",
        ROOT / "scripts" / "faiss_qa_job.py",
        extra_args,
        env=env,
        cwd=ROOT,
    )
    return safe_json({**status, "started": True})


@app.get("/api/qa/status")
def qa_status(tag: str = DEFAULT_TAG, job_id: str = "") -> JSONResponse:
    """Return the most recent Q&A index build job status."""
    tag = clean_tag(tag)
    if job_id:
        status = read_status(job_path(RUNTIME, tag, job_id))
    else:
        status = find_active_job(RUNTIME, tag, "faiss_qa")
        if not status:
            d = RUNTIME / tag / "jobs"
            if d.exists():
                all_jobs = [
                    s for p in d.glob("*.json")
                    if (s := read_status(p)) and s.get("kind") == "faiss_qa"
                ]
                if all_jobs:
                    status = max(all_jobs, key=lambda s: float(s.get("started_at", 0)))
    if not status:
        return safe_json({"state": "idle", "tag": tag})
    return safe_json(status)


@app.post("/api/qa/search")
def qa_search(request: QaSearchRequest) -> JSONResponse:
    """Return top-k retrieved evidence docs for a query (no LLM generation)."""
    from src.qa_retrieval import load_qa_artifacts, retrieve
    tag = clean_tag(request.tag)
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="query must not be empty.")
    try:
        docs, index, metadata = load_qa_artifacts(RUNTIME, tag)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    pcfg = provider_config(request.provider, request.model, request.api_key)
    if pcfg.api_key:
        os.environ[pcfg.api_key_env] = pcfg.api_key
    try:
        hits = retrieve(
            request.query, docs, index, pcfg,
            model=metadata.get("model", "text-embedding-3-small"),
            k=max(1, min(request.k, 20)),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Retrieval error: {exc}") from exc
    return safe_json({"ok": True, "query": request.query, "hits": hits, "metadata": metadata})


@app.post("/api/qa/answer")
def qa_answer(request: QaAnswerRequest) -> JSONResponse:
    """Retrieve evidence and generate an LLM-grounded answer."""
    from src.qa_retrieval import answer_question, load_qa_artifacts, retrieve
    tag = clean_tag(request.tag)
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty.")
    try:
        docs, index, metadata = load_qa_artifacts(RUNTIME, tag)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    pcfg = provider_config(request.provider, request.model, request.api_key)
    if pcfg.api_key:
        os.environ[pcfg.api_key_env] = pcfg.api_key
    try:
        hits = retrieve(
            request.question, docs, index, pcfg,
            model=metadata.get("model", "text-embedding-3-small"),
            k=max(1, min(request.k, 20)),
        )
        answer = answer_question(request.question, hits, pcfg)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Q&A error: {exc}") from exc
    return safe_json({"ok": True, "question": request.question, "answer": answer, "hits": hits})


@app.post("/api/analyze")
def analyze(request: AnalyzeRequest) -> JSONResponse:
    """Launch the full analysis pipeline as a durable run in the SQLite ledger."""
    tag = clean_tag(request.tag)
    run_id = uuid.uuid4().hex
    owner_id = uuid.uuid4().hex
    pcfg = provider_config(request.provider, request.model, request.api_key)
    n_clusters = max(2, request.n_clusters)
    prompt_provenance = cluster_prompt_provenance(load_config())
    prompt_snapshot_path = RUNTIME / tag / "runs" / run_id / "config" / "cluster_prompt.json"
    run_config = {
        "provider": pcfg.provider,
        "model": pcfg.model,
        "base_url": pcfg.base_url,
        "api_key_env": pcfg.api_key_env,
        "n_clusters": n_clusters,
        "embedding_model": "text-embedding-3-small",
        "cluster_prompt_sha256": prompt_provenance["sha256"],
    }

    with RunStore(RUNTIME / "runs.db") as store:
        reservation = store.reserve_new_run(
            tag,
            run_id=run_id,
            owner_id=owner_id,
            pid=os.getpid(),
            config=run_config,
            step_names=STEP_ORDER,
        )
    if not reservation.acquired:
        return run_active_response(tag, reservation.active_run_id or "unknown")

    try:
        write_cluster_prompt_snapshot(prompt_snapshot_path, prompt_provenance)
    except Exception as exc:
        with RunStore(RUNTIME / "runs.db") as store:
            store.fail_reserved_run(tag, run_id=run_id, owner_id=owner_id, warning=str(exc))
        raise HTTPException(status_code=500, detail=f"Prompt snapshot failed: {exc}") from exc

    # API key goes into the subprocess env, never on the CLI
    env: dict[str, str] = {}
    if pcfg.api_key:
        env[pcfg.api_key_env] = pcfg.api_key

    extra_args = [
        "--tag", tag,
        "--runtime_root", str(RUNTIME),
        "--run_id", run_id,
        "--db_path", str(RUNTIME / "runs.db"),
        "--provider", pcfg.provider,
        "--base_url", pcfg.base_url,
        "--model", pcfg.model,
        "--api_key_env", pcfg.api_key_env,
        "--n_clusters", str(n_clusters),
        "--cluster_prompt_path", str(prompt_snapshot_path),
        "--strict-adoption",
    ]
    try:
        status = start_job(
            RUNTIME,
            tag,
            "analyze",
            ROOT / "scripts" / "analyze_run.py",
            extra_args,
            env=env,
            cwd=ROOT,
            job_id=owner_id,
        )
        with RunStore(RUNTIME / "runs.db") as store:
            if not store.attach_reserved_pid(
                tag,
                run_id=run_id,
                owner_id=owner_id,
                pid=int(status["pid"]),
            ):
                raise RuntimeError("Coordinator reservation disappeared during launch.")
    except Exception as exc:
        with RunStore(RUNTIME / "runs.db") as store:
            store.fail_reserved_run(
                tag,
                run_id=run_id,
                owner_id=owner_id,
                warning=str(exc),
            )
        raise HTTPException(status_code=500, detail=f"Pipeline launch failed: {exc}") from exc
    return safe_json({"run_id": run_id, "job_id": owner_id})


@app.get("/api/pipeline/status")
def pipeline_status(tag: str = DEFAULT_TAG, run_id: str = "") -> JSONResponse:
    """Return the full status of one pipeline run including per-step details."""
    tag = clean_tag(tag)
    with RunStore(RUNTIME / "runs.db") as store:
        store.reconcile_pending_run(run_id)
        run = require_pipeline_run(store, tag, run_id)
        steps_rows = store.get_steps(run_id, STEP_ORDER)
        # Build per-step dicts with artifact URLs and log availability
        steps = []
        for row in steps_rows:
            step = row["name"]
            attempts = store.get_attempts(run_id, step)
            latest = attempts[-1] if attempts else None
            # Map attempt artifacts to API-friendly shapes
            artifacts = []
            if latest:
                for i, a in enumerate(latest.get("artifacts", [])):
                    apath = Path(a["path"])
                    artifacts.append({
                        "id": f"{step}-{i}",
                        "name": apath.name,
                        "bytes": apath.stat().st_size if apath.exists() else 0,
                        "url": f"/api/pipeline/artifact?tag={tag}&run_id={run_id}&step={step}&id={i}",
                    })
            # Log is available if the latest attempt has a non-empty log file
            log_available = False
            if latest and latest.get("log_path"):
                lpath = Path(latest["log_path"])
                log_available = lpath.exists() and lpath.stat().st_size > 0
            steps.append({
                "name": step,
                "state": row["state"],
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
                "processed": row["processed"],
                "total": row["total"],
                "errors": row["errors"],
                "error_rate": row["error_rate"],
                "warning": row["warning"],
                "artifacts": artifacts,
                "log_available": log_available,
            })
    return safe_json({**run, "steps": steps})


@app.get("/api/pipeline/runs")
def pipeline_runs(tag: str = DEFAULT_TAG, limit: int = 20) -> JSONResponse:
    """List recent pipeline runs for a tag."""
    tag = clean_tag(tag)
    with RunStore(RUNTIME / "runs.db") as store:
        for pending in store.list_runs(tag, limit=min(limit, 100)):
            if pending["state"] == "pending":
                store.reconcile_pending_run(pending["run_id"])
        runs = store.list_runs(tag, limit=min(limit, 100))
    return safe_json(runs)


@app.get("/api/pipeline/log")
def pipeline_log(tag: str = DEFAULT_TAG, run_id: str = "", step: str = "") -> Response:
    """Return the tail of the step log for the latest attempt."""
    tag = clean_tag(tag)
    with RunStore(RUNTIME / "runs.db") as store:
        # Distinguish a missing run from a run that exists but has no attempts
        require_pipeline_run(store, tag, run_id)
        attempts = store.get_attempts(run_id, step)
    if not attempts:
        raise HTTPException(status_code=404, detail=f"No attempts found for {run_id}/{step}.")
    latest = attempts[-1]
    log_path = latest.get("log_path")
    if not log_path:
        raise HTTPException(status_code=404, detail="Log file not found.")
    safe_log_path = require_snapshot_path(tag, run_id, log_path)
    if not safe_log_path.is_file():
        raise HTTPException(status_code=404, detail="Log file not found.")
    text = tail_text(safe_log_path)
    return Response(content=text, media_type="text/plain")


@app.get("/api/pipeline/artifact")
def pipeline_artifact(tag: str = DEFAULT_TAG, run_id: str = "", step: str = "", id: str = "0") -> Response:
    """Download a specific artifact by index from the latest attempt of a step."""
    tag = clean_tag(tag)
    with RunStore(RUNTIME / "runs.db") as store:
        # Distinguish a missing run from a run that exists but has no attempts
        require_pipeline_run(store, tag, run_id)
        attempts = store.get_attempts(run_id, step)
    if not attempts:
        raise HTTPException(status_code=404, detail="No attempts found.")
    latest = attempts[-1]
    artifacts = latest.get("artifacts", [])
    try:
        idx = int(id)
        if idx < 0:
            raise IndexError
        artifact = artifacts[idx]
    except (ValueError, IndexError):
        raise HTTPException(status_code=404, detail=f"Artifact index {id!r} not found.")
    apath = require_snapshot_path(tag, run_id, artifact["path"])
    if not apath.is_file():
        raise HTTPException(status_code=404, detail="Artifact file not found on disk.")
    content_type = mimetypes.guess_type(str(apath))[0] or "application/octet-stream"
    return FileResponse(str(apath), media_type=content_type)


@app.post("/api/pipeline/cancel")
def pipeline_cancel(request: PipelineCancelRequest) -> JSONResponse:
    """Cancel an active pipeline run by sending SIGTERM to its process."""
    tag = clean_tag(request.tag)
    with RunStore(RUNTIME / "runs.db") as store:
        lock = store.get_tag_lock(tag)
        # Only cancel if the lock matches the requested run_id
        if not lock or lock["run_id"] != request.run_id:
            return safe_json({"cancelled": False, "state": "not_active"})
        # Stop the coordinator and any active child worker so cancellation does
        # not leave an orphan writing into this tag.
        active_jobs = [find_active_job(RUNTIME, tag, "analyze")]
        active_jobs.extend(
            find_active_job(RUNTIME, tag, kind) for kind in PIPELINE_CHILD_JOB_KINDS
        )
        for active in active_jobs:
            pid = active.get("pid") if active else None
            if pid:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except OSError:
                    pass
        # Mark the run and unfinished step projection as cancelled in the ledger.
        cancel_run_steps(store, request.run_id)
        try:
            store.update_run(
                request.run_id,
                state="cancelled",
                ended_at=time.time(),
                warning="Cancelled by user.",
            )
        except KeyError:
            pass
        # Release the tag lock so the next run can start
        store.release_tag_lock(tag, lock["owner_id"])
    return safe_json({"cancelled": True, "state": "cancelled"})


@app.post("/api/pipeline/retry")
def pipeline_retry(request: PipelineRetryRequest) -> JSONResponse:
    """Retry a failed/completed run from a specific step (re-runs the step and its dependents)."""
    tag = clean_tag(request.tag)
    if request.step not in STEP_ORDER:
        raise HTTPException(status_code=400, detail=f"Unknown step {request.step!r}. Valid: {STEP_ORDER}")

    owner_id = uuid.uuid4().hex
    steps = retry_steps(request.step)
    with RunStore(RUNTIME / "runs.db") as store:
        run = require_pipeline_run(store, tag, request.run_id)
        if run["state"] in {"pending", "running"}:
            raise HTTPException(status_code=400, detail="Run is still active; cancel it first.")
        reservation = store.reserve_retry(
            tag,
            run_id=request.run_id,
            owner_id=owner_id,
            pid=os.getpid(),
            step_names=steps,
        )
    if not reservation.acquired:
        return run_active_response(tag, reservation.active_run_id or "unknown")

    # Determine provider from stored run config, fall back to defaults
    run_config = run.get("config") or {}
    pcfg = provider_config(
        run_config.get("provider", "openrouter"),
        run_config.get("model", "gpt-oss-120b"),
        request.api_key,
    )
    env: dict[str, str] = {}
    if pcfg.api_key:
        env[pcfg.api_key_env] = pcfg.api_key

    prompt_snapshot_path = RUNTIME / tag / "runs" / request.run_id / "config" / "cluster_prompt.json"
    if not prompt_snapshot_path.exists():
        write_cluster_prompt_snapshot(prompt_snapshot_path, cluster_prompt_provenance(load_config()))

    extra_args = [
        "--tag", tag,
        "--runtime_root", str(RUNTIME),
        "--run_id", request.run_id,
        "--db_path", str(RUNTIME / "runs.db"),
        "--provider", pcfg.provider,
        "--base_url", pcfg.base_url,
        "--model", pcfg.model,
        "--api_key_env", pcfg.api_key_env,
        "--n_clusters", str(max(2, run_config.get("n_clusters", 10))),
        "--cluster_prompt_path", str(prompt_snapshot_path),
        "--retry-from-step", request.step,
        "--strict-adoption",
    ]
    try:
        status = start_job(
            RUNTIME,
            tag,
            "analyze",
            ROOT / "scripts" / "analyze_run.py",
            extra_args,
            env=env,
            cwd=ROOT,
            job_id=owner_id,
        )
        with RunStore(RUNTIME / "runs.db") as store:
            if not store.attach_reserved_pid(
                tag,
                run_id=request.run_id,
                owner_id=owner_id,
                pid=int(status["pid"]),
            ):
                raise RuntimeError("Coordinator reservation disappeared during retry launch.")
    except Exception as exc:
        with RunStore(RUNTIME / "runs.db") as store:
            store.fail_reserved_run(
                tag,
                run_id=request.run_id,
                owner_id=owner_id,
                warning=str(exc),
            )
        raise HTTPException(status_code=500, detail=f"Pipeline retry failed: {exc}") from exc
    return safe_json({"run_id": request.run_id, "job_id": owner_id})


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "server": "fastapi",
        "legacy_collector_configured": LEGACY_ROOT is not None,
        "legacy_collector_found": bool(LEGACY_ROOT and LEGACY_ROOT.exists()),
        "legacy_collector_root": str(LEGACY_ROOT) if LEGACY_ROOT else "",
        "runtime": str(RUNTIME),
    }
