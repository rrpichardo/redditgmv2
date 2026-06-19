import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import app
from scripts.classify_job import run_classify_job
from scripts.faiss_qa_job import run_faiss_qa_job
from scripts.trend_briefing_job import run_trend_briefing_job
from scripts.trend_job import run_trend_job
from src.gm_insights import ProviderConfig, normalize_reddit_frame, save_classified
from src.jobs import job_path, start_job, write_status


def _provider() -> ProviderConfig:
    return ProviderConfig(
        provider="openai",
        model="gpt-test",
        base_url="https://example.test/v1",
        api_key_env="OPENAI_API_KEY",
        api_key="",
    )


def _status(runtime_root: Path, tag: str, job_id: str, kind: str) -> Path:
    path = job_path(runtime_root, tag, job_id)
    write_status(path, {"state": "running", "kind": kind, "pid": os.getpid()})
    return path


def test_start_job_captures_stdout_and_stderr_in_real_log(tmp_path: Path) -> None:
    script = tmp_path / "talker.py"
    script.write_text(
        "import sys\nprint('worker stdout')\nprint('worker stderr', file=sys.stderr)\n",
        encoding="utf-8",
    )
    log_path = tmp_path / "attempt" / "step.log"

    status = start_job(
        runtime_root=tmp_path / "runtime",
        tag="gm",
        kind="classify",
        script=script,
        extra_args=[],
        cwd=tmp_path,
        log_path=log_path,
    )
    os.waitpid(status["pid"], 0)

    text = log_path.read_text(encoding="utf-8")
    assert "worker stdout" in text
    assert "worker stderr" in text
    assert status["log_path"] == str(log_path)


def test_classify_writes_redacted_row_errors_to_staging(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    output_root = tmp_path / "staging"
    frame = normalize_reddit_frame(
        pd.DataFrame(
            [
                {
                    "id": "bad-row",
                    "title": "A sufficiently long bad classification row",
                    "selftext": "This comment contains private customer evidence.",
                    "subreddit": "gm",
                },
                {
                    "id": "good-row",
                    "title": "A sufficiently long good classification row",
                    "selftext": "This second comment can be classified normally.",
                    "subreddit": "gm",
                },
            ]
        )
    )
    classified = output_root / "gm" / "classified" / "classified_posts.csv"
    save_classified(frame, classified)
    status_path = _status(runtime_root, "gm", "classify-1", "classify")

    with patch(
        "scripts.classify_job.classify_with_llm",
        side_effect=[RuntimeError("api_key=secret-token provider timeout"), {}],
    ):
        run_classify_job(
            tag="gm",
            job_id="classify-1",
            runtime_root=runtime_root,
            classified_path=classified,
            provider=_provider(),
            output_root=output_root,
        )

    errors_path = output_root / "gm" / "classified" / "classification_errors.jsonl"
    error = json.loads(errors_path.read_text(encoding="utf-8").strip())
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert set(error) == {"row_id", "error"}
    assert error["row_id"] == "bad-row"
    assert "secret-token" not in error["error"]
    assert "private customer evidence" not in errors_path.read_text(encoding="utf-8")
    assert status["errors"] == 1
    assert str(errors_path) in status["artifact_paths"]


def test_trend_worker_uses_output_root_but_keeps_status_in_runtime(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    output_root = tmp_path / "staging"
    status_path = _status(runtime_root, "gm", "trend-1", "trend")
    artifact = output_root / "gm" / "trends" / "cluster_labels.json"

    with (
        patch("scripts.trend_job.load_classified", return_value=pd.DataFrame([{"x": 1}, {"x": 2}])),
        patch(
            "scripts.trend_job.run_clustering",
            return_value={"n_clusters": 2, "n_docs": 2, "artifact_paths": [str(artifact)]},
        ) as clustering,
        patch("scripts.trend_job.run_trend_analysis", return_value={"n_clusters": 2}) as trends,
    ):
        run_trend_job(
            "gm",
            "trend-1",
            runtime_root,
            tmp_path / "classified.csv",
            _provider(),
            output_root=output_root,
            n_clusters=2,
        )

    assert clustering.call_args.kwargs["runtime_root"] == output_root
    assert trends.call_args.kwargs["runtime_root"] == output_root
    assert json.loads(status_path.read_text(encoding="utf-8"))["state"] == "completed"


def test_trend_pdf_worker_reads_and_writes_output_root(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    output_root = tmp_path / "staging"
    status_path = _status(runtime_root, "gm", "pdf-1", "trend_briefing")
    trends_dir = output_root / "gm" / "trends"
    trends_dir.mkdir(parents=True)
    (trends_dir / "cluster_labels.json").write_text('{"0": "Issue"}', encoding="utf-8")

    def _write_pdf(**kwargs) -> None:
        kwargs["pdf_path"].write_bytes(b"pdf")

    with patch("src.pdf_export.build_trend_briefing_pdf", side_effect=_write_pdf):
        run_trend_briefing_job(
            "gm", "pdf-1", runtime_root, output_root=output_root
        )

    expected = output_root / "gm" / "downloads" / "gm_trend_briefing.pdf"
    assert expected.read_bytes() == b"pdf"
    assert json.loads(status_path.read_text(encoding="utf-8"))["artifact_paths"] == [str(expected)]


def test_qa_worker_uses_output_root_but_keeps_status_in_runtime(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    output_root = tmp_path / "staging"
    status_path = _status(runtime_root, "gm", "qa-1", "faiss_qa")
    artifact = output_root / "gm" / "qa" / "index.faiss"

    with (
        patch("scripts.faiss_qa_job.load_classified", return_value=pd.DataFrame([{"x": 1}])),
        patch(
            "scripts.faiss_qa_job.build_qa_index",
            return_value={"doc_count": 1, "artifact_paths": [str(artifact)]},
        ) as build,
    ):
        run_faiss_qa_job(
            "gm",
            "qa-1",
            runtime_root,
            tmp_path / "classified.csv",
            _provider(),
            output_root=output_root,
        )

    assert build.call_args.kwargs["runtime_root"] == output_root
    assert json.loads(status_path.read_text(encoding="utf-8"))["state"] == "completed"


def test_load_frame_prefers_newer_raw_upload_over_stale_classified(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(app, "RUNTIME", tmp_path / "runtime")
    classified = normalize_reddit_frame(
        pd.DataFrame(
            [
                {
                    "id": "old",
                    "title": "Old classified title with sufficient length",
                    "selftext": "Old classified body with sufficient evidence.",
                    "subreddit": "gm",
                }
            ]
        )
    )
    classified.loc[:, "classifier_mode"] = "llm"
    old_path = app.classified_path("gm")
    save_classified(classified, old_path)
    old_time = time.time() - 20
    os.utime(old_path, (old_time, old_time))

    raw_path = app.data_dir("gm") / "gm_posts.csv"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "id": "new",
                "title": "New upload title with sufficient length",
                "selftext": "New upload body with sufficient evidence.",
                "subreddit": "gm",
            }
        ]
    ).to_csv(raw_path, index=False)

    frame = app.load_frame("gm")

    assert frame.iloc[0]["source_id"] == "new"
    assert frame.iloc[0]["classifier_mode"] == ""
