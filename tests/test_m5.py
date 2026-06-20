"""M5 polish and contract-boundary regression tests."""

from __future__ import annotations

import os
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app as app_module
from scripts.analyze_run import STEP_ORDER
from src.run_store import RunStore, ensure_attempt_layout


client = TestClient(app_module.app, raise_server_exceptions=True)


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "runtime"
    monkeypatch.setattr(app_module, "RUNTIME", root)
    return root


def _store(runtime: Path) -> RunStore:
    return RunStore(runtime / "runs.db")


def _create_run(runtime: Path, tag: str, run_id: str = "run-1", state: str = "completed") -> str:
    with _store(runtime) as store:
        store.create_run(
            run_id,
            tag,
            state=state,
            config={"provider": "openrouter", "model": "gpt-test"},
        )
    return run_id


def _finish_attempt_with_files(
    runtime: Path,
    tag: str,
    run_id: str,
    *,
    log_path: Path | None = None,
) -> tuple[Path, Path]:
    paths = ensure_attempt_layout(runtime, tag, run_id, "prepare", 1)
    actual_log = log_path or paths.log_path
    actual_log.parent.mkdir(parents=True, exist_ok=True)
    actual_log.write_text("prepare completed\n", encoding="utf-8")
    artifact = paths.artifacts_dir / "prepared.csv"
    artifact.write_text("id,title\n1,example\n", encoding="utf-8")
    with _store(runtime) as store:
        attempt = store.start_attempt(run_id, "prepare", log_path=str(actual_log))
        store.finish_attempt(
            run_id,
            "prepare",
            attempt["attempt_no"],
            state="completed",
            processed=1,
            total=1,
            artifacts=[
                {
                    "id": "prepared.csv",
                    "name": artifact.name,
                    "bytes": artifact.stat().st_size,
                    "path": str(artifact),
                }
            ],
        )
    return actual_log, artifact


def test_run_active_response_matches_frozen_top_level_contract(runtime: Path) -> None:
    tag = "locked"
    _create_run(runtime, tag, state="running")
    with _store(runtime) as store:
        store.acquire_tag_lock(
            tag,
            owner_id="owner-1",
            run_id="run-1",
            pid=os.getpid(),
        )

    response = client.post("/api/analyze", json={"tag": tag})

    assert response.status_code == 409
    assert response.json() == {
        "error": "run_active",
        "message": "Tag 'locked' has an active run: run-1",
        "active_run_id": "run-1",
    }


def test_pipeline_status_rejects_run_from_another_tag(runtime: Path) -> None:
    _create_run(runtime, "alpha")

    response = client.get("/api/pipeline/status?tag=beta&run_id=run-1")

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("path", "method", "payload"),
    [
        ("/api/pipeline/log?tag=beta&run_id=run-1&step=prepare", "get", None),
        ("/api/pipeline/artifact?tag=beta&run_id=run-1&step=prepare&id=0", "get", None),
        (
            "/api/pipeline/retry",
            "post",
            {"tag": "beta", "run_id": "run-1", "step": "prepare"},
        ),
    ],
)
def test_pipeline_resources_reject_run_from_another_tag(
    runtime: Path,
    path: str,
    method: str,
    payload: dict[str, str] | None,
) -> None:
    _create_run(runtime, "alpha")
    _finish_attempt_with_files(runtime, "alpha", "run-1")

    with patch("app.start_job", return_value={"job_id": "should-not-start"}) as start_job:
        response = getattr(client, method)(path, json=payload) if payload else getattr(client, method)(path)

    assert response.status_code == 404
    start_job.assert_not_called()


def test_pipeline_log_rejects_path_outside_run_snapshot(runtime: Path, tmp_path: Path) -> None:
    _create_run(runtime, "alpha")
    outside_log = tmp_path / "outside-secret.log"
    _finish_attempt_with_files(runtime, "alpha", "run-1", log_path=outside_log)

    response = client.get("/api/pipeline/log?tag=alpha&run_id=run-1&step=prepare")

    assert response.status_code == 404
    assert "prepare completed" not in response.text


