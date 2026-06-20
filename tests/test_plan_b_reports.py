from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


def _artifacts() -> tuple[dict, dict, dict]:
    labels = {
        "10": {
            "short_label": "Charging — résumé",
            "detailed_label": "Owners report intermittent charging failures.",
            "theme_type": "pain_point",
        },
        "2": {
            "short_label": "Dealer delays",
            "detailed_label": "Long waits for parts and appointments.",
            "theme_type": "service",
        },
    }
    examples = {
        "10": {
            "cluster_size": 7,
            "top_vehicles": ["Equinox EV"],
            "top_categories": ["charging_issue"],
            "sentiment_mix": {"negative": 6, "neutral": 1},
            "centroid_reps_text": "The charger stopped again.\nDealer could not reproduce it.",
        },
        "2": {"cluster_size": 4, "top_vehicles": ["Blazer EV"]},
    }
    signals = {
        "computed_at": 1_750_000_000,
        "has_timestamps": True,
        "data_span_days": 31,
        "signals": {
            "10": {
                "confidence_banner": "high",
                "confidence_note": "Enough dated records for a directional signal.",
                "velocity": {"direction": "rising", "data_note": "Recent activity increased."},
                "zscore": {"direction": "stable", "data_note": "Within the historical range."},
                "agreement": "mixed",
            },
            "2": {
                "confidence_banner": "low",
                "velocity": {"direction": "stable"},
            },
        },
    }
    return labels, examples, signals


def test_canonical_report_is_deterministic_and_carries_provenance() -> None:
    from src.trend_report import TrendReportModel

    labels, examples, signals = _artifacts()
    model = TrendReportModel.from_artifacts(
        labels,
        examples,
        signals,
        tag="gm",
        generation_id="generation-7",
        run_id="run-42",
    )

    assert [cluster.cluster_id for cluster in model.clusters] == ["10", "2"]
    assert model.tag == "gm"
    assert model.generation_id == "generation-7"
    assert model.run_id == "run-42"
    assert model.has_timestamps is True
    assert model.data_span_days == 31
    round_trip = model.to_artifacts()
    assert round_trip[0] == labels
    assert round_trip[1] == examples
    assert round_trip[2] == signals


def test_markdown_and_pdf_are_rendered_from_same_unicode_model(tmp_path: Path) -> None:
    from src.pdf_export import build_trend_report_pdf
    from src.trend_report import TrendReportModel, render_trend_markdown

    model = TrendReportModel.from_artifacts(*_artifacts(), tag="gm")
    markdown = render_trend_markdown(model)
    pdf_path = build_trend_report_pdf(model, tmp_path / "briefing.pdf")

    assert "Charging — résumé" in markdown
    assert "Generation: legacy" in markdown
    assert "31 days" in markdown
    assert pdf_path.read_bytes().startswith(b"%PDF")


def test_markdown_is_honest_when_timestamps_are_missing() -> None:
    from src.trend_report import TrendReportModel, render_trend_markdown

    labels, examples, _ = _artifacts()
    model = TrendReportModel.from_artifacts(
        labels,
        examples,
        {"has_timestamps": False, "data_span_days": 0, "signals": {}},
    )

    markdown = render_trend_markdown(model)
    assert "No usable timestamps" in markdown
    assert "Trend direction is unavailable" in markdown


def test_worker_keeps_markdown_when_pdf_rendering_fails(tmp_path: Path) -> None:
    from scripts.trend_briefing_job import run_trend_briefing_job
    from src.jobs import job_path, write_status

    runtime_root = tmp_path / "runtime"
    output_root = tmp_path / "staging"
    status_path = job_path(runtime_root, "gm", "report-1")
    write_status(status_path, {"kind": "trend_briefing", "state": "running", "tag": "gm"})
    trends = output_root / "gm" / "trends"
    trends.mkdir(parents=True)
    labels, examples, signals = _artifacts()
    for name, payload in (
        ("cluster_labels.json", labels),
        ("cluster_examples.json", examples),
        ("trend_signals.json", signals),
    ):
        (trends / name).write_text(json.dumps(payload), encoding="utf-8")

    with patch("src.pdf_export.build_trend_report_pdf", side_effect=RuntimeError("font exploded")):
        run_trend_briefing_job(
            "gm",
            "report-1",
            runtime_root,
            output_root=output_root,
            generation_id="generation-7",
            run_id="run-42",
        )

    markdown_path = output_root / "gm" / "downloads" / "gm_trend_briefing.md"
    assert "Charging — résumé" in markdown_path.read_text(encoding="utf-8")
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["state"] == "completed_with_warnings"
    assert status["artifact_paths"] == [str(markdown_path)]
    assert status["failed_artifacts"] == ["pdf"]
    assert status["generation_id"] == "generation-7"
    assert status["run_id"] == "run-42"


def test_warning_complete_report_is_terminal_for_jobs_and_coordinator(tmp_path: Path) -> None:
    from scripts.analyze_run import monitor_job
    from src.jobs import find_active_job, job_path, write_status

    status_path = job_path(tmp_path, "gm", "report-warning")
    write_status(status_path, {
        "job_id": "report-warning",
        "kind": "trend_briefing",
        "tag": "gm",
        "state": "completed_with_warnings",
        "warning": "PDF unavailable; Markdown retained.",
        "artifact_paths": [str(tmp_path / "gm.md")],
        "started_at": 1,
    })

    assert find_active_job(tmp_path, "gm", "trend_briefing") is None
    result = monitor_job(tmp_path, "gm", "report-warning", poll_interval=0.01)
    assert result.state == "completed_with_warnings"
    assert result.warning == "PDF unavailable; Markdown retained."


def test_downloads_resolve_from_current_generation(tmp_path: Path) -> None:
    import app as app_module

    original_runtime = app_module.RUNTIME
    app_module.RUNTIME = tmp_path
    generation = tmp_path / "gm" / "generations" / "generation-7"
    downloads = generation / "downloads"
    downloads.mkdir(parents=True)
    (downloads / "gm_trend_briefing.pdf").write_bytes(b"%PDF-1.4")
    (downloads / "gm_trend_briefing.md").write_text("# Report", encoding="utf-8")
    (tmp_path / "gm" / "current").symlink_to(generation, target_is_directory=True)
    try:
        client = TestClient(app_module.app)
        pdf = client.get("/api/download/trend-pdf", params={"tag": "gm"})
        markdown = client.get("/api/download/trend-md", params={"tag": "gm"})
    finally:
        app_module.RUNTIME = original_runtime

    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")
    assert markdown.status_code == 200
    assert markdown.text == "# Report"
    assert markdown.headers["content-type"].startswith("text/markdown")


def test_report_download_links_are_independent() -> None:
    trends_source = Path("web/js/views/trends.js").read_text(encoding="utf-8")
    dashboard_source = Path("web/js/views/dashboard.js").read_text(encoding="utf-8")

    for source in (trends_source, dashboard_source):
        assert "/api/download/trend-md" in source
        assert "/api/download/trend-pdf" in source
