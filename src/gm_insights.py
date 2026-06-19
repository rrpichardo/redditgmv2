"""Notebook-derived GM Reddit classification, analytics, and briefing helpers."""

from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

try:
    from json_repair import repair_json
except Exception:  # pragma: no cover - optional at import time
    repair_json = None


EXPECTED_KEYS = [
    "sentiment",
    "complaint",
    "competitor_mention",
    "dealer_experience",
    "reliability_concern",
    "software_tech_issue",
    "purchase_intent",
    "loyalty_signal",
    "ev_topic",
    "enthusiast_mod",
    "classic_vintage",
    "top_complaint_category",
    "multi_complaint_categories",
    "vehicle_mentioned",
    "comment_type",
    "competitor_brand",
    "issue_severity",
    "description",
]

BINARY_FLAGS = [
    "complaint",
    "competitor_mention",
    "dealer_experience",
    "reliability_concern",
    "software_tech_issue",
    "purchase_intent",
    "loyalty_signal",
    "ev_topic",
    "enthusiast_mod",
    "classic_vintage",
]

STRING_DEFAULTS = {
    "sentiment": "neutral",
    "top_complaint_category": "not_applicable",
    "vehicle_mentioned": "unknown",
    "comment_type": "off_topic_noise",
    "competitor_brand": "none",
    "issue_severity": "none",
    "description": "",
}

COMPLAINT_CATEGORIES = [
    "transmission_torque_converter",
    "engine_lifter_afm_dod",
    "electrical_battery_alternator",
    "software_infotainment_ota",
    "ac_hvac",
    "suspension_steering",
    "rust_body_paint_quality",
    "dealership_service_wait",
    "pricing_fees_financing",
    "subscription_model",
    "parts_availability_backorder",
    "recall_warranty_lemon_law",
    "other",
    "not_applicable",
]

VEHICLE_ALIASES = {
    "silverado_ev": ["silverado ev"],
    "equinox_ev": ["equinox ev"],
    "blazer_ev": ["blazer ev"],
    "hummer_ev": ["hummer ev"],
    "silverado": ["silverado"],
    "sierra": ["sierra"],
    "colorado": ["colorado"],
    "canyon": ["canyon"],
    "tahoe": ["tahoe"],
    "yukon": ["yukon"],
    "suburban": ["suburban"],
    "traverse": ["traverse"],
    "equinox": ["equinox"],
    "trax": ["trax"],
    "trailblazer": ["trailblazer"],
    "blazer": ["blazer"],
    "camaro": ["camaro"],
    "corvette": ["corvette", "vette"],
    "malibu": ["malibu"],
    "bolt": ["bolt"],
    "lyriq": ["lyriq"],
    "escalade": ["escalade"],
    "ct4": ["ct4"],
    "ct5": ["ct5"],
    "xt4": ["xt4"],
    "xt5": ["xt5"],
    "xt6": ["xt6"],
    "encore": ["encore"],
    "enclave": ["enclave"],
}

COMPETITOR_ALIASES = {
    "ford": ["ford", "f-150", "f150", "bronco", "mustang"],
    "toyota": ["toyota", "tacoma", "tundra", "camry", "rav4"],
    "ram": ["ram", "1500", "2500"],
    "tesla": ["tesla", "model 3", "model y", "cybertruck"],
    "honda": ["honda", "accord", "civic", "ridgeline"],
    "hyundai_kia": ["hyundai", "kia", "ioniq", "telluride", "ev6"],
    "nissan": ["nissan", "frontier", "altima"],
    "jeep": ["jeep", "wrangler", "grand cherokee"],
}

# Minimum cell count for noisy model/category detail charts.
# Charts that cross-tab vehicles × categories suppress cells below this threshold
# to prevent misleading rates from tiny samples.
MIN_CELL = 5

CLASSIFICATION_SYSTEM_PROMPT = """
You are an expert automotive consumer insights analyst working for General Motors.
You will receive one Reddit comment with the original post as context. The original
post is context only. Classify the comment itself.

Return valid JSON with exactly these fields:
- sentiment: positive, neutral, or negative view of GM/Chevrolet/GMC/Cadillac/Buick.
- complaint: 1 or 0, whether the comment describes a problem, defect, failure, or frustration.
- competitor_mention: 1 or 0, whether it mentions Ford, Toyota, Ram, Tesla, Honda, Hyundai, Kia, Nissan, Jeep, or another competitor.
- dealer_experience: 1 or 0, whether it discusses a GM dealer or customer service experience.
- reliability_concern: 1 or 0, whether it raises long-term reliability, defect, or recurring failure concerns.
- software_tech_issue: 1 or 0, whether it concerns infotainment, OTA, Android Auto, CarPlay, OnStar, or software.
- purchase_intent: 1 or 0, whether the commenter is considering buying, leasing, or trading into a GM vehicle.
- loyalty_signal: 1 or 0, whether it expresses long-term GM ownership, loyalty, or attachment.
- ev_topic: 1 or 0, whether the comment or original post concerns GM electric vehicles.
- enthusiast_mod: 1 or 0, whether it is mainly about modifications or performance builds.
- classic_vintage: 1 or 0, whether it is mainly about a pre-1990 classic GM vehicle.
- top_complaint_category: one of transmission_torque_converter, engine_lifter_afm_dod, electrical_battery_alternator, software_infotainment_ota, ac_hvac, suspension_steering, rust_body_paint_quality, dealership_service_wait, pricing_fees_financing, subscription_model, parts_availability_backorder, recall_warranty_lemon_law, other, not_applicable.
- multi_complaint_categories: list of additional complaint categories from the same list.
- vehicle_mentioned: one of silverado, sierra, colorado, canyon, tahoe, yukon, suburban, traverse, equinox, trax, trailblazer, blazer, camaro, corvette, malibu, bolt, equinox_ev, silverado_ev, blazer_ev, hummer_ev, lyriq, escalade, ct4, ct5, xt4, xt5, xt6, encore, enclave, other_gm, unknown.
- comment_type: complaint, advice_recommendation, shared_experience, question, praise, comparison, or off_topic_noise.
- competitor_brand: ford, toyota, ram, tesla, honda, hyundai_kia, nissan, jeep, other, or none.
- issue_severity: critical, major, minor, or none.
- description: one single-line, 1-3 sentence actionable insight for GM.

Use neutral/0/none/not_applicable when the comment carries no signal.
Output only JSON. Do not include markdown.
"""