def test_pipeline_artifact_rejects_negative_index(runtime: Path) -> None:
    _create_run(runtime, "alpha")
    _finish_attempt_with_files(runtime, "alpha", "run-1")

    response = client.get("/api/pipeline/artifact?tag=alpha&run_id=run-1&step=prepare&id=-1")

    assert response.status_code == 404


def test_pipeline_artifact_serves_valid_immutable_snapshot(runtime: Path) -> None:
    _create_run(runtime, "alpha")
    _, artifact = _finish_attempt_with_files(runtime, "alpha", "run-1")

    response = client.get("/api/pipeline/artifact?tag=alpha&run_id=run-1&step=prepare&id=0")

    assert response.status_code == 200
    assert response.content == artifact.read_bytes()
    assert str(artifact) not in response.headers.get("content-disposition", "")


def test_chart_detail_sanitizes_non_finite_golden_fixture_values(runtime: Path) -> None:
    source = Path(__file__).parent / "fixtures" / "golden_classified.csv"
    destination = runtime / "golden" / "classified" / "classified_posts.csv"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())

    response = client.get("/api/charts/detail?tag=golden")

    assert response.status_code == 200
    assert "NaN" not in response.text
    assert "Infinity" not in response.text


def test_pipeline_manifest_matches_canonical_order_and_public_fields(runtime: Path) -> None:
    tag = "alpha"
    with _store(runtime) as store:
        store.create_run(
            "run-1",
            tag,
            state="completed_with_warnings",
            config={
                "provider": "openrouter",
                "model": "gpt-test",
                "api_key": "must-not-leak",
            },
        )
        for index, step in enumerate(STEP_ORDER):
            store.upsert_step(
                "run-1",
                step,
                state="completed_with_warnings" if step == "classify" else "completed",
                processed=index,
                total=6,
                errors=1 if step == "classify" else 0,
                error_rate=1 / 6 if step == "classify" else 0,
                warning="1 row failed" if step == "classify" else None,
            )
    _finish_attempt_with_files(runtime, tag, "run-1")

    response = client.get("/api/pipeline/status?tag=alpha&run_id=run-1")

    assert response.status_code == 200
    body = response.json()
    assert [step["name"] for step in body["steps"]] == STEP_ORDER
    required = {
        "name",
        "state",
        "started_at",
        "ended_at",
        "processed",
        "total",
        "errors",
        "error_rate",
        "warning",
        "artifacts",
        "log_available",
    }
    assert all(set(step) == required for step in body["steps"])
    serialized = response.text
    assert "must-not-leak" not in serialized
    assert str(runtime) not in serialized
    assert body["config"] == {"model": "gpt-test", "provider": "openrouter"}


def test_pipeline_manifest_and_download_use_latest_immutable_attempt(runtime: Path) -> None:
    _create_run(runtime, "alpha")
    _finish_attempt_with_files(runtime, "alpha", "run-1")
    paths = ensure_attempt_layout(runtime, "alpha", "run-1", "prepare", 2)
    paths.log_path.write_text("second attempt\n", encoding="utf-8")
    latest_artifact = paths.artifacts_dir / "prepared.csv"
    latest_artifact.write_text("id,title\n2,newest\n", encoding="utf-8")
    with _store(runtime) as store:
        attempt = store.start_attempt("run-1", "prepare", log_path=str(paths.log_path))
        store.finish_attempt(
            "run-1",
            "prepare",
            attempt["attempt_no"],
            state="completed",
            processed=1,
            total=1,
            artifacts=[
                {
                    "id": "prepared.csv",
                    "name": latest_artifact.name,
                    "bytes": latest_artifact.stat().st_size,
                    "path": str(latest_artifact),
                }
            ],
        )

    manifest = client.get("/api/pipeline/status?tag=alpha&run_id=run-1").json()
    prepare = next(step for step in manifest["steps"] if step["name"] == "prepare")
    download = client.get(prepare["artifacts"][0]["url"])

    assert len(prepare["artifacts"]) == 1
    assert download.status_code == 200
    assert download.content == latest_artifact.read_bytes()


