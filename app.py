"""FastAPI server for the redditgm v2 web application."""

from __future__ import annotations

import io
import json
import math
import os
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.charts import build_chart_payload, build_detail_data
# Bare import so test mocks (patch "app.start_job", "app.find_active_job") resolve correctly
from src.jobs import find_active_job, job_path, read_status, start_job
from src.gm_insights import (
    MIN_CELL,
    ProviderConfig,
    all_complaint_mentions,
    apply_labels,
    classify_preview,
    classify_upload_kind,
    classify_with_llm,
    complete_label,
    cooccurrence,
    complaint_summary,
    ev_comparison,
    evidence_table,
    fallback_synthesis,
    filter_analyzed,
    flag_summary,
    generate_synthesis_with_llm,
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
RUNTIME = ROOT / "runtime"
WEB = ROOT / "web"
LEGACY_ROOT = Path(os.getenv("REDDITGM_LEGACY_ROOT", "/Users/ricopichardo/Claude/redditgm"))
DEFAULT_TAG = "gm_vehicle_on_demand"
COLLECT_JOBS: dict[str, dict[str, Any]] = {}

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


def safe_json(data: Any) -> JSONResponse:
    return JSONResponse(_sanitize(data))


app = FastAPI(title="redditgm v2", version="2.0.0")
app.mount("/static", StaticFiles(directory=WEB), name="static")


class CollectRequest(BaseModel):
    tag: str = DEFAULT_TAG
    source: str = "gm"
    subreddits: str = ""
    listing_limit: int = 100
    comments_limit: int = 5
    since_days: int = 0
    dry_run: bool = False


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


def run_dir(tag: str) -> Path:
    return RUNTIME / clean_tag(tag)


def data_dir(tag: str) -> Path:
    return run_dir(tag) / "data"


def classified_path(tag: str) -> Path:
    return run_dir(tag) / "classified" / "classified_posts.csv"


def report_path(tag: str) -> Path:
    return run_dir(tag) / "reports" / "gm_reddit_synthesis_report.md"


def collect_log_path(tag: str) -> Path:
    return run_dir(tag) / "collect" / "latest.log"


def manifest_path(tag: str) -> Path:
    return run_dir(tag) / "runs" / "run_manifest.jsonl"


def download_dir(tag: str) -> Path:
    return run_dir(tag) / "downloads"


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
    key = source.lower().strip()
    if key == "gm":
        return LEGACY_ROOT / "config" / "gm_vehicle_subreddits.txt"
    if key == "competitor":
        return LEGACY_ROOT / "config" / "competitor_subreddits.txt"
    if key == "custom":
        path = run_dir(tag) / "config" / "subreddits.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(subreddits.strip() + "\n", encoding="utf-8")
        return path
    raise HTTPException(status_code=400, detail="source must be gm, competitor, or custom.")


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

    source_file = Path(job["subreddits_file"]) if job and job.get("subreddits_file") else LEGACY_ROOT / "config" / "gm_vehicle_subreddits.txt"
    total_subreddits = int(job.get("total_subreddits", 0)) if job else len(read_subreddit_file(source_file))
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
    return ProviderConfig(
        provider=key,
        model=model.strip() or info["model"],
        base_url=info["base_url"],
        api_key_env=info["api_key_env"],
        api_key=api_key.strip(),
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
    if cpath.exists():
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
        evidence = dataframe_records(evidence_table(selected, limit=200), limit=200)
        # New Phase 1 chart contract: chart_specs + chart_data.
        chart_contract = build_chart_payload(selected)
    return {
        "tag": clean_tag(tag),
        "paths": {
            "runtime": str(run_dir(tag)),
            "data": str(data_dir(tag)),
            "classified": str(classified_path(tag)),
            "report": str(report_path(tag)),
            "collector": str(LEGACY_ROOT),
        },
        "status": {
            "has_source": not load_runtime_frame(data_dir(tag)).empty,
            "has_classified": classified_path(tag).exists(),
            "has_report": report_path(tag).exists(),
            "legacy_collector_found": LEGACY_ROOT.exists(),
            "source_files": source_download_entries(tag),
        },
        "summary": payload,
        "charts": charts,
        # Phase 1 chart contract — browser and export renderers should prefer these.
        "chart_specs": chart_contract["chart_specs"],
        "chart_data": chart_contract["chart_data"],
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
) -> dict[str, Any]:
    return {
        "sentiment": sentiment or [],
        "vehicle": vehicle or [],
        "subreddit": subreddit or [],
        "severity": severity or [],
        "comment_type": comment_type or [],
        "competitor": competitor or [],
        "search": search,
        "min_score": min_score,
    }


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((WEB / "index.html").read_text(encoding="utf-8"))


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
) -> JSONResponse:
    filters = request_filters(sentiment, vehicle, subreddit, severity, comment_type, competitor, search, min_score)
    return safe_json(run_snapshot(tag, filters))


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
) -> JSONResponse:
    """Serve lazy chart data (category_by_model heatmap and flag co-occurrence).
    Accepts the same filter params as /api/run so the browser can pass them through."""
    df = load_frame(tag)
    filters = request_filters(sentiment, vehicle, subreddit, severity, comment_type, competitor, search, min_score)
    selected = filter_analyzed(df, filters) if not df.empty else pd.DataFrame()
    return JSONResponse(build_detail_data(selected))