SYNTHESIS_SYSTEM_PROMPT = """
You are a senior automotive consumer insights strategist preparing a briefing for
GM product and strategy teams. Produce a professional markdown report with:
1. Executive Summary
2. Top Complaint Themes
3. Complaints by Model
4. EV vs. Non-EV Findings
5. Prioritization - What to Fix First
6. Competitor Signal
7. Recommended Actions
8. Final Summary - What This Means for GM
9. Open Questions / Next Steps

Use specific numbers from the supplied payload. Where sample sizes are small,
call findings directional rather than conclusive. Output only the markdown report.
"""


def _get_classification_prompt() -> str:
    try:
        from src.app_config import load_config
        p = load_config().get("prompts", {}).get("classification", "").strip()
        return p if p else CLASSIFICATION_SYSTEM_PROMPT
    except Exception:
        return CLASSIFICATION_SYSTEM_PROMPT


def _get_synthesis_prompt() -> str:
    try:
        from src.app_config import load_config
        p = load_config().get("prompts", {}).get("synthesis", "").strip()
        return p if p else SYNTHESIS_SYSTEM_PROMPT
    except Exception:
        return SYNTHESIS_SYSTEM_PROMPT


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    model: str
    base_url: str
    api_key_env: str
    api_key: str = ""


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    return str(value)


def _first_present(row: pd.Series, names: Iterable[str]) -> str:
    for name in names:
        if name in row:
            value = _text(row[name]).strip()
            if value:
                return value
    return ""


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def load_runtime_frame(data_dir: Path) -> pd.DataFrame:
    """Load the current run's combined or split Reddit CSVs."""
    combined = data_dir / "gm_posts_with_comments.csv"
    posts = data_dir / "gm_posts.csv"
    comments = data_dir / "gm_comments.csv"

    combined_mtime = combined.stat().st_mtime if combined.exists() else -1.0
    split_mtime = max(
        posts.stat().st_mtime if posts.exists() else -1.0,
        comments.stat().st_mtime if comments.exists() else -1.0,
    )
    if combined.exists() and combined_mtime >= split_mtime:
        return _read_csv(combined)
    if posts.exists() and comments.exists():
        post_df = _read_csv(posts)
        comment_df = _read_csv(comments)
        merged = comment_df.merge(post_df, left_on="post_id", right_on="id", how="left", suffixes=("_comment", "_post"))
        merged = merged.rename(
            columns={
                "post_title": "post_title",
                "post_subreddit": "post_subreddit",
                "body": "comment_body",
                "score_comment": "comment_score",
                "score_post": "post_score",
                "created_at": "post_created_at",
                "selftext": "post_selftext",
                "content": "post_content",
                "permalink_post": "post_permalink",
                "permalink_comment": "comment_permalink",
            }
        )
        return merged
    if posts.exists():
        return _read_csv(posts)
    if comments.exists():
        return _read_csv(comments)
    return pd.DataFrame()


def classify_upload_kind(df: pd.DataFrame) -> str:
    cols = set(df.columns)
    if {"post_id", "post_title"}.issubset(cols) and ("comment_body" in cols or "post_content" in cols):
        return "combined"
    if {"id", "title", "subreddit"}.issubset(cols):
        return "posts"
    if {"comment_id", "post_id"}.issubset(cols):
        return "comments"
    if set(EXPECTED_KEYS).issubset(cols):
        return "classified"
    return "unknown"