def test_retry_preserves_immutable_attempt_history(runtime: Path) -> None:
    _create_run(runtime, "alpha", state="failed")
    _finish_attempt_with_files(runtime, "alpha", "run-1")
    with _store(runtime) as store:
        before = store.get_attempts("run-1", "prepare")

    with patch("app.start_job", return_value={"job_id": "retry-job"}):
        response = client.post(
            "/api/pipeline/retry",
            json={"tag": "alpha", "run_id": "run-1", "step": "prepare"},
        )

    assert response.status_code == 200
    with _store(runtime) as store:
        after = store.get_attempts("run-1", "prepare")
        assert after == before
        assert store.get_step("run-1", "prepare")["state"] == "pending"


def test_cancel_projects_running_step_and_attempt_to_cancelled(runtime: Path) -> None:
    _create_run(runtime, "alpha", state="running")
    _finish_attempt_with_files(runtime, "alpha", "run-1")
    paths = ensure_attempt_layout(runtime, "alpha", "run-1", "classify", 1)
    paths.log_path.write_text("classify running\n", encoding="utf-8")
    with _store(runtime) as store:
        store.start_attempt("run-1", "classify", log_path=str(paths.log_path))
        store.acquire_tag_lock(
            "alpha",
            owner_id="owner-1",
            run_id="run-1",
            pid=os.getpid(),
        )

    with patch("app.find_active_job", return_value=None):
        response = client.post(
            "/api/pipeline/cancel",
            json={"tag": "alpha", "run_id": "run-1"},
        )

    assert response.status_code == 200
    with _store(runtime) as store:
        assert store.get_run("run-1")["state"] == "cancelled"
        assert store.get_step("run-1", "classify")["state"] == "cancelled"
        assert store.get_attempts("run-1", "classify")[-1]["state"] == "cancelled"


def test_cancel_terminates_coordinator_and_active_child(runtime: Path) -> None:
    _create_run(runtime, "alpha", state="running")
    with _store(runtime) as store:
        store.acquire_tag_lock(
            "alpha",
            owner_id="owner-1",
            run_id="run-1",
            pid=os.getpid(),
        )
    jobs = {
        "analyze": {"pid": 111, "job_id": "coordinator"},
        "classify": {"pid": 222, "job_id": "child"},
    }

    with (
        patch("app.find_active_job", side_effect=lambda _root, _tag, kind: jobs.get(kind)),
        patch("app.os.kill") as kill,
    ):
        response = client.post(
            "/api/pipeline/cancel",
            json={"tag": "alpha", "run_id": "run-1"},
        )

    assert response.status_code == 200
    assert {call.args for call in kill.call_args_list} == {
        (111, app_module.signal.SIGTERM),
        (222, app_module.signal.SIGTERM),
    }


def _load_browser_with_data(page, live_server) -> None:
    page.goto(live_server.url)
    page.wait_for_load_state("networkidle", timeout=10000)
    page.evaluate(
        """async (tag) => {
            const { state } = await import('/static/js/state.js');
            state.tag = tag;
            const { loadRun } = await import('/static/js/app.js');
            await loadRun();
        }""",
        live_server.tag,
    )


def test_tabs_follow_aria_keyboard_interaction_pattern(live_server, browser_page) -> None:
    page = browser_page
    _load_browser_with_data(page, live_server)

    assert page.get_attribute(".tabs", "role") == "tablist"
    assert page.get_attribute('.tab[data-view="dashboard"]', "role") == "tab"
    assert page.get_attribute('.tab[data-view="dashboard"]', "aria-selected") == "true"
    assert page.get_attribute('.tab[data-view="dashboard"]', "tabindex") == "0"
    assert page.get_attribute("#viewRoot", "role") == "tabpanel"
    assert page.get_attribute("#viewRoot", "aria-labelledby") == "tab-dashboard"

    page.focus('.tab[data-view="dashboard"]')
    page.keyboard.press("ArrowRight")
    assert page.evaluate("() => document.activeElement?.dataset?.view") == "explorer"
    assert page.get_attribute('.tab[data-view="explorer"]', "aria-selected") == "true"
    assert page.get_attribute('.tab[data-view="dashboard"]', "tabindex") == "-1"

    page.keyboard.press("End")
    assert page.evaluate("() => document.activeElement?.dataset?.view") == "settings"
    assert page.get_attribute("#viewRoot", "aria-labelledby") == "tab-settings"


