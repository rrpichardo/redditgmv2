"""Plan B B8: generation-aware Q&A provenance, status, recovery, and polling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as app_module
from src.qa_retrieval import classified_fingerprint, qa_status_payload


client = TestClient(app_module.app, raise_server_exceptions=True)
ROOT = Path(__file__).parents[1]


def _published_generation(tmp_path: Path, tag: str = "qa") -> tuple[Path, Path]:
    runtime = tmp_path / "runtime"
    generation = runtime / tag / "generations" / "generation-1"
    classified = generation / "classified" / "classified_posts.csv"
    classified.parent.mkdir(parents=True)
    classified.write_text("source_id,target_text\na,Enough classified evidence text\n", encoding="utf-8")
    (classified.parent / ".run_id").write_text("run-1", encoding="utf-8")
    current = runtime / tag / "current"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(Path("generations") / generation.name)
    return runtime, generation


def _qa_artifacts(generation: Path, *, fingerprint: str, run_id: str = "run-1") -> None:
    qdir = generation / "qa"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / "docs.json").write_text("[]", encoding="utf-8")
    (qdir / "index.faiss").write_bytes(b"index")
    (qdir / "metadata.json").write_text(
        json.dumps(
            {
                "model": "embedding-test",
                "dim": 3,
                "doc_count": 1,
                "created_at": 1,
                "tag": "qa",
                "input_fingerprint": fingerprint,
                "generation_id": "generation-1",
                "run_id": run_id,
            }
        ),
        encoding="utf-8",
    )


def test_classified_fingerprint_is_stable_and_content_sensitive(tmp_path: Path) -> None:
    path = tmp_path / "classified.csv"
    path.write_bytes(b"a,b\n1,2\n")
    first = classified_fingerprint(path)
    second = classified_fingerprint(path)
    path.write_bytes(b"a,b\n1,3\n")

    assert first == second
    assert classified_fingerprint(path) != first


def test_matching_current_generation_is_ready(tmp_path: Path) -> None:
    runtime, generation = _published_generation(tmp_path)
    fingerprint = classified_fingerprint(generation / "classified" / "classified_posts.csv")
    _qa_artifacts(generation, fingerprint=fingerprint)

    result = qa_status_payload(runtime, "qa", api_key_available=True)

    assert result["state"] == "ready"
    assert result["generation_id"] == "generation-1"
    assert result["run_id"] == "run-1"
    assert result["input_fingerprint"] == fingerprint


@pytest.mark.parametrize(
    ("job_status", "analysis_running", "api_key", "expected"),
    [
        ({"state": "running", "job_id": "job-1"}, False, True, "building"),
        ({"state": "failed", "job_id": "job-1", "error": "boom"}, False, True, "failed"),
        (None, True, True, "building"),
        (None, False, False, "blocked_no_api_key"),
        (None, False, True, "missing"),
    ],
)
def test_non_ready_status_matrix(
    tmp_path: Path,
    job_status: dict | None,
    analysis_running: bool,
    api_key: bool,
    expected: str,
) -> None:
    runtime, _ = _published_generation(tmp_path)

    result = qa_status_payload(
        runtime,
        "qa",
        api_key_available=api_key,
        job_status=job_status,
        analysis_running=analysis_running,
    )

    assert result["state"] == expected


def test_older_input_fingerprint_is_stale(tmp_path: Path) -> None:
    runtime, generation = _published_generation(tmp_path)
    _qa_artifacts(generation, fingerprint="old-fingerprint", run_id="older-run")

    result = qa_status_payload(runtime, "qa", api_key_available=True)

    assert result["state"] == "stale"
    assert result["recovery_action"] == "rebuild"
    assert result["current_input_fingerprint"] != "old-fingerprint"


def test_imported_matching_artifacts_remain_usable(tmp_path: Path) -> None:
    runtime, generation = _published_generation(tmp_path)
    fingerprint = classified_fingerprint(generation / "classified" / "classified_posts.csv")
    _qa_artifacts(generation, fingerprint=fingerprint, run_id="")

    result = qa_status_payload(runtime, "qa", api_key_available=True)

    assert result["state"] == "ready"


def test_qa_status_endpoint_uses_generation_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, generation = _published_generation(tmp_path)
    fingerprint = classified_fingerprint(generation / "classified" / "classified_posts.csv")
    _qa_artifacts(generation, fingerprint=fingerprint)
    monkeypatch.setattr(app_module, "RUNTIME", runtime)
    monkeypatch.setattr(app_module, "load_api_key", lambda: "key")

    response = client.get("/api/qa/status?tag=qa")

    assert response.status_code == 200
    assert response.json()["state"] == "ready"


def test_stale_qa_search_is_rejected_with_recovery_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, generation = _published_generation(tmp_path)
    _qa_artifacts(generation, fingerprint="old")
    monkeypatch.setattr(app_module, "RUNTIME", runtime)
    monkeypatch.setattr(app_module, "load_api_key", lambda: "key")

    response = client.post("/api/qa/search", json={"tag": "qa", "query": "What changed?"})

    assert response.status_code == 409
    assert response.json()["detail"]["state"] == "stale"
    assert response.json()["detail"]["recovery_action"] == "rebuild"


def test_qa_ui_is_on_dashboard_and_polling_stops_on_navigation() -> None:
    dashboard = (ROOT / "web/js/views/dashboard.js").read_text(encoding="utf-8")
    qa = (ROOT / "web/js/views/qa.js").read_text(encoding="utf-8")
    nav = (ROOT / "web/js/nav.js").read_text(encoding="utf-8")

    assert "qaView" in dashboard
    assert dashboard.index("${signalsSection}") < dashboard.index("${qaView()}")
    for state in ("ready", "building", "missing", "failed", "stale", "blocked_no_api_key"):
        assert state in qa
    assert "stopQaPolling" in qa
    assert "stopQaPolling();" in nav
    assert "stopQaPolling();" in qa
