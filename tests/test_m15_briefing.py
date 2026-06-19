import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import app
from scripts.briefing_job import run_briefing_job
from src.briefing import generate_briefing, write_briefing
from src.gm_insights import ProviderConfig, normalize_reddit_frame, save_classified
from src.jobs import job_path, write_status


def _classified_frame() -> pd.DataFrame:
    frame = normalize_reddit_frame(
        pd.DataFrame(
            [
                {
                    "id": "p1",
                    "title": "Transmission issue needs service support",
                    "selftext": "My truck transmission failed during a road trip.",
                    "subreddit": "gm",
                    "created_at": "2026-05-01T00:00:00Z",
                    "score": "4",
                }
            ]
        )
    )
    frame.loc[:, "classifier_mode"] = "llm"
    frame.loc[:, "sentiment"] = "negative"
    frame.loc[:, "complaint"] = 1
    frame.loc[:, "top_complaint_category"] = "transmission"
    return frame


def _provider() -> ProviderConfig:
    return ProviderConfig(
        provider="openai",
        model="gpt-test",
        base_url="https://example.test/v1",
        api_key_env="OPENAI_API_KEY",
        api_key="test-key",
    )


def test_generate_briefing_uses_deterministic_fallback_without_llm() -> None:
    result = generate_briefing(_classified_frame(), use_llm=False)

    assert result.used_llm is False
    assert result.warning is None
    assert result.report.startswith("# GM Reddit Strategy Briefing")
    assert "Analyzed 1 Reddit evidence rows" in result.report


def test_llm_failure_can_degrade_to_fallback_with_warning() -> None:
    with patch("src.briefing.generate_synthesis_with_llm", side_effect=RuntimeError("provider down")):
        result = generate_briefing(
            _classified_frame(),
            provider=_provider(),
            use_llm=True,
            fallback_on_error=True,
        )

    assert result.used_llm is False
    assert result.warning == "LLM synthesis failed; deterministic fallback used: provider down"
    assert result.report.startswith("# GM Reddit Strategy Briefing")


def test_write_briefing_is_atomic(tmp_path: Path) -> None:
    output_path = tmp_path / "reports" / "brief.md"

    result = write_briefing(_classified_frame(), output_path, use_llm=False)

    assert output_path.read_text(encoding="utf-8") == result.report
    assert not output_path.with_suffix(".tmp").exists()


def test_briefing_worker_writes_status_to_runtime_and_artifact_to_output_root(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    output_root = tmp_path / "staging"
    classified_path = tmp_path / "classified.csv"
    save_classified(_classified_frame(), classified_path)
    status_path = job_path(runtime_root, "gm", "brief-1")
    write_status(status_path, {"state": "running", "kind": "briefing"})

    run_briefing_job(
        tag="gm",
        job_id="brief-1",
        runtime_root=runtime_root,
        classified_path=classified_path,
        provider=_provider(),
        output_root=output_root,
        use_llm=False,
    )

    status = json.loads(status_path.read_text(encoding="utf-8"))
    expected = output_root / "gm" / "reports" / "gm_reddit_synthesis_report.md"
    assert status["state"] == "completed"
    assert status["artifact_paths"] == [str(expected)]
    assert expected.exists()
    assert not (runtime_root / "gm" / "reports").exists()


def test_app_briefing_route_is_an_adapter_over_shared_writer(tmp_path: Path) -> None:
    expected_path = tmp_path / "brief.md"
    shared_result = generate_briefing(_classified_frame(), use_llm=False)
    with (
        patch("app.load_frame", return_value=_classified_frame()),
        patch("app.report_path", return_value=expected_path),
        patch("app.write_briefing", return_value=shared_result) as shared,
    ):
        response = app.briefing(app.BriefingRequest(tag="gm", use_llm=False))

    shared.assert_called_once()
    body = json.loads(response.body)
    assert body["report"] == shared_result.report
    assert body["path"] == str(expected_path)