def test_active_view_controls_have_names_and_associated_labels(live_server, browser_page) -> None:
    page = browser_page
    _load_browser_with_data(page, live_server)

    issues: list[dict[str, str]] = []
    for view in ["explorer", "gathering", "pipeline", "settings"]:
        page.click(f'.tab[data-view="{view}"]')
        issues.extend(
            page.eval_on_selector_all(
                "#viewRoot input, #viewRoot select, #viewRoot textarea",
                """(controls) => controls
                    .filter((control) => !control.name || (!control.labels?.length && !control.getAttribute('aria-label')))
                    .map((control) => ({ view: document.querySelector('.tab[aria-selected="true"]')?.dataset.view,
                                        id: control.id, name: control.name }))""",
            )
        )

    assert issues == []


def test_filter_focus_is_visibly_indicated(live_server, browser_page) -> None:
    page = browser_page
    _load_browser_with_data(page, live_server)
    page.click('.tab[data-view="explorer"]')
    page.focus("#filter-search")

    focus_style = page.eval_on_selector(
        "#filter-search",
        "el => { const s = getComputedStyle(el); return {style:s.outlineStyle, width:s.outlineWidth}; }",
    )

    assert focus_style["style"] != "none"
    assert focus_style["width"] != "0px"


def test_settings_save_announces_confirmation(live_server, browser_page) -> None:
    page = browser_page
    _load_browser_with_data(page, live_server)
    page.click('.tab[data-view="settings"]')

    page.click("#saveConfigBtn")

    # New settings flow confirms inline next to the save button (not the global status bar).
    page.wait_for_selector("#configSaveStatus")
    assert "Saved" in page.inner_text("#configSaveStatus")


def test_active_loading_copy_uses_typographic_ellipsis() -> None:
    root = Path(__file__).resolve().parents[1]
    components = (root / "web/js/components.js").read_text(encoding="utf-8")
    app_js = (root / "web/js/app.js").read_text(encoding="utf-8")
    assert '"Working..."' not in components
    assert '"Loading run..."' not in app_js


def test_chart_catalog_has_aria_and_data_table_equivalents(live_server, browser_page) -> None:
    page = browser_page
    _load_browser_with_data(page, live_server)
    page.click('.tab[data-view="explorer"]')
    page.wait_for_selector('[data-chart="sentiment"] canvas', timeout=30000)
    page.evaluate(
        """async () => {
            const { loadDetailCharts } = await import('/static/js/app.js');
            await loadDetailCharts();
        }"""
    )

    result = page.evaluate(
        """() => Array.from(document.querySelectorAll('.echart[data-chart]')).map((chart) => {
            const id = chart.dataset.chart;
            const instance = window.echarts.getInstanceByDom(chart);
                return {
                    id,
                    role: chart.getAttribute('role'),
                    label: chart.getAttribute('aria-label'),
                    rendered: Boolean(instance),
                    ariaEnabled: instance?.getOption()?.aria?.enabled === true,
                    hasTable: Boolean(document.querySelector(`[data-chart-table="${id}"] table`)),
                    hasAlternative: Boolean(document.querySelector(`[data-chart-table="${id}"]`)?.textContent?.trim()),
                };
        })"""
    )

    assert result
    assert all(item["role"] == "img" and item["label"] for item in result)
    assert all(item["ariaEnabled"] for item in result if item["rendered"]), result
    assert [item["id"] for item in result if not item["hasAlternative"]] == []
    assert sum(item["hasTable"] for item in result) >= 5


