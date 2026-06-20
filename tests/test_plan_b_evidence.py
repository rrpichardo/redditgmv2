"""Plan B B1/B2: Reddit score semantics and paginated evidence."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app as app_module
from src.gm_insights import analyzed_frame, evidence_table, normalize_reddit_frame, save_classified


client = TestClient(app_module.app, raise_server_exceptions=True)


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "runtime"
    monkeypatch.setattr(app_module, "RUNTIME", root)
    return root


def _classified_rows() -> pd.DataFrame:
    rows = []
    values = [
        ("b", 100, "negative", "silverado", "gm", "major", "complaint", "ford", "2026-01-04", "Reply needle with enough detail"),
        ("a", 100, "negative", "silverado", "gm", "major", "complaint", "ford", "2026-01-03", "Reply needle with enough detail"),
        ("c", 7, "positive", "sierra", "trucks", "minor", "praise", "none", "2026-01-02", "Different positive evidence"),
        ("d", 0, "neutral", "unknown", "gm", "none", "question", "none", "2026-01-01", "Missing score question body"),
        ("e", -9, "negative", "silverado", "gm", "critical", "complaint", "ram", "2025-12-31", "Low score complaint body"),
    ]
    for source_id, score, sentiment, vehicle, subreddit, severity, comment_type, competitor, created, body in values:
        rows.append(
            {
                "source_id": source_id,
                "source_type": "comment",
                "post_id_norm": f"p-{source_id}",
                "subreddit_norm": subreddit,
                "title_norm": f"Title {source_id}",
                "post_body_norm": f"Parent {source_id}",
                "comment_body_norm": body,
                "target_text": body,
                "combined_text": body,
                "score_norm": score,
                "created_at_norm": created,
                "permalink_norm": f"/r/{subreddit}/{source_id}",
                "skip_classification": False,
                "classifier_mode": "imported",
                "sentiment": sentiment,
                "complaint": int(comment_type == "complaint"),
                "vehicle_mentioned": vehicle,
                "issue_severity": severity,
                "comment_type": comment_type,
                "competitor_brand": competitor,
                "description": f"Summary {body}",
                "top_complaint_category": "other" if comment_type == "complaint" else "not_applicable",
            }
        )
    return pd.DataFrame(rows)


def _seed(runtime: Path, tag: str = "evidence") -> Path:
    path = runtime / tag / "classified" / "classified_posts.csv"
    save_classified(_classified_rows(), path)
    return path


def test_normalization_preserves_parent_post_and_comment_fields() -> None:
    frame = normalize_reddit_frame(
        pd.DataFrame(
            [
                {
                    "post_id": "p1",
                    "comment_id": "c1",
                    "post_title": "Title",
                    "post_selftext": "Parent body",
                    "comment_body": "Reply body",
                    "comment_score": "-4",
                }
            ]
        )
    )
    row = frame.iloc[0]

    assert row["source_type"] == "comment"
    assert row["post_body_norm"] == "Parent body"
    assert row["comment_body_norm"] == "Reply body"
    assert row["target_text"] == "Reply body"
    assert row["score_norm"] == -4


def test_normalization_handles_post_only_and_imported_rows() -> None:
    post = normalize_reddit_frame(
        pd.DataFrame([{"id": "p1", "title": "Title", "subreddit": "gm", "selftext": "Post body"}])
    ).iloc[0]
    imported = normalize_reddit_frame(
        pd.DataFrame(
            [
                {
                    "source_id": "imported-1",
                    "source_type": "comment",
                    "target_text": "Imported reply",
                    "classifier_mode": "imported",
                    "sentiment": "neutral",
                    "complaint": 0,
                    "top_complaint_category": "not_applicable",
                    "description": "Imported",
                }
            ]
        )
    ).iloc[0]

    assert post["source_type"] == "post"
    assert post["post_body_norm"] == "Post body"
    assert post["comment_body_norm"] == ""
    assert imported["source_type"] == "comment"
    assert imported["post_body_norm"] == ""
    assert imported["comment_body_norm"] == "Imported reply"


@pytest.mark.parametrize(("raw_score", "expected"), [(-20, -20.0), (None, 0.0), (9_000_000, 9_000_000.0)])
def test_reddit_score_is_raw_with_missing_to_zero(raw_score: object, expected: float) -> None:
    frame = _classified_rows().iloc[[0]].copy()
    frame.loc[frame.index[0], "score_norm"] = raw_score

    assert analyzed_frame(frame).iloc[0]["score_norm"] == expected


def test_evidence_table_uses_stable_score_then_source_id_sort() -> None:
    rows = evidence_table(_classified_rows(), limit=None)

    assert rows["source_id"].tolist() == ["a", "b", "c", "d", "e"]
    assert {"source_type", "post_body_norm", "comment_body_norm"}.issubset(rows.columns)


def test_evidence_endpoint_paginates_server_side(runtime: Path) -> None:
    _seed(runtime)

    response = client.get("/api/evidence?tag=evidence&page=2&page_size=2")

    assert response.status_code == 200
    body = response.json()
    assert {key: body[key] for key in body if key != "items"} == {
        "page": 2,
        "page_size": 2,
        "total_items": 5,
        "total_pages": 3,
        "score_unit": "reddit_score",
    }
    assert [item["source_id"] for item in body["items"]] == ["c", "d"]


def test_evidence_endpoint_applies_all_explorer_filters(runtime: Path) -> None:
    _seed(runtime)
    query = (
        "/api/evidence?tag=evidence&sentiment=negative&vehicle=silverado&subreddit=gm"
        "&severity=major&comment_type=complaint&competitor=ford&search=needle"
        "&min_score=100&date_start=2026-01-03&date_end=2026-01-04&page=1&page_size=10"
    )

    response = client.get(query)

    assert response.status_code == 200
    body = response.json()
    assert [item["source_id"] for item in body["items"]] == ["a", "b"]
    assert body["total_items"] == 2
    assert body["total_pages"] == 1


def test_evidence_endpoint_searches_parent_post_body(runtime: Path) -> None:
    _seed(runtime)

    response = client.get("/api/evidence?tag=evidence&search=parent%20e")

    assert response.status_code == 200
    assert [item["source_id"] for item in response.json()["items"]] == ["e"]


def test_evidence_endpoint_returns_empty_out_of_range_page(runtime: Path) -> None:
    _seed(runtime)

    response = client.get("/api/evidence?tag=evidence&page=99&page_size=10")

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["page"] == 99
    assert response.json()["total_pages"] == 1


def test_evidence_endpoint_rejects_invalid_page_size(runtime: Path) -> None:
    _seed(runtime)

    assert client.get("/api/evidence?tag=evidence&page=0").status_code == 422
    assert client.get("/api/evidence?tag=evidence&page_size=101").status_code == 422