def normalize_reddit_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """Return one analysis row per comment when available, otherwise per post."""
    if raw.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    kind = classify_upload_kind(raw)
    has_imported_labels = set(EXPECTED_KEYS).issubset(raw.columns)

    for idx, row in raw.reset_index(drop=True).iterrows():
        if kind in {"combined", "classified"} or "post_title" in raw.columns:
            comment_id = _first_present(row, ["comment_id"])
            post_id = _first_present(row, ["post_id", "id", "post_id_norm"])
            source_type = "comment" if comment_id else "post"
            source_id = _first_present(row, ["source_id"]) or comment_id or post_id or f"row_{idx}"
            subreddit = _first_present(row, ["post_subreddit", "subreddit", "subreddit_norm"])
            title = _first_present(row, ["post_title", "title", "title_norm"])
            post_body = _first_present(row, ["post_selftext", "post_content", "selftext", "content"])
            target_text = _first_present(row, ["target_text", "comment_body", "body"])
            if not target_text:
                target_text = post_body or title
            score = _first_present(row, ["score_norm", "comment_score", "score", "post_score"])
            created_at = _first_present(row, ["created_at_norm", "post_created_at", "created_at", "downloaded_at"])
            permalink = _first_present(row, ["permalink_norm", "comment_permalink", "post_permalink", "permalink"])
        elif kind == "posts":
            post_id = _first_present(row, ["id"])
            source_type = "post"
            source_id = post_id or f"row_{idx}"
            subreddit = _first_present(row, ["subreddit"])
            title = _first_present(row, ["title"])
            post_body = _first_present(row, ["selftext", "content"])
            target_text = post_body or title
            score = _first_present(row, ["score"])
            created_at = _first_present(row, ["created_at", "downloaded_at"])
            permalink = _first_present(row, ["permalink"])
        else:
            source_type = "row"
            source_id = _first_present(row, ["source_id", "id", "comment_id", "post_id"]) or f"row_{idx}"
            subreddit = _first_present(row, ["subreddit", "post_subreddit"])
            title = _first_present(row, ["title", "post_title"])
            post_body = _first_present(row, ["selftext", "content", "post_selftext", "post_content"])
            target_text = _first_present(row, ["comment_body", "body", "text", "description"]) or post_body or title
            score = _first_present(row, ["comment_score", "score", "post_score"])
            created_at = _first_present(row, ["created_at", "post_created_at", "downloaded_at"])
            permalink = _first_present(row, ["permalink", "comment_permalink", "post_permalink"])
            post_id = _first_present(row, ["post_id", "id"])

        combined_text = (
            "=== ORIGINAL POST (CONTEXT ONLY) ===\n"
            f"Subreddit: r/{subreddit}\n"
            f"Title: {title}\n"
            f"Post body: {post_body}\n\n"
            "=== COMMENT OR POST TO ANALYZE ===\n"
            f"{target_text}"
        ).strip()

        record = row.to_dict()
        record.update(
            {
                "source_id": source_id,
                "source_type": source_type,
                "post_id_norm": post_id,
                "subreddit_norm": subreddit,
                "title_norm": title,
                "target_text": target_text,
                "combined_text": combined_text,
                "score_norm": pd.to_numeric(score, errors="coerce"),
                "created_at_norm": pd.to_datetime(created_at, errors="coerce"),
                "permalink_norm": permalink,
            }
        )
        rows.append(record)

    normalized = pd.DataFrame(rows)
    normalized["skip_classification"] = normalized["target_text"].apply(is_junk_text)
    normalized = ensure_label_columns(normalized)
    if has_imported_labels and "classifier_mode" in normalized:
        normalized.loc[normalized["classifier_mode"].fillna("").eq(""), "classifier_mode"] = "imported"
    return normalized


def ensure_label_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for key in EXPECTED_KEYS:
        if key not in out.columns:
            if key == "multi_complaint_categories":
                out[key] = ""
            elif key in STRING_DEFAULTS:
                out[key] = STRING_DEFAULTS[key]
            else:
                out[key] = 0
    for key in BINARY_FLAGS:
        out[key] = pd.to_numeric(out[key], errors="coerce").fillna(0).astype(int)
    out["sentiment"] = out["sentiment"].replace("", "neutral")
    out["classifier_mode"] = out.get("classifier_mode", "")
    out = add_engagement_level(out)
    return out


def is_junk_text(value: Any) -> bool:
    text = _text(value).strip()
    if len(text) < 15:
        return True
    return bool(re.match(r"^\[(deleted|removed)\]$", text, flags=re.IGNORECASE))


