"""Plan B B6: typed subreddit-list store, CRUD, snapshots, and collection UI."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import app as app_module
from src.subreddit_lists import ListConflictError, ListValidationError, SubredditListStore


client = TestClient(app_module.app, raise_server_exceptions=True)
ROOT = Path(__file__).parents[1]


@pytest.fixture
def store(tmp_path: Path) -> SubredditListStore:
    return SubredditListStore(tmp_path / "lists")


@pytest.fixture
def app_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    runtime = tmp_path / "runtime"
    list_root = tmp_path / "config" / "subreddit_lists"
    legacy = tmp_path / "legacy"
    (legacy / "config").mkdir(parents=True)
    (legacy / "config" / "gm_vehicle_subreddits.txt").write_text("Silverado\nGMC\n", encoding="utf-8")
    (legacy / "config" / "competitor_subreddits.txt").write_text("Ford\nToyota\n", encoding="utf-8")
    (legacy / "collect_incremental.py").write_text("# fixture\n", encoding="utf-8")
    monkeypatch.setattr(app_module, "RUNTIME", runtime)
    monkeypatch.setattr(app_module, "SUBREDDIT_LISTS_ROOT", list_root)
    monkeypatch.setattr(app_module, "LEGACY_ROOT", legacy)
    app_module.COLLECT_JOBS.clear()
    return runtime, list_root, legacy


def test_create_normalizes_comments_prefixes_and_duplicates(store: SubredditListStore) -> None:
    record = store.create(
        display_name="Launch list",
        list_type="custom",
        subreddits=["# note", "r/Silverado", "silverado", " GMC ", ""],
    )

    assert record["id"].startswith("list_")
    assert record["display_name"] == "Launch list"
    assert record["type"] == "custom"
    assert record["subreddits"] == ["Silverado", "GMC"]
    assert record["version"] == 1
    assert (store.root / f"{record['id']}.json").is_file()
    assert not list(store.root.glob("*.tmp"))


@pytest.mark.parametrize(
    ("display_name", "subreddits"),
    [("../escape", ["gm"]), ("Blank", []), ("Comments", ["# only", "  # still only"])],
)
def test_store_rejects_unsafe_or_empty_records(
    store: SubredditListStore, display_name: str, subreddits: list[str]
) -> None:
    with pytest.raises(ListValidationError):
        store.create(display_name=display_name, list_type="custom", subreddits=subreddits)


def test_duplicate_display_name_and_stale_update_are_conflicts(store: SubredditListStore) -> None:
    record = store.create("My list", "custom", ["silverado"])

    with pytest.raises(ListConflictError):
        store.create("my LIST", "custom", ["gmc"])
    with pytest.raises(ListConflictError) as exc:
        store.update(
            record["id"],
            version=0,
            display_name="Revised",
            list_type="custom",
            subreddits=["gmc"],
        )
    assert exc.value.current["version"] == 1


def test_update_increments_version_and_keeps_stable_id(store: SubredditListStore) -> None:
    record = store.create("My list", "custom", ["silverado"])

    updated = store.update(
        record["id"],
        version=1,
        display_name="My revised list",
        list_type="custom",
        subreddits=["gmc", "r/GMC", "buick"],
    )

    assert updated["id"] == record["id"]
    assert updated["version"] == 2
    assert updated["subreddits"] == ["gmc", "buick"]


def test_seed_imports_gm_and_competitor_legacy_files(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    (legacy / "config").mkdir(parents=True)
    (legacy / "config" / "gm_vehicle_subreddits.txt").write_text("Silverado\nGMC\n", encoding="utf-8")
    (legacy / "config" / "competitor_subreddits.txt").write_text("Ford\nToyota\n", encoding="utf-8")
    store = SubredditListStore(tmp_path / "lists", legacy_root=legacy)

    records = store.list_all()

    assert [(item["id"], item["type"]) for item in records] == [
        ("competitor-default", "competitor"),
        ("gm-default", "gm"),
    ]
    assert store.get("gm-default")["subreddits"] == ["Silverado", "GMC"]


def test_crud_endpoints_are_versioned_and_conflict_aware(app_roots: tuple[Path, Path, Path]) -> None:
    created = client.post(
        "/api/subreddit-lists",
        json={"display_name": "Launch", "type": "custom", "subreddits": ["silverado", "gmc"]},
    )
    assert created.status_code == 201
    record = created.json()

    assert client.get(f"/api/subreddit-lists/{record['id']}").json()["display_name"] == "Launch"
    assert any(item["id"] == record["id"] for item in client.get("/api/subreddit-lists").json()["items"])

    conflict = client.put(
        f"/api/subreddit-lists/{record['id']}",
        json={"version": 0, "display_name": "Changed", "type": "custom", "subreddits": ["buick"]},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["current"]["version"] == 1


def test_collection_uses_immutable_named_list_snapshot(app_roots: tuple[Path, Path, Path]) -> None:
    runtime, _, _ = app_roots
    record = client.post(
        "/api/subreddit-lists",
        json={"display_name": "Collection", "type": "custom", "subreddits": ["silverado", "gmc"]},
    ).json()
    process = MagicMock(pid=4321)
    process.poll.return_value = None

    with patch("app.subprocess.Popen", return_value=process) as popen:
        response = client.post(
            "/api/collect",
            json={
                "tag": "snapshot",
                "subreddit_list_id": record["id"],
                "listing_limit": 10,
                "comments_limit": 2,
            },
        )

    assert response.status_code == 200
    body = response.json()
    snapshot_root = runtime / "snapshot" / "collect" / body["job_id"] / "config"
    snapshot = json.loads((snapshot_root / "subreddit_list.json").read_text(encoding="utf-8"))
    assert snapshot["subreddits"] == ["silverado", "gmc"]
    command = popen.call_args.args[0]
    snapshot_file = snapshot_root / "subreddits.txt"
    assert command[command.index("--subreddits-file") + 1] == str(snapshot_file.resolve())

    client.put(
        f"/api/subreddit-lists/{record['id']}",
        json={"version": 1, "display_name": "Collection", "type": "custom", "subreddits": ["buick"]},
    )
    assert json.loads((snapshot_root / "subreddit_list.json").read_text(encoding="utf-8"))["subreddits"] == ["silverado", "gmc"]


def test_health_surfaces_configured_legacy_collector(app_roots: tuple[Path, Path, Path]) -> None:
    _, _, legacy = app_roots

    body = client.get("/api/health").json()

    assert body["legacy_collector_configured"] is True
    assert body["legacy_collector_found"] is True
    assert body["legacy_collector_root"] == str(legacy)


def test_collect_editor_uses_named_list_id_and_crud_controls() -> None:
    gathering = (ROOT / "web/js/views/gathering.js").read_text(encoding="utf-8")
    collect = (ROOT / "web/js/views/collect.js").read_text(encoding="utf-8")

    for value in ("subredditListSelect", "subredditListName", "subredditListType", "saveSubredditListBtn", "createSubredditListBtn"):
        assert value in gathering
    assert "subreddit_list_id" in collect
    assert "collectSource" not in gathering
    assert "customSubs" not in collect
