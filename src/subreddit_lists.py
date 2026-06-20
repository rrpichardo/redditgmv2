"""Typed, atomic subreddit-list persistence and collection snapshots."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable


LIST_TYPES = frozenset({"gm", "competitor", "custom"})
_ID_PATTERN = re.compile(r"^(?:list_[0-9a-f]{32}|gm-default|competitor-default)$")
_SUBREDDIT_PATTERN = re.compile(r"^[A-Za-z0-9_]{2,64}$")
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class ListValidationError(ValueError):
    pass


class ListConflictError(RuntimeError):
    def __init__(self, message: str, *, current: dict[str, Any] | None = None):
        self.current = current or {}
        super().__init__(message)


class ListNotFoundError(KeyError):
    pass


def _root_lock(root: Path) -> threading.RLock:
    key = str(root.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_text(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def _validate_id(list_id: str) -> str:
    value = str(list_id).strip()
    if not _ID_PATTERN.fullmatch(value):
        raise ListValidationError("Invalid subreddit-list ID.")
    return value


def _validate_name(display_name: str) -> str:
    value = " ".join(str(display_name).split())
    if not value or len(value) > 80:
        raise ListValidationError("Display name must contain 1-80 characters.")
    if value in {".", ".."} or "/" in value or "\\" in value or any(ord(ch) < 32 for ch in value):
        raise ListValidationError("Display name cannot contain path separators or control characters.")
    return value


def normalize_subreddits(values: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw).strip()
        if not value or value.startswith("#"):
            continue
        if value.lower().startswith("r/"):
            value = value[2:].strip()
        value = value.strip("/")
        if not _SUBREDDIT_PATTERN.fullmatch(value):
            raise ListValidationError(f"Invalid subreddit name: {raw!r}.")
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            normalized.append(value)
    if not normalized:
        raise ListValidationError("At least one subreddit is required; comments and blank lines do not count.")
    return normalized


class SubredditListStore:
    def __init__(self, root: Path, *, legacy_root: Path | None = None):
        self.root = Path(root)
        self.legacy_root = Path(legacy_root) if legacy_root is not None else None
        self._lock = _root_lock(self.root)

    def _path(self, list_id: str) -> Path:
        return self.root / f"{_validate_id(list_id)}.json"

    def _read_records(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                _validate_id(value["id"])
            except (KeyError, ValueError, json.JSONDecodeError):
                continue
            records.append(value)
        return records

    def _assert_unique_name(self, display_name: str, *, exclude_id: str = "") -> None:
        key = display_name.casefold()
        for record in self._read_records():
            if record["id"] != exclude_id and str(record["display_name"]).casefold() == key:
                raise ListConflictError("A subreddit list with this display name already exists.", current=record)

    def _write_record(
        self,
        *,
        list_id: str,
        display_name: str,
        list_type: str,
        subreddits: Iterable[str],
        version: int,
        created_at: float,
    ) -> dict[str, Any]:
        if list_type not in LIST_TYPES:
            raise ListValidationError("List type must be gm, competitor, or custom.")
        now = time.time()
        record = {
            "id": _validate_id(list_id),
            "display_name": _validate_name(display_name),
            "type": list_type,
            "subreddits": normalize_subreddits(subreddits),
            "version": int(version),
            "created_at": float(created_at),
            "updated_at": now,
        }
        _atomic_json(self._path(list_id), record)
        return record

    def create(self, display_name: str, list_type: str, subreddits: list[str]) -> dict[str, Any]:
        with self._lock:
            name = _validate_name(display_name)
            self._assert_unique_name(name)
            now = time.time()
            return self._write_record(
                list_id=f"list_{uuid.uuid4().hex}",
                display_name=name,
                list_type=list_type,
                subreddits=subreddits,
                version=1,
                created_at=now,
            )

    def update(
        self,
        list_id: str,
        *,
        version: int,
        display_name: str,
        list_type: str,
        subreddits: list[str],
    ) -> dict[str, Any]:
        with self._lock:
            current = self.get(list_id)
            if int(version) != int(current["version"]):
                raise ListConflictError("The subreddit list changed after it was loaded.", current=current)
            name = _validate_name(display_name)
            self._assert_unique_name(name, exclude_id=current["id"])
            return self._write_record(
                list_id=current["id"],
                display_name=name,
                list_type=list_type,
                subreddits=subreddits,
                version=int(current["version"]) + 1,
                created_at=float(current["created_at"]),
            )

    def get(self, list_id: str) -> dict[str, Any]:
        path = self._path(list_id)
        if not path.exists():
            raise ListNotFoundError(list_id)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ListValidationError(f"Stored subreddit list {list_id!r} is invalid JSON.") from exc

    def _seed_legacy(self) -> None:
        if self.legacy_root is None:
            return
        seeds = (
            ("competitor-default", "Competitor vehicles", "competitor", "competitor_subreddits.txt"),
            ("gm-default", "GM vehicles", "gm", "gm_vehicle_subreddits.txt"),
        )
        for list_id, name, list_type, filename in seeds:
            destination = self._path(list_id)
            source = self.legacy_root / "config" / filename
            if destination.exists() or not source.exists():
                continue
            values = source.read_text(encoding="utf-8").splitlines()
            try:
                self._write_record(
                    list_id=list_id,
                    display_name=name,
                    list_type=list_type,
                    subreddits=values,
                    version=1,
                    created_at=time.time(),
                )
            except ListValidationError:
                continue

    def list_all(self) -> list[dict[str, Any]]:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            self._seed_legacy()
            return sorted(self._read_records(), key=lambda record: record["id"])


def write_collection_snapshot(record: dict[str, Any], config_root: Path) -> tuple[Path, Path]:
    """Persist immutable JSON and collector-text copies of one selected list."""
    config_root = Path(config_root)
    json_path = config_root / "subreddit_list.json"
    text_path = config_root / "subreddits.txt"
    _atomic_json(json_path, record)
    _atomic_text(text_path, "\n".join(record["subreddits"]) + "\n")
    return json_path, text_path