@app.post("/api/upload")
def upload_csv(tag: str = DEFAULT_TAG, file: UploadFile = File(...)) -> JSONResponse:
    df = read_upload(file)
    path = save_upload(tag, file, df)
    return safe_json({"ok": True, "path": str(path), "kind": classify_upload_kind(df), "rows": len(df)})


@app.post("/api/collect")
def collect_data(request: CollectRequest) -> JSONResponse:
    if not LEGACY_ROOT.exists():
        raise HTTPException(status_code=400, detail=f"Collector folder not found: {LEGACY_ROOT}")

    tag = clean_tag(request.tag)
    active = COLLECT_JOBS.get(tag)
    if active and active.get("process") and active["process"].poll() is None:
        payload = collect_status_payload(tag, active.get("job_id", ""))
        payload["started"] = False
        return safe_json(payload)

    subreddits_file = source_subreddits_file(request.source, tag, request.subreddits)

    if not subreddits_file.exists():
        raise HTTPException(status_code=400, detail=f"Subreddit file not found: {subreddits_file}")

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
    if request.since_days:
        cmd.extend(["--since-days", str(max(0, request.since_days))])
    if request.dry_run:
        cmd.append("--dry-run")

    log_path = collect_log_path(tag)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_start = len(read_manifest(tag))
    job_id = uuid.uuid4().hex
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
    payload = summary_payload(df)
    if request.use_llm:
        report = generate_synthesis_with_llm(payload, provider_config(request.provider, request.model, request.api_key))
    else:
        report = fallback_synthesis(payload)
    path = report_path(request.tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: temp file then replace so a partial write is never visible
    tmp = path.with_suffix(".tmp")
    tmp.write_text(report, encoding="utf-8")
    os.replace(tmp, path)
    return safe_json({"ok": True, "path": str(path), "report": report})


@app.post("/api/classify/job")
def classify_job(request: ClassifyJobRequest) -> JSONResponse:
    """Start a full-run LLM classify subprocess. Returns existing job if one is running."""
    tag = clean_tag(request.tag)
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
    if request.api_key:
        env[pcfg.api_key_env] = request.api_key
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
    """Start a PDF export subprocess job (stub — Phase 3 fills real rendering)."""
    tag = clean_tag(request.tag)
    active = find_active_job(RUNTIME, tag, "pdf_export")
    if active:
        return safe_json({**active, "started": False})
    extra_args = ["--kind", request.kind]
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


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "server": "fastapi",
        "legacy_collector_found": LEGACY_ROOT.exists(),
        "runtime": str(RUNTIME),
    }
