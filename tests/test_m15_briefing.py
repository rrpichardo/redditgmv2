import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from pypdf import PdfReader

import app
from scripts.briefing_job import run_briefing_job
from src.briefing import BriefingResult, generate_briefing, write_briefing
from src.gm_insights import ProviderConfig, normalize_reddit_frame, save_classified
from src.jobs import job_path, write_status
from src.pdf_export import build_synthesis_pdf_artifact


# Briefing is LLM-only — there is no deterministic fallback. Tests that need a report
# mock the LLM synthesis call so they run offline.
_FAKE_REPORT = "# GM Reddit Strategy Briefing\n\nMocked LLM synthesis output."


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


def test_generate_briefing_requires_llm() -> None:
    # No deterministic fallback: calling without an LLM provider must raise.
    with pytest.raises(RuntimeError, match="requires an LLM provider"):
        generate_briefing(_classified_frame(), use_llm=False)


def test_llm_failure_propagates() -> None:
    # Errors from the LLM call surface to the caller — they are not degraded to a fallback.
    with patch("src.briefing.generate_synthesis_with_llm", side_effect=RuntimeError("provider down")):
        with pytest.raises(RuntimeError, match="provider down"):
            generate_briefing(
                _classified_frame(),
                provider=_provider(),
                use_llm=True,
                fallback_on_error=True,  # no longer honored — error still propagates
            )


def test_generate_briefing_uses_llm_output() -> None:
    with patch("src.briefing.generate_synthesis_with_llm", return_value=_FAKE_REPORT):
        result = generate_briefing(_classified_frame(), provider=_provider(), use_llm=True)

    assert result.used_llm is True
    assert result.report == _FAKE_REPORT


def test_write_briefing_is_atomic(tmp_path: Path) -> None:
    output_path = tmp_path / "reports" / "brief.md"

    with patch("src.briefing.generate_synthesis_with_llm", return_value=_FAKE_REPORT):
        result = write_briefing(
            _classified_frame(), output_path, provider=_provider(), use_llm=True
        )

    assert output_path.read_text(encoding="utf-8") == result.report
    assert not output_path.with_suffix(".tmp").exists()


def test_synthesis_pdf_artifact_is_parseable_and_has_multiple_pages(tmp_path: Path) -> None:
    classified = tmp_path / "classified.csv"
    save_classified(_classified_frame(), classified)
    pdf = tmp_path / "reports" / "briefing.pdf"

    build_synthesis_pdf_artifact(
        classified,
        _FAKE_REPORT,
        pdf,
        tmp_path / "charts",
    )

    reader = PdfReader(str(pdf))
    assert len(reader.pages) >= 2


def test_briefing_worker_writes_status_to_runtime_and_artifact_to_output_root(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    output_root = tmp_path / "staging"
    classified_path = tmp_path / "classified.csv"
    save_classified(_classified_frame(), classified_path)
    status_path = job_path(runtime_root, "gm", "brief-1")
    write_status(status_path, {"state": "running", "kind": "briefing"})

    with patch("src.briefing.generate_synthesis_with_llm", return_value=_FAKE_REPORT):
        run_briefing_job(
            tag="gm",
            job_id="brief-1",
            runtime_root=runtime_root,
            classified_path=classified_path,
            provider=_provider(),
            output_root=output_root,
            use_llm=True,
        )

    status = json.loads(status_path.read_text(encoding="utf-8"))
    expected = output_root / "gm" / "reports" / "gm_reddit_synthesis_report.md"
    assert status["state"] == "completed"
    assert status["artifact_paths"] == [str(expected)]
    assert expected.exists()
    assert not (runtime_root / "gm" / "reports").exists()


def test_briefing_worker_fails_without_llm(tmp_path: Path) -> None:
    # use_llm=False now blocks at the writer; the worker records a failed status.
    runtime_root = tmp_path / "runtime"
    output_root = tmp_path / "staging"
    classified_path = tmp_path / "classified.csv"
    save_classified(_classified_frame(), classified_path)
    status_path = job_path(runtime_root, "gm", "brief-nokey")
    write_status(status_path, {"state": "running", "kind": "briefing"})

    with pytest.raises(SystemExit):
        run_briefing_job(
            tag="gm",
            job_id="brief-nokey",
            runtime_root=runtime_root,
            classified_path=classified_path,
            provider=_provider(),
            output_root=output_root,
            use_llm=False,
        )

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert "LLM provider" in status["error"]


def test_app_briefing_route_is_an_adapter_over_shared_writer(tmp_path: Path) -> None:
    expected_path = tmp_path / "brief.md"
    shared_result = BriefingResult(report=_FAKE_REPORT, used_llm=True)
    with (
        patch("app.load_frame", return_value=_classified_frame()),
        patch("app.report_path", return_value=expected_path),
        patch("app.write_briefing", return_value=shared_result) as shared,
    ):
        response = app.briefing(app.BriefingRequest(tag="gm", use_llm=True))

    shared.assert_called_once()
    body = json.loads(response.body)
    assert body["report"] == shared_result.report
    assert body["path"] == str(expected_path)