def add_engagement_level(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    scores = pd.to_numeric(out.get("score_norm", pd.Series(dtype=float)), errors="coerce")
    if scores.notna().sum() >= 3:
        try:
            out["engagement_level"] = pd.qcut(
                scores.rank(method="first"),
                q=3,
                labels=["low", "medium", "high"],
            ).astype(str)
        except ValueError:
            out["engagement_level"] = "unknown"
    else:
        out["engagement_level"] = "unknown"
    return out


def _contains_any(text: str, needles: Iterable[str]) -> bool:
    haystack = text.lower()
    return any(needle in haystack for needle in needles)


def infer_vehicle(text: str) -> str:
    lower = text.lower()
    for vehicle, aliases in VEHICLE_ALIASES.items():
        if any(re.search(rf"\b{re.escape(alias)}\b", lower) for alias in aliases):
            return vehicle
    if _contains_any(lower, ["chevy", "chevrolet", "gmc", "cadillac", "buick", "gm "]):
        return "other_gm"
    return "unknown"


def infer_competitor(text: str) -> str:
    lower = text.lower()
    for brand, aliases in COMPETITOR_ALIASES.items():
        if any(re.search(rf"\b{re.escape(alias)}\b", lower) for alias in aliases):
            return brand
    return "none"


def infer_complaint_category(text: str) -> str:
    lower = text.lower()
    rules = [
        ("transmission_torque_converter", ["transmission", "torque converter", "shudder", "shift"]),
        ("engine_lifter_afm_dod", ["lifter", "afm", "dod", "engine knock", "misfire"]),
        ("electrical_battery_alternator", ["battery", "alternator", "electrical", "starter"]),
        ("software_infotainment_ota", ["infotainment", "ota", "android auto", "carplay", "onstar", "screen"]),
        ("ac_hvac", ["ac ", "a/c", "hvac", "air conditioning", "heater"]),
        ("suspension_steering", ["suspension", "steering", "strut", "shock"]),
        ("rust_body_paint_quality", ["rust", "paint", "body panel", "clearcoat"]),
        ("dealership_service_wait", ["dealer", "dealership", "service advisor", "service department"]),
        ("pricing_fees_financing", ["price", "payment", "finance", "interest", "fee", "markup"]),
        ("subscription_model", ["subscription", "monthly fee", "paywall"]),
        ("parts_availability_backorder", ["backorder", "parts", "waiting on part"]),
        ("recall_warranty_lemon_law", ["recall", "warranty", "lemon"]),
    ]
    for category, needles in rules:
        if _contains_any(lower, needles):
            return category
    return "other"


def heuristic_label(row: pd.Series) -> dict[str, Any]:
    text = f"{_text(row.get('title_norm'))} {_text(row.get('target_text'))}".lower()
    complaint_words = [
        "problem",
        "issue",
        "fail",
        "failed",
        "broken",
        "repair",
        "lemon",
        "recall",
        "warranty",
        "hate",
        "bad",
        "terrible",
        "expensive",
        "wait",
        "backorder",
    ]
    praise_words = ["love", "great", "reliable", "excellent", "perfect", "happy", "best"]
    complaint = int(_contains_any(text, complaint_words))
    positive = _contains_any(text, praise_words)
    competitor_brand = infer_competitor(text)
    vehicle = infer_vehicle(text)
    ev_topic = int(_contains_any(text, [" ev", "electric", "ultium", "battery range", "charging", "bolt", "lyriq"]))
    software = int(_contains_any(text, ["infotainment", "ota", "android auto", "carplay", "onstar", "software", "screen"]))
    dealer = int(_contains_any(text, ["dealer", "dealership", "service advisor"]))
    reliability = int(_contains_any(text, ["reliable", "reliability", "lifter", "transmission", "engine", "recurring", "failure"]))
    severity = "none"
    if complaint:
        if _contains_any(text, ["unsafe", "safety", "undrivable", "lemon", "recall"]):
            severity = "critical"
        elif _contains_any(text, ["expensive", "warranty", "transmission", "engine", "lifter", "backorder"]):
            severity = "major"
        else:
            severity = "minor"
    if complaint:
        sentiment = "negative"
    elif positive:
        sentiment = "positive"
    else:
        sentiment = "neutral"
    if complaint:
        comment_type = "complaint"
    elif competitor_brand != "none":
        comment_type = "comparison"
    elif "?" in _text(row.get("target_text")):
        comment_type = "question"
    elif positive:
        comment_type = "praise"
    else:
        comment_type = "shared_experience"
    top_category = infer_complaint_category(text) if complaint else "not_applicable"
    return complete_label(
        {
            "sentiment": sentiment,
            "complaint": complaint,
            "competitor_mention": int(competitor_brand != "none"),
            "dealer_experience": dealer,
            "reliability_concern": reliability,
            "software_tech_issue": software,
            "purchase_intent": int(_contains_any(text, ["buy", "lease", "trade", "shopping", "considering"])),
            "loyalty_signal": int(_contains_any(text, ["owned gm", "always buy", "my third", "loyal", "family has"])),
            "ev_topic": ev_topic,
            "enthusiast_mod": int(_contains_any(text, ["mod", "swap", "turbo", "cammed", "lift kit"])),
            "classic_vintage": int(_contains_any(text, ["196", "197", "198", "classic", "vintage"])),
            "top_complaint_category": top_category,
            "multi_complaint_categories": [],
            "vehicle_mentioned": vehicle,
            "comment_type": comment_type,
            "competitor_brand": competitor_brand,
            "issue_severity": severity,
            "description": heuristic_description(row, sentiment, top_category, vehicle),
        }
    )


def heuristic_description(row: pd.Series, sentiment: str, category: str, vehicle: str) -> str:
    text = _text(row.get("target_text")).strip().replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    excerpt = text[:180] + ("..." if len(text) > 180 else "")
    if category != "not_applicable":
        return f"Preview classifier flagged a {category.replace('_', ' ')} signal for {vehicle}. Evidence excerpt: {excerpt}"
    return f"Preview classifier marked this as {sentiment} signal for {vehicle}. Evidence excerpt: {excerpt}"


def complete_label(label: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in EXPECTED_KEYS:
        if key == "multi_complaint_categories":
            value = label.get(key, [])
            if isinstance(value, str):
                value = [part.strip() for part in value.split(",") if part.strip()]
            out[key] = value if isinstance(value, list) else []
        elif key in STRING_DEFAULTS:
            out[key] = str(label.get(key) or STRING_DEFAULTS[key]).replace("\n", " ").strip()
        else:
            try:
                out[key] = int(label.get(key, 0))
            except (TypeError, ValueError):
                out[key] = 0
    if out["top_complaint_category"] not in COMPLAINT_CATEGORIES:
        out["top_complaint_category"] = "other" if out["complaint"] else "not_applicable"
    return out


def parse_json_label(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if repair_json:
        text = repair_json(text)
    return complete_label(json.loads(text))


def classify_with_llm(text: str, provider: ProviderConfig, max_retries: int = 2) -> dict[str, Any]:
    from openai import OpenAI

    api_key = provider.api_key or os.getenv(provider.api_key_env, "")
    if not api_key:
        raise RuntimeError(f"Missing API key. Set {provider.api_key_env} or enter a key in the app.")

    client = OpenAI(api_key=api_key, base_url=provider.base_url)
    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=provider.model,
                messages=[
                    {"role": "system", "content": _get_classification_prompt()},
                    {"role": "user", "content": f"Analyze the comment in this thread.\n\n{text}"},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
            return parse_json_label(response.choices[0].message.content or "{}")
        except Exception:
            if attempt >= max_retries:
                raise
            time.sleep(2**attempt)
    raise RuntimeError("Classification failed")


def apply_labels(
    df: pd.DataFrame,
    labels: list[dict[str, Any]],
    indices: list[int],
    *,
    mode: str,
) -> pd.DataFrame:
    out = df.copy()
    for idx, label in zip(indices, labels):
        completed = complete_label(label)
        for key, value in completed.items():
            if key == "multi_complaint_categories":
                value = ", ".join(value)
            out.at[idx, key] = value
        out.at[idx, "classifier_mode"] = mode
    return ensure_label_columns(out)


def classify_preview(df: pd.DataFrame, limit: int | None = None) -> pd.DataFrame:
    out = normalize_reddit_frame(df)
    candidates = out[~out["skip_classification"]].head(limit) if limit else out[~out["skip_classification"]]
    labels = [heuristic_label(row) for _, row in candidates.iterrows()]
    return apply_labels(out, labels, candidates.index.tolist(), mode="heuristic_preview")


def save_classified(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    if "created_at_norm" in out:
        out["created_at_norm"] = pd.to_datetime(out["created_at_norm"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
    # Write to a temp file then atomically replace so readers never see a partial CSV
    tmp = path.with_suffix(".tmp")
    out.to_csv(tmp, index=False)
    os.replace(tmp, path)
    return path


def load_classified(path: Path) -> pd.DataFrame:
    return ensure_label_columns(normalize_reddit_frame(_read_csv(path)))


def load_working_classified(path: Path) -> pd.DataFrame:
    """Load a prepared CSV without upgrading pending classifier modes to imported."""
    raw = _read_csv(path)
    original_modes = (
        raw["classifier_mode"].fillna("").astype(str).tolist()
        if "classifier_mode" in raw.columns
        else None
    )
    frame = ensure_label_columns(normalize_reddit_frame(raw))
    if original_modes is not None and len(original_modes) == len(frame):
        frame.loc[:, "classifier_mode"] = original_modes
    return frame


def analyzed_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = ensure_label_columns(df)
    # Exclude junk rows that were never eligible for classification.
    if "skip_classification" in out.columns:
        out = out[~out["skip_classification"].astype(bool)]
    # Keep only rows that actually went through a classifier.
    if "classifier_mode" in out.columns:
        out = out[out["classifier_mode"].fillna("").astype(str).str.len() > 0]
    # Exclude sentinel values written when classification was skipped or errored
    out = out[~out["sentiment"].isin(["skipped", "error", ""])].copy()
    out["created_at_norm"] = pd.to_datetime(out["created_at_norm"], errors="coerce")
    out["score_norm"] = pd.to_numeric(out["score_norm"], errors="coerce").fillna(0)
    return out


def filter_analyzed(df: pd.DataFrame, filters: dict[str, Any]) -> pd.DataFrame:
    out = analyzed_frame(df)
    for column, values in [
        ("sentiment", filters.get("sentiment")),
        ("vehicle_mentioned", filters.get("vehicle")),
        ("subreddit_norm", filters.get("subreddit")),
        ("issue_severity", filters.get("severity")),
        ("comment_type", filters.get("comment_type")),
        ("competitor_brand", filters.get("competitor")),
    ]:
        if values:
            out = out[out[column].isin(values)]
    if filters.get("date_range") and len(filters["date_range"]) == 2:
        start, end = filters["date_range"]
        if "created_at_norm" in out.columns and (start or end):
            # Compute dates once against the current (possibly already filtered) index
            # to avoid boolean-Series reindexing warnings from index mismatch.
            dates = pd.to_datetime(out["created_at_norm"], errors="coerce").dt.date
            if start and end:
                out = out[(dates >= start) & (dates <= end)]
            elif start:
                out = out[dates >= start]
            else:
                out = out[dates <= end]
    search = (filters.get("search") or "").strip().lower()
    if search:
        haystack = (out["title_norm"].fillna("") + " " + out["target_text"].fillna("") + " " + out["description"].fillna("")).str.lower()
        out = out[haystack.str.contains(re.escape(search), na=False)]
    min_score = filters.get("min_score")
    if min_score is not None:
        out = out[out["score_norm"] >= float(min_score)]
    return out


def percent(numerator: float, denominator: float) -> float:
    return round((numerator / denominator * 100), 1) if denominator else 0.0


def summary_metrics(df: pd.DataFrame) -> dict[str, Any]:
    all_rows = ensure_label_columns(df)
    analyzed = analyzed_frame(all_rows)
    total = len(all_rows)
    n = len(analyzed)
    complaints = int(analyzed["complaint"].sum()) if n else 0
    negative = int((analyzed["sentiment"] == "negative").sum()) if n else 0
    competitors = int(analyzed["competitor_mention"].sum()) if n else 0
    ev = int(analyzed["ev_topic"].sum()) if n else 0
    return {
        "total_rows": total,
        "analyzed_rows": n,
        "skipped_rows": int(all_rows["skip_classification"].sum()) if "skip_classification" in all_rows else 0,
        "complaints": complaints,
        "complaint_rate": percent(complaints, n),
        "negative_rate": percent(negative, n),
        "competitor_rate": percent(competitors, n),
        "ev_rate": percent(ev, n),
    }


def value_counts_df(df: pd.DataFrame, column: str, label: str = "value", limit: int | None = None) -> pd.DataFrame:
    if df.empty or column not in df:
        return pd.DataFrame(columns=[label, "count", "pct"])
    counts = df[column].replace("", pd.NA).dropna().value_counts()
    if limit:
        counts = counts.head(limit)
    total = counts.sum()
    return pd.DataFrame({label: counts.index, "count": counts.values, "pct": [percent(v, total) for v in counts.values]})


def flag_summary(df: pd.DataFrame) -> pd.DataFrame:
    analyzed = analyzed_frame(df)
    rows = []
    for flag in BINARY_FLAGS:
        count = int(analyzed[flag].sum()) if not analyzed.empty else 0
        rows.append({"flag": flag, "count": count, "pct_of_rows": percent(count, len(analyzed))})
    return pd.DataFrame(rows).sort_values("count", ascending=False)


def complaint_summary(df: pd.DataFrame) -> pd.DataFrame:
    analyzed = analyzed_frame(df)
    complaints = analyzed[analyzed["complaint"] == 1]
    return value_counts_df(complaints, "top_complaint_category", "theme", 12)


def all_complaint_mentions(df: pd.DataFrame) -> pd.DataFrame:
    analyzed = analyzed_frame(df)
    complaints = analyzed[analyzed["complaint"] == 1]
    counts: Counter[str] = Counter()
    for _, row in complaints.iterrows():
        top = _text(row.get("top_complaint_category"))
        if top and top != "not_applicable":
            counts[top] += 1
        secondary = _text(row.get("multi_complaint_categories"))
        for part in [p.strip() for p in secondary.split(",") if p.strip()]:
            if part != top:
                counts[part] += 1
    if not counts:
        return pd.DataFrame(columns=["theme", "count", "pct"])
    total = sum(counts.values())
    rows = [{"theme": k, "count": v, "pct": percent(v, total)} for k, v in counts.most_common(12)]
    return pd.DataFrame(rows)


def ev_comparison(df: pd.DataFrame) -> pd.DataFrame:
    analyzed = analyzed_frame(df)
    if analyzed.empty:
        return pd.DataFrame(columns=["powertrain", "comment_count", "complaint_rate_pct", "reliability_concern_pct", "software_tech_issue_pct", "competitor_mention_pct"])
    out = analyzed.assign(powertrain=np.where(analyzed["ev_topic"].astype(int) == 1, "EV topic", "Non-EV topic"))
    grouped = out.groupby("powertrain").agg(
        comment_count=("powertrain", "count"),
        complaint_rate_pct=("complaint", lambda x: round(x.mean() * 100, 1)),
        reliability_concern_pct=("reliability_concern", lambda x: round(x.mean() * 100, 1)),
        software_tech_issue_pct=("software_tech_issue", lambda x: round(x.mean() * 100, 1)),
        competitor_mention_pct=("competitor_mention", lambda x: round(x.mean() * 100, 1)),
    )
    return grouped.reset_index()


def vehicle_breakdown(df: pd.DataFrame, min_rows: int = 3) -> pd.DataFrame:
    analyzed = analyzed_frame(df)
    if analyzed.empty:
        return pd.DataFrame()
    grouped = analyzed.groupby("vehicle_mentioned").agg(
        comment_count=("vehicle_mentioned", "count"),
        complaint_rate_pct=("complaint", lambda x: round(x.mean() * 100, 1)),
        negative_sentiment_pct=("sentiment", lambda x: round((x == "negative").mean() * 100, 1)),
        competitor_mention_pct=("competitor_mention", lambda x: round(x.mean() * 100, 1)),
    )
    grouped = grouped[grouped["comment_count"] >= min_rows]
    return grouped.sort_values(["complaint_rate_pct", "comment_count"], ascending=[False, False]).reset_index()


def severity_by_model(df: pd.DataFrame, min_complaints: int = 2) -> pd.DataFrame:
    analyzed = analyzed_frame(df)
    complaints = analyzed[analyzed["complaint"] == 1]
    if complaints.empty:
        return pd.DataFrame()
    counts = complaints.groupby(["vehicle_mentioned", "issue_severity"]).size().reset_index(name="count")
    totals = counts.groupby("vehicle_mentioned")["count"].sum()
    keep = totals[totals >= min_complaints].index
    return counts[counts["vehicle_mentioned"].isin(keep)]


def priority_matrix(df: pd.DataFrame, min_complaints: int = 2) -> pd.DataFrame:
    analyzed = analyzed_frame(df)
    complaints = analyzed[analyzed["complaint"] == 1]
    if complaints.empty:
        return pd.DataFrame(columns=["theme", "volume", "pct_negative", "avg_score", "critical_count", "priority"])
    grouped = complaints.groupby("top_complaint_category").agg(
        volume=("top_complaint_category", "count"),
        pct_negative=("sentiment", lambda s: round((s == "negative").mean() * 100, 1)),
        avg_score=("score_norm", "mean"),
        critical_count=("issue_severity", lambda s: int((s == "critical").sum())),
    )
    grouped = grouped[grouped["volume"] >= min_complaints]
    if grouped.empty:
        grouped = complaints.groupby("top_complaint_category").agg(
            volume=("top_complaint_category", "count"),
            pct_negative=("sentiment", lambda s: round((s == "negative").mean() * 100, 1)),
            avg_score=("score_norm", "mean"),
            critical_count=("issue_severity", lambda s: int((s == "critical").sum())),
        )
    volume_median = grouped["volume"].median() if not grouped.empty else 0
    negative_median = grouped["pct_negative"].median() if not grouped.empty else 0

    def priority(row: pd.Series) -> str:
        if row["volume"] >= volume_median and row["pct_negative"] >= negative_median:
            return "Fix now"
        if row["volume"] < volume_median and row["pct_negative"] >= negative_median:
            return "Monitor"
        if row["volume"] >= volume_median:
            return "Watch"
        return "Low priority"

    grouped["priority"] = grouped.apply(priority, axis=1)
    return grouped.reset_index(names="theme").sort_values(["priority", "volume"], ascending=[True, False])


def cooccurrence(df: pd.DataFrame) -> pd.DataFrame:
    analyzed = analyzed_frame(df)
    if analyzed.empty:
        return pd.DataFrame()
    return analyzed[BINARY_FLAGS].astype(int).corr().round(2)


# ---------------------------------------------------------------------------
# Phase 1 notebook-parity aggregations
# ---------------------------------------------------------------------------

def engagement_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    """Distribution of engagement levels (low / medium / high) across analyzed rows."""
    analyzed = analyzed_frame(df)
    return value_counts_df(analyzed, "engagement_level", "level")


def comment_type_dist(df: pd.DataFrame) -> pd.DataFrame:
    """Distribution of comment types (complaint, question, praise, etc.)."""
    analyzed = analyzed_frame(df)
    return value_counts_df(analyzed, "comment_type", "comment_type")


def subreddit_breakdown(df: pd.DataFrame, min_rows: int = MIN_CELL) -> pd.DataFrame:
    """Per-subreddit complaint rate and negative sentiment.
    Subreddits with fewer than min_rows analyzed rows are excluded."""
    analyzed = analyzed_frame(df)
    if analyzed.empty or "subreddit_norm" not in analyzed.columns:
        return pd.DataFrame()
    grouped = analyzed.groupby("subreddit_norm").agg(
        comment_count=("subreddit_norm", "count"),
        complaint_rate_pct=("complaint", lambda x: round(x.mean() * 100, 1)),
        negative_pct=("sentiment", lambda x: round((x == "negative").mean() * 100, 1)),
    )
    return (
        grouped[grouped["comment_count"] >= min_rows]
        .sort_values("comment_count", ascending=False)
        .reset_index()
    )


def complaint_by_model_table(df: pd.DataFrame, min_cell: int = MIN_CELL) -> list[dict[str, Any]]:
    """Complaint category × vehicle pivot.
    Vehicles with fewer than min_cell total complaints are dropped.
    Individual cells below min_cell are zeroed so noisy rates don't mislead.
    Returns list of dicts keyed by vehicle + category columns (stacked-bar ready)."""
    analyzed = analyzed_frame(df)
    complaints = analyzed[analyzed["complaint"] == 1]
    if complaints.empty:
        return []
    pivot = (
        complaints
        .groupby(["vehicle_mentioned", "top_complaint_category"])
        .size()
        .unstack(fill_value=0)
    )
    row_totals = pivot.sum(axis=1)
    pivot = pivot[row_totals >= min_cell]
    if pivot.empty:
        return []
    # Suppress individual cells below min_cell.
    pivot = pivot.where(pivot >= min_cell, other=0)
    return (
        pivot
        .reset_index()
        .rename(columns={"vehicle_mentioned": "vehicle"})
        .to_dict(orient="records")
    )


def sentiment_by_model_table(df: pd.DataFrame, min_cell: int = MIN_CELL) -> list[dict[str, Any]]:
    """Sentiment × vehicle pivot for 100% stacked bar rendering.
    Vehicles with fewer than min_cell analyzed rows are excluded."""
    analyzed = analyzed_frame(df)
    if analyzed.empty:
        return []
    pivot = (
        analyzed
        .groupby(["vehicle_mentioned", "sentiment"])
        .size()
        .unstack(fill_value=0)
    )
    row_totals = pivot.sum(axis=1)
    pivot = pivot[row_totals >= min_cell]
    if pivot.empty:
        return []
    return (
        pivot
        .reset_index()
        .rename(columns={"vehicle_mentioned": "vehicle"})
        .to_dict(orient="records")
    )


def category_heatmap(df: pd.DataFrame, min_cell: int = MIN_CELL) -> dict[str, Any]:
    """Category-by-model heatmap matrix (lazy — served from /api/charts/detail).
    Rows = vehicles, columns = complaint categories.
    Cells below min_cell become None (null in JSON) so the renderer can grey them out."""
    analyzed = analyzed_frame(df)
    complaints = analyzed[analyzed["complaint"] == 1]
    if complaints.empty:
        return {"rows": [], "columns": [], "values": []}
    pivot = (
        complaints
        .groupby(["vehicle_mentioned", "top_complaint_category"])
        .size()
        .unstack(fill_value=0)
    )
    # Drop rows (vehicles) with too few total complaints.
    pivot = pivot[pivot.sum(axis=1) >= min_cell]
    # Drop columns (categories) with too few total complaints.
    col_totals = pivot.sum(axis=0)
    pivot = pivot[col_totals[col_totals >= min_cell].index]
    if pivot.empty:
        return {"rows": [], "columns": [], "values": []}
    masked = pivot.where(pivot >= min_cell, other=None).astype(object)
    return {
        "rows": pivot.index.tolist(),
        "columns": pivot.columns.tolist(),
        "values": masked.values.tolist(),
    }


def engagement_weighted_themes(df: pd.DataFrame, min_rows: int = 3) -> pd.DataFrame:
    """Complaint themes ranked by sum of Reddit scores (engagement-weighted volume).
    Themes with fewer than min_rows complaints are excluded."""
    analyzed = analyzed_frame(df)
    complaints = analyzed[analyzed["complaint"] == 1]
    if complaints.empty:
        return pd.DataFrame(columns=["theme", "weighted_score", "count"])
    grouped = complaints.groupby("top_complaint_category").agg(
        count=("top_complaint_category", "count"),
        weighted_score=("score_norm", lambda x: round(float(x.sum()), 1)),
    )
    return (
        grouped[grouped["count"] >= min_rows]
        .sort_values("weighted_score", ascending=False)
        .reset_index(names="theme")
    )


def competitor_breakdown_detail(df: pd.DataFrame) -> pd.DataFrame:
    """Per-competitor volume, complaint rate, and negative sentiment share."""
    analyzed = analyzed_frame(df)
    if analyzed.empty:
        return pd.DataFrame()
    mentioned = analyzed[analyzed["competitor_mention"] == 1]
    if mentioned.empty or "competitor_brand" not in mentioned.columns:
        return pd.DataFrame()
    grouped = mentioned.groupby("competitor_brand").agg(
        count=("competitor_brand", "count"),
        complaint_rate_pct=("complaint", lambda x: round(x.mean() * 100, 1)),
        negative_pct=("sentiment", lambda x: round((x == "negative").mean() * 100, 1)),
    )
    return grouped.sort_values("count", ascending=False).reset_index()


def evidence_table(df: pd.DataFrame, limit: int = 500) -> pd.DataFrame:
    analyzed = analyzed_frame(df)
    columns = [
        "created_at_norm",
        "subreddit_norm",
        "vehicle_mentioned",
        "sentiment",
        "complaint",
        "issue_severity",
        "top_complaint_category",
        "score_norm",
        "description",
        "title_norm",
        "target_text",  # raw post/comment body for inline reading
        "permalink_norm",
    ]
    existing = [col for col in columns if col in analyzed.columns]
    # Sort by classification confidence so highest-signal rows appear first.
    out = analyzed.loc[:, existing].sort_values("score_norm", ascending=False, na_position="last").head(limit)
    if "created_at_norm" in out:
        out["created_at_norm"] = pd.to_datetime(out["created_at_norm"], errors="coerce").dt.strftime("%Y-%m-%d")
    return out


def summary_payload(df: pd.DataFrame) -> dict[str, Any]:
    analyzed = analyzed_frame(df)
    return {
        "metrics": summary_metrics(df),
        "sentiment_distribution": value_counts_df(analyzed, "sentiment", "sentiment").to_dict(orient="records"),
        "binary_flag_summary": flag_summary(analyzed).to_dict(orient="records"),
        "top_complaint_categories": complaint_summary(analyzed).to_dict(orient="records"),
        "all_complaint_mentions": all_complaint_mentions(analyzed).to_dict(orient="records"),
        "ev_vs_non_ev": ev_comparison(analyzed).to_dict(orient="records"),
        "vehicles": vehicle_breakdown(analyzed, min_rows=2).to_dict(orient="records"),
        "priority_matrix": priority_matrix(analyzed, min_complaints=2).to_dict(orient="records"),
        "competitor_brands": value_counts_df(analyzed[analyzed["competitor_mention"] == 1], "competitor_brand", "brand").to_dict(orient="records"),
        "issue_severity": value_counts_df(analyzed[analyzed["complaint"] == 1], "issue_severity", "severity").to_dict(orient="records"),
    }


def generate_synthesis_with_llm(payload: dict[str, Any], provider: ProviderConfig) -> str:
    from openai import OpenAI

    api_key = provider.api_key or os.getenv(provider.api_key_env, "")
    if not api_key:
        raise RuntimeError(f"Missing API key. Set {provider.api_key_env} or enter a key in the app.")
    client = OpenAI(api_key=api_key, base_url=provider.base_url)
    response = client.chat.completions.create(
        model=provider.model,
        messages=[
            {"role": "system", "content": _get_synthesis_prompt()},
            {"role": "user", "content": json.dumps(payload, indent=2)},
        ],
        temperature=0.2,
    )
    return response.choices[0].message.content or ""


def fallback_synthesis(payload: dict[str, Any]) -> str:
    metrics = payload.get("metrics", {})
    top_themes = payload.get("top_complaint_categories", [])[:5]
    vehicles = payload.get("vehicles", [])[:5]
    priorities = payload.get("priority_matrix", [])[:5]
    competitors = payload.get("competitor_brands", [])[:5]

    lines = [
        "# GM Reddit Strategy Briefing",
        "",
        "## Executive Summary",
        f"- Analyzed {metrics.get('analyzed_rows', 0):,} Reddit evidence rows from {metrics.get('total_rows', 0):,} source rows.",
        f"- Complaint rate is {metrics.get('complaint_rate', 0)}%, negative sentiment rate is {metrics.get('negative_rate', 0)}%, and competitor mention rate is {metrics.get('competitor_rate', 0)}%.",
        f"- EV-related discussion accounts for {metrics.get('ev_rate', 0)}% of analyzed rows.",
        "",
        "## Top Complaint Themes",
    ]
    if top_themes:
        lines.append("| Theme | Volume | % of complaints | Strategic implication |")
        lines.append("|---|---:|---:|---|")
        for row in top_themes:
            theme = row.get("theme", "unknown")
            lines.append(f"| {theme.replace('_', ' ')} | {row.get('count', 0)} | {row.get('pct', 0)}% | Investigate root causes and ownership. |")
    else:
        lines.append("- No complaint themes were detected in the current filter.")

    lines += ["", "## Complaints by Model"]
    if vehicles:
        for row in vehicles:
            lines.append(f"- {row.get('vehicle_mentioned')} has {row.get('comment_count')} rows, {row.get('complaint_rate_pct')}% complaint rate, and {row.get('negative_sentiment_pct')}% negative sentiment.")
    else:
        lines.append("- No model had enough rows for a stable directional read.")

    lines += ["", "## EV vs. Non-EV Findings"]
    for row in payload.get("ev_vs_non_ev", []):
        lines.append(f"- {row.get('powertrain')}: {row.get('comment_count')} rows, {row.get('complaint_rate_pct')}% complaint rate, {row.get('software_tech_issue_pct')}% software/tech issue rate.")

    lines += ["", "## Prioritization - What to Fix First"]
    if priorities:
        for row in priorities:
            lines.append(f"- {row.get('priority')}: {row.get('theme', '').replace('_', ' ')} with {row.get('volume')} complaints and {row.get('pct_negative')}% negative sentiment.")
    else:
        lines.append("- No priority matrix available yet.")

    lines += ["", "## Competitor Signal"]
    if competitors:
        for row in competitors:
            lines.append(f"- {row.get('brand')} appears in {row.get('count')} competitor-mention rows.")
    else:
        lines.append("- Competitor mentions are not prominent in the current selection.")

    lines += [
        "",
        "## Recommended Actions",
        "- Assign the highest-volume negative themes to a clear owner across product, engineering, dealer network, or marketing.",
        "- Validate high-priority Reddit signals against warranty, service, survey, and call-center data before making investment decisions.",
        "- Re-run the collector on a fixed cadence and compare new-vs-historical deltas.",
        "",
        "## Final Summary - What This Means for GM",
        "Reddit is not a representative survey, but it is useful early-warning evidence. Treat high-volume, high-negativity themes as directional signals that deserve validation and owner assignment.",
        "",
        "## Open Questions / Next Steps",
        "- Which complaints align with warranty claims or service backlogs?",
        "- Which models need larger sample sizes before leadership decisions?",
        "- Are EV/software complaints tied to specific release windows or OTA events?",
    ]
    return "\n".join(lines)
