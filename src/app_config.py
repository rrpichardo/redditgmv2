"""User-editable application config.

Non-secret settings live in config.json at the project root (version-controlled).
The API key lives in runtime/secrets.json (gitignored — never committed).
"""
from __future__ import annotations

import json
import hashlib
import os
import string
import uuid
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = _ROOT / "config.json"
SECRETS_PATH = _ROOT / "runtime" / "secrets.json"

CLUSTER_PROMPT_PLACEHOLDERS = frozenset({
    "cluster_size",
    "sentiment_mix",
    "severity_mix",
    "top_vehicles",
    "top_categories",
    "centroid_reps",
    "engagement_reps",
    "recent_reps",
})
CLUSTER_PROMPT_OUTPUT_KEYS = frozenset({
    "short_label",
    "detailed_label",
    "theme_type",
    "confidence",
    "confidence_score",
    "rationale",
    "label_risk",
})
_OUTPUT_MARKER = "Respond with a JSON object:"

DEFAULT_CLUSTER_LABEL_PROMPT = """\
Analyze this cluster of {cluster_size} Reddit comments about General Motors vehicles.

Cluster metadata:
- Sentiment mix: {sentiment_mix}
- Severity mix: {severity_mix}
- Top vehicles mentioned: {top_vehicles}
- Top complaint categories: {top_categories}

Most characteristic comments (centroid-nearest — these define the cluster):
{centroid_reps}

Highest-engagement comments:
{engagement_reps}

Most recent comments:
{recent_reps}

Respond with a JSON object:
{{
  "short_label": "3-6 word theme",
  "detailed_label": "1-2 sentence description of this cluster",
  "theme_type": "complaint",
  "confidence": "medium",
  "confidence_score": 0.0,
  "rationale": "Why this label fits these examples",
  "label_risk": "low"
}}"""

DEFAULTS: dict[str, Any] = {
    "provider": {
        "name": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "gpt-oss-120b",
        "embedding_model": "text-embedding-3-small",
    },
    "collection": {
        "default_tag": "gm_vehicle_on_demand",
        "legacy_root": "",
        "listing_limit": 100,
        "comments_limit": 5,
    },
    "analysis": {
        "n_clusters": 10,
        "classify_limit": 0,
        "qa_k": 8,
    },
    "prompts": {
        "classification": "",
        "synthesis": "",
        "cluster_label": DEFAULT_CLUSTER_LABEL_PROMPT,
    },
}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def validate_cluster_prompt(prompt: str) -> dict[str, Any]:
    errors: list[str] = []
    fields: set[str] = set()
    try:
        for _, field_name, _, _ in string.Formatter().parse(str(prompt)):
            if field_name:
                fields.add(field_name)
    except ValueError as exc:
        errors.append(f"Prompt braces are invalid: {exc}")

    missing_placeholders = sorted(CLUSTER_PROMPT_PLACEHOLDERS - fields)
    unknown_placeholders = sorted(fields - CLUSTER_PROMPT_PLACEHOLDERS)
    missing_output_keys: list[str] = []
    if _OUTPUT_MARKER not in str(prompt):
        errors.append(f"Prompt must include the marker {_OUTPUT_MARKER!r}.")
        missing_output_keys = sorted(CLUSTER_PROMPT_OUTPUT_KEYS)
    else:
        contract = str(prompt).split(_OUTPUT_MARKER, 1)[1].strip()
        contract = contract.replace("{{", "{").replace("}}", "}")
        start, end = contract.find("{"), contract.rfind("}")
        try:
            parsed = json.loads(contract[start:end + 1])
            if not isinstance(parsed, dict):
                raise ValueError("output contract must be a JSON object")
            missing_output_keys = sorted(CLUSTER_PROMPT_OUTPUT_KEYS - set(parsed))
        except (json.JSONDecodeError, ValueError) as exc:
            errors.append(f"Output contract must be valid JSON: {exc}")
            missing_output_keys = sorted(CLUSTER_PROMPT_OUTPUT_KEYS)

    if missing_placeholders:
        errors.append("Missing required placeholders: " + ", ".join(missing_placeholders))
    if unknown_placeholders:
        errors.append("Unknown placeholders: " + ", ".join(unknown_placeholders))
    if missing_output_keys and not any("valid JSON" in error for error in errors):
        errors.append("Missing output keys: " + ", ".join(missing_output_keys))
    return {
        "valid": not errors,
        "missing_placeholders": missing_placeholders,
        "unknown_placeholders": unknown_placeholders,
        "missing_output_keys": missing_output_keys,
        "errors": errors,
    }


def get_cluster_label_prompt(config: dict[str, Any] | None = None) -> str:
    resolved = load_config() if config is None else config
    candidate = str(resolved.get("prompts", {}).get("cluster_label", "")).strip()
    return candidate if candidate and validate_cluster_prompt(candidate)["valid"] else DEFAULT_CLUSTER_LABEL_PROMPT


def cluster_prompt_provenance(config: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved = load_config() if config is None else config
    candidate = str(resolved.get("prompts", {}).get("cluster_label", "")).strip()
    validation = validate_cluster_prompt(candidate) if candidate else validate_cluster_prompt(DEFAULT_CLUSTER_LABEL_PROMPT)
    prompt = candidate if candidate and validation["valid"] else DEFAULT_CLUSTER_LABEL_PROMPT
    source = "configured" if prompt == candidate and candidate else ("fallback_default" if candidate else "default")
    return {
        "prompt": prompt,
        "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "source": source,
        "validation": validate_cluster_prompt(prompt),
    }


def reset_cluster_label_prompt() -> str:
    save_config({"prompts": {"cluster_label": DEFAULT_CLUSTER_LABEL_PROMPT}})
    return DEFAULT_CLUSTER_LABEL_PROMPT


def write_cluster_prompt_snapshot(path: Path, provenance: dict[str, Any]) -> Path:
    _atomic_write(path, json.dumps(provenance, indent=2, ensure_ascii=False) + "\n")
    return path


def load_config() -> dict[str, Any]:
    """Return merged config: file values on top of defaults."""
    if not CONFIG_PATH.exists():
        return {k: dict(v) for k, v in DEFAULTS.items()}
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    merged: dict[str, Any] = {}
    for section, defaults in DEFAULTS.items():
        merged[section] = {**defaults, **raw.get(section, {})}
    return merged


def save_config(data: dict[str, Any]) -> None:
    """Write non-secret config to config.json, merging with existing."""
    current = load_config()
    for section in DEFAULTS:
        if section in data and isinstance(data[section], dict):
            current[section].update(data[section])
    _atomic_write(CONFIG_PATH, json.dumps(current, indent=2, ensure_ascii=False) + "\n")


def load_api_key() -> str:
    """Return API key: runtime/secrets.json → OPENROUTER_API_KEY env var → empty."""
    if SECRETS_PATH.exists():
        try:
            s = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
            key = s.get("api_key", "").strip()
            if key:
                return key
        except Exception:
            pass
    return os.environ.get("OPENROUTER_API_KEY", "")


def save_api_key(key: str) -> None:
    """Persist API key to runtime/secrets.json (gitignored)."""
    existing: dict[str, Any] = {}
    if SECRETS_PATH.exists():
        try:
            existing = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing["api_key"] = key.strip()
    _atomic_write(SECRETS_PATH, json.dumps(existing, indent=2) + "\n")
