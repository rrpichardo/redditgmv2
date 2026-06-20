"""User-editable application config.

Non-secret settings live in config.json at the project root (version-controlled).
The API key lives in runtime/secrets.json (gitignored — never committed).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = _ROOT / "config.json"
SECRETS_PATH = _ROOT / "runtime" / "secrets.json"

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
    },
}


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
    CONFIG_PATH.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")


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
    SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if SECRETS_PATH.exists():
        try:
            existing = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing["api_key"] = key.strip()
    SECRETS_PATH.write_text(json.dumps(existing, indent=2), encoding="utf-8")