def test_custom_dashboard_charts_have_names_tables_and_decorative_sparklines_hidden(
    live_server, browser_page
) -> None:
    page = browser_page
    _load_browser_with_data(page, live_server)
    page.evaluate(
        """async () => {
            const { state } = await import('/static/js/state.js');
            const cluster = {
                cluster_id: 7,
                cluster_size: 12,
                label: { short_label: 'Brake vibration' },
                trend_signal: {
                    confidence_banner: 'high',
                    velocity: { valid: true, velocity: 0.42 },
                    zscore: { valid: true, zscore: 2.4 },
                },
            };
            state.trendsData = { ok: true, clusters: [cluster] };
            state.timeseriesData = {
                ok: true,
                buckets: ['2026-06-01', '2026-06-08'],
                negative: [2, 4], neutral: [3, 2], positive: [1, 2],
                by_cluster: { '7': [1, 3] },
            };
            const { setView } = await import('/static/js/nav.js');
            setView('dashboard');
        }"""
    )
    page.wait_for_selector("#quadrantChart canvas", timeout=10000)

    for chart_id in ["quadrantChart", "leaderboardChart", "negTimeChart", "sentTimeChart"]:
        assert page.get_attribute(f"#{chart_id}", "role") == "img"
        assert page.get_attribute(f"#{chart_id}", "aria-label")
        assert page.locator(f'[data-chart-table="{chart_id}"] table').count() == 1
    assert page.locator('[id^="sparkline-"]:not([aria-hidden="true"])').count() == 0


def test_pipeline_status_badges_use_icon_and_text_without_injected_palette(
    live_server, browser_page
) -> None:
    page = browser_page
    _load_browser_with_data(page, live_server)
    page.route(
        "**/api/pipeline/runs*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                [{"run_id": "run-1", "state": "failed", "started_at": 1, "ended_at": 2}]
            ),
        ),
    )
    page.route(
        "**/api/pipeline/status*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "run_id": "run-1",
                    "tag": "fixture",
                    "state": "failed",
                    "steps": [
                        {
                            "name": "classify",
                            "state": "failed",
                            "started_at": 1,
                            "ended_at": 2,
                            "processed": 1,
                            "total": 2,
                            "errors": 1,
                            "error_rate": 0.5,
                            "warning": "Worker failed.",
                            "artifacts": [],
                            "log_available": False,
                        }
                    ],
                }
            ),
        ),
    )

    page.click('.tab[data-view="pipeline"]')
    page.wait_for_selector('.step-card[data-step-card="classify"]')
    badge = page.locator('.step-card[data-step-card="classify"] .step-state-badge')

    assert "Failed" in badge.inner_text()
    assert badge.locator('[aria-hidden="true"]').count() == 1
    assert page.locator("#pipeline-styles").count() == 0


def test_pipeline_status_recovers_and_clears_stale_notice(
    live_server, browser_page
) -> None:
    page = browser_page
    _load_browser_with_data(page, live_server)
    page.route(
        "**/api/pipeline/runs*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                [{"run_id": "run-1", "state": "running", "started_at": 1}]
            ),
        ),
    )
    calls = {"status": 0}

    def status_route(route) -> None:
        calls["status"] += 1
        if calls["status"] == 1:
            route.fulfill(
                status=404,
                content_type="application/json",
                body=json.dumps({"detail": "Run 'run-1' not found."}),
            )
            return
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "run_id": "run-1",
                    "tag": "fixture",
                    "state": "running",
                    "started_at": 1,
                    "steps": [],
                }
            ),
        )

    page.route("**/api/pipeline/status*", status_route)
    page.click('.tab[data-view="pipeline"]')
    page.wait_for_selector("#statusBar .notice.error")
    assert "Run 'run-1' not found" in page.inner_text("#statusBar")

    page.click("#pipelineRefreshBtn")
    page.wait_for_function("() => document.querySelector('#statusBar')?.textContent === ''")
    assert calls["status"] >= 2


