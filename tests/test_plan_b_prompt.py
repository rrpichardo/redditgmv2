"""Plan B B7: cluster prompt validation, atomic config, reset, and run provenance."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app as app_module
import src.app_config as app_config
from src.app_config import (
    DEFAULT_CLUSTER_LABEL_PROMPT,
    cluster_prompt_provenance,
    get_cluster_label_prompt,
    load_config,
    reset_cluster_label_prompt,
    save_api_key,
    save_config,
    validate_cluster_prompt,
)
from src.run_store import RunStore


client = TestClient(app_module.app, raise_server_exceptions=True)


@pytest.fixture
def config_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    config_path = tmp_path / "config.json"
    secrets_path = tmp_path / "runtime" / "secrets.json"
    monkeypatch.setattr(app_config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(app_config, "SECRETS_PATH", secrets_path)
    return config_path, secrets_path


def test_default_cluster_prompt_is_valid_and_declares_output_contract() -> None:
    result = validate_cluster_prompt(DEFAULT_CLUSTER_LABEL_PROMPT)

    assert result == {
        "valid": True,
        "missing_placeholders": [],
        "unknown_placeholders": [],
        "missing_output_keys": [],
        "errors": [],
    }


def test_validation_requires_every_context_placeholder() -> None:
    prompt = DEFAULT_CLUSTER_LABEL_PROMPT.replace("{centroid_reps}", "no examples")

    result = validate_cluster_prompt(prompt)

    assert result["valid"] is False
    assert result["missing_placeholders"] == ["centroid_reps"]


def test_validation_rejects_malformed_json_contract() -> None:
    prompt = DEFAULT_CLUSTER_LABEL_PROMPT.replace('"confidence_score": 0.0', '"confidence_score": nope')

    result = validate_cluster_prompt(prompt)

    assert result["valid"] is False
    assert any("valid JSON" in error for error in result["errors"])


def test_validation_rejects_missing_output_key() -> None:
    prompt = DEFAULT_CLUSTER_LABEL_PROMPT.replace(',\n  "label_risk": "low"', "")

    result = validate_cluster_prompt(prompt)

    assert result["valid"] is False
    assert result["missing_output_keys"] == ["label_risk"]


def test_effective_prompt_falls_back_when_saved_value_is_invalid() -> None:
    assert get_cluster_label_prompt({"prompts": {"cluster_label": "broken"}}) == DEFAULT_CLUSTER_LABEL_PROMPT


def test_config_and_secret_writes_are_atomic(config_paths: tuple[Path, Path]) -> None:
    config_path, secrets_path = config_paths

    save_config({"analysis": {"n_clusters": 7}})
    save_api_key("secret-value")

    assert json.loads(config_path.read_text(encoding="utf-8"))["analysis"]["n_clusters"] == 7
    assert json.loads(secrets_path.read_text(encoding="utf-8"))["api_key"] == "secret-value"
    assert not list(config_path.parent.glob("*.tmp"))
    assert not list(secrets_path.parent.glob("*.tmp"))


def test_reset_saves_and_returns_default(config_paths: tuple[Path, Path]) -> None:
    custom = DEFAULT_CLUSTER_LABEL_PROMPT.replace("Analyze this cluster", "Review this cluster")
    save_config({"prompts": {"cluster_label": custom}})

    reset = reset_cluster_label_prompt()

    assert reset == DEFAULT_CLUSTER_LABEL_PROMPT
    assert load_config()["prompts"]["cluster_label"] == DEFAULT_CLUSTER_LABEL_PROMPT


def test_prompt_validation_preview_and_invalid_save(config_paths: tuple[Path, Path]) -> None:
    preview = client.post("/api/config/cluster-prompt/validate", json={"prompt": "broken"})
    invalid_save = client.patch("/api/config", json={"prompts": {"cluster_label": "broken"}})

    assert preview.status_code == 200
    assert preview.json()["valid"] is False
    assert invalid_save.status_code == 422
    assert not config_paths[0].exists()


def test_prompt_reset_endpoint(config_paths: tuple[Path, Path]) -> None:
    response = client.post("/api/config/cluster-prompt/reset")

    assert response.status_code == 200
    assert response.json()["prompt"] == DEFAULT_CLUSTER_LABEL_PROMPT
    assert response.json()["validation"]["valid"] is True


def test_analyze_snapshots_effective_prompt_and_hash(
    tmp_path: Path,
    config_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "analysis-runtime"
    monkeypatch.setattr(app_module, "RUNTIME", runtime)
    custom = DEFAULT_CLUSTER_LABEL_PROMPT.replace("Analyze this cluster", "Review this cluster")
    save_config({"prompts": {"cluster_label": custom}})

    with patch("app.start_job", return_value={"pid": 4321, "job_id": "owner"}) as start_job:
        response = client.post("/api/analyze", json={"tag": "prompt-snapshot", "n_clusters": 3})

    assert response.status_code == 200
    run_id = response.json()["run_id"]
    snapshot_path = runtime / "prompt-snapshot" / "runs" / run_id / "config" / "cluster_prompt.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    expected = cluster_prompt_provenance({"prompts": {"cluster_label": custom}})
    assert snapshot == expected
    with RunStore(runtime / "runs.db") as store:
        assert store.get_run(run_id)["config"]["cluster_prompt_sha256"] == expected["sha256"]
    command_args = start_job.call_args.args[4]
    assert command_args[command_args.index("--cluster_prompt_path") + 1] == str(snapshot_path)

    save_config({"prompts": {"cluster_label": DEFAULT_CLUSTER_LABEL_PROMPT}})
    assert json.loads(snapshot_path.read_text(encoding="utf-8"))["prompt"] == custom


def test_trend_worker_and_coordinator_receive_prompt_snapshot() -> None:
    trend_job = (Path(__file__).parents[1] / "scripts" / "trend_job.py").read_text(encoding="utf-8")
    coordinator = (Path(__file__).parents[1] / "scripts" / "analyze_run.py").read_text(encoding="utf-8")
    trend_module = (Path(__file__).parents[1] / "src" / "trend_insights.py").read_text(encoding="utf-8")

    assert "--cluster_prompt_path" in trend_job
    assert '"cluster_prompt_sha256"' in coordinator
    assert '"--cluster_prompt_path"' in coordinator
    assert "prompt_template or get_cluster_label_prompt()" in trend_module


def test_settings_exposes_cluster_prompt_preview_and_reset() -> None:
    source = (Path(__file__).parents[1] / "web" / "js" / "views" / "settings.js").read_text(encoding="utf-8")

    assert "cfg-prompt-cluster" in source
    assert "validateClusterPrompt" in source
    assert "resetClusterPrompt" in source
    assert "/api/config/cluster-prompt/validate" in source
    assert "/api/config/cluster-prompt/reset" in source