def test_pipeline_retry_conflict_selects_active_run(live_server, browser_page) -> None:
    page = browser_page
    _load_browser_with_data(page, live_server)
    page.route(
        "**/api/pipeline/runs*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                [
                    {"run_id": "run-1", "state": "failed", "started_at": 1},
                    {"run_id": "run-2", "state": "running", "started_at": 2},
                ]
            ),
        ),
    )

    def status_route(route) -> None:
        run_id = "run-2" if "run_id=run-2" in route.request.url else "run-1"
        state = "running" if run_id == "run-2" else "failed"
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "run_id": run_id,
                    "tag": "fixture",
                    "state": state,
                    "started_at": 2 if run_id == "run-2" else 1,
                    "steps": [
                        {
                            "name": "trend_pdf",
                            "state": state,
                            "processed": 0,
                            "total": 0,
                            "errors": 0,
                            "error_rate": 0,
                            "warning": "PDF failed." if state == "failed" else None,
                            "artifacts": [],
                            "log_available": False,
                        }
                    ],
                }
            ),
        )

    page.route("**/api/pipeline/status*", status_route)
    page.route(
        "**/api/pipeline/retry",
        lambda route: route.fulfill(
            status=409,
            content_type="application/json",
            body=json.dumps(
                {
                    "error": "run_active",
                    "message": "Tag 'fixture' has an active run: run-2",
                    "active_run_id": "run-2",
                }
            ),
        ),
    )

    page.click('.tab[data-view="pipeline"]')
    page.wait_for_selector('.retry-btn[data-step="trend_pdf"]')
    page.click('.retry-btn[data-step="trend_pdf"]')
    page.wait_for_function(
        "() => document.querySelector('#pipelineRunSelect')?.value === 'run-2'"
    )

    assert "run run-2" in page.inner_text("#statusBar")
    assert "Request failed with 409" not in page.inner_text("#statusBar")


def test_view_change_uses_short_transform_opacity_animation(live_server, browser_page) -> None:
    page = browser_page
    _load_browser_with_data(page, live_server)

    page.click('.tab[data-view="gathering"]')
    animations = page.evaluate(
        """() => document.getElementById('viewRoot').getAnimations().map((animation) => ({
            duration: Number(animation.effect.getTiming().duration),
            playState: animation.playState,
        }))"""
    )

    assert any(150 <= item["duration"] <= 300 for item in animations), animations


def test_reduced_motion_disables_view_chart_and_hover_motion(live_server, browser_page) -> None:
    page = browser_page
    page.emulate_media(reduced_motion="reduce")
    _load_browser_with_data(page, live_server)

    # The topband refresh button was removed; the primary tab is a stable hover target.
    page.hover('.tab[data-view="dashboard"]')
    assert page.eval_on_selector('.tab[data-view="dashboard"]', "el => getComputedStyle(el).transform") == "none"

    page.click('.tab[data-view="explorer"]')
    page.wait_for_selector('[data-chart="sentiment"] canvas', timeout=30000)
    state = page.evaluate(
        """() => {
            const root = document.getElementById('viewRoot');
            const chart = document.querySelector('[data-chart="sentiment"]');
            return {
                viewAnimations: root.getAnimations().length,
                chartAnimation: window.echarts.getInstanceByDom(chart).getOption().animation,
            };
        }"""
    )

    assert state["viewAnimations"] == 0
    assert state["chartAnimation"] is False


def test_explorer_chart_tables_do_not_expand_mobile_document(live_server, browser_page) -> None:
    page = browser_page
    page.set_viewport_size({"width": 360, "height": 812})
    _load_browser_with_data(page, live_server)
    page.click('.tab[data-view="explorer"]')
    page.evaluate(
        """async () => {
            const { loadDetailCharts } = await import('/static/js/app.js');
            await loadDetailCharts();
        }"""
    )

    overflow = page.evaluate(
        """() => ({
            html: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
            body: document.body.scrollWidth > document.body.clientWidth + 1,
        })"""
    )

    assert overflow == {"html": False, "body": False}
