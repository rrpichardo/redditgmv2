"""Plan B browser contracts, with source checks that do not require a live socket."""

import json
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_explorer_uses_truthful_score_and_search_labels() -> None:
    source = _source("web/js/views/explore.js")

    assert "Minimum Reddit score" in source
    assert "Search post title, comment & summary…" in source
    assert "Min score" not in source


def test_explorer_requests_ten_server_paginated_evidence_rows() -> None:
    explore = _source("web/js/views/explore.js")
    state = _source("web/js/state.js")

    assert 'apiUrl("/api/evidence"' in explore
    assert "page_size: 10" in state
    assert "state.evidence.items" in explore
    assert ".slice(page * EVIDENCE_PAGE_SIZE" not in explore


def test_evidence_cards_separate_post_and_comment_and_measure_overflow() -> None:
    source = _source("web/js/views/explore.js")

    assert 'evidenceSection("Post"' in source
    assert 'evidenceSection("Comment"' in source
    assert 'data-evidence-expand' in source
    assert 'aria-expanded="false"' in source
    assert "scrollHeight" in source
    assert "clientHeight" in source
    assert "post_body_norm" in source
    assert "comment_body_norm" in source
    assert "external-link" in source


def test_dashboard_groups_predefined_and_discovered_charts() -> None:
    source = _source("web/js/views/dashboard.js")

    assert "Predefined categories" in source
    assert "fixed choices assigned by the model" in source
    assert "Discovered from your data" in source
    assert "found by clustering" in source
    assert "not_applicable = not a complaint" in source
    assert 'chartPanel("vehicles"' in source


def test_primary_navigation_promotes_qa_and_removes_pipeline_tab() -> None:
    source = _source("web/index.html")

    assert source.index('data-view="dashboard"') < source.index('data-view="qa"')
    assert source.index('data-view="qa"') < source.index('data-view="explorer"')
    assert 'data-view="pipeline"' not in source


def test_qa_is_no_longer_composed_inside_explorer() -> None:
    source = _source("web/js/views/explore.js")

    assert "qaView" not in source
    assert "qaBuildIndexBtn" not in source


def test_evidence_clamp_styles_are_present() -> None:
    source = _source("web/styles.css")

    assert ".evidence-copy.is-clamped" in source
    assert "-webkit-line-clamp: 2" in source
    assert ".evidence-external-link" in source


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


def test_dashboard_grouping_renders_in_browser(live_server, browser_page) -> None:
    page = browser_page
    _load_browser_with_data(page, live_server)

    assert page.get_by_role("heading", name="Predefined categories").count() == 1
    assert page.get_by_role("heading", name="Discovered from your data").count() == 1
    assert "fixed choices assigned by the model" in page.inner_text("#viewRoot")
    assert "not_applicable = not a complaint" in page.inner_text("#viewRoot")


def test_evidence_overflow_keyboard_and_server_page_navigation(live_server, browser_page) -> None:
    page = browser_page

    def evidence_route(route) -> None:
        page_number = int(route.request.url.split("page=")[1].split("&")[0])
        long_text = " ".join(["A detailed owner comment about repeated transmission shudder."] * 16)
        if page_number == 1:
            items = [
                {
                    "source_id": "long",
                    "source_type": "comment",
                    "title_norm": "Transmission thread",
                    "post_body_norm": "The original owner described the service timeline.",
                    "comment_body_norm": long_text,
                    "score_norm": 42,
                    "subreddit_norm": "silverado",
                    "sentiment": "negative",
                    "top_complaint_category": "transmission_torque_converter",
                    "permalink_norm": "/r/silverado/comments/long",
                },
                {
                    "source_id": "short",
                    "source_type": "post",
                    "title_norm": "Short post",
                    "post_body_norm": "Brief body.",
                    "comment_body_norm": "",
                    "score_norm": -3,
                    "subreddit_norm": "gm",
                    "sentiment": "neutral",
                    "top_complaint_category": "not_applicable",
                    "permalink_norm": "",
                },
            ]
        else:
            items = [{"source_id": "page-two", "source_type": "post", "title_norm": "Page two", "post_body_norm": "Second page evidence.", "score_norm": 1}]
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "items": items,
                "page": page_number,
                "page_size": 10,
                "total_items": 11,
                "total_pages": 2,
                "score_unit": "reddit_score",
            }),
        )

    page.route("**/api/evidence*", evidence_route)
    _load_browser_with_data(page, live_server)
    page.click('.tab[data-view="explorer"]')

    expand = page.locator('[data-evidence-expand]:not([hidden])').first
    expand.wait_for(timeout=10000)
    assert page.locator('[data-evidence-expand]:not([hidden])').count() == 1
    expand.focus()
    page.keyboard.press("Enter")
    assert expand.get_attribute("aria-expanded") == "true"
    assert "Page 1 of 2" in page.inner_text(".evidence-pager-info")

    page.click("#evidenceNextBtn")
    page.wait_for_function("() => document.querySelector('.evidence-pager-info')?.textContent.includes('Page 2 of 2')")
    assert "Page two" in page.inner_text("#viewRoot")


def test_collect_list_editor_creates_and_uses_named_list(live_server, browser_page) -> None:
    page = browser_page
    records = [
        {
            "id": "list_11111111111111111111111111111111",
            "display_name": "Existing list",
            "type": "custom",
            "subreddits": ["silverado"],
            "version": 1,
            "created_at": 1,
            "updated_at": 1,
        }
    ]
    saved_payload: dict = {}
    collect_payload: dict = {}

    def list_route(route) -> None:
        if route.request.method == "GET":
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"items": records}))
            return
        payload = route.request.post_data_json
        saved_payload.update(payload)
        created = {
            "id": "list_22222222222222222222222222222222",
            "display_name": payload["display_name"],
            "type": payload["type"],
            "subreddits": [value for value in payload["subreddits"] if value],
            "version": 1,
            "created_at": 2,
            "updated_at": 2,
        }
        records.append(created)
        route.fulfill(status=201, content_type="application/json", body=json.dumps(created))

    def collect_route(route) -> None:
        collect_payload.update(route.request.post_data_json)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "ok": True,
                "tag": live_server.tag,
                "job_id": "fixture-collect",
                "status": "completed",
                "completed_subreddits": 2,
                "total_subreddits": 2,
                "log": "complete",
                "started": True,
            }),
        )

    page.route("**/api/subreddit-lists*", list_route)
    page.route("**/api/collect", collect_route)
    _load_browser_with_data(page, live_server)
    page.click('.tab[data-view="gathering"]')

    page.click("#createSubredditListBtn")
    page.fill("#subredditListName", "Launch watchlist")
    page.select_option("#subredditListType", "custom")
    page.fill("#subredditListValues", "Silverado\nGMC")
    page.click("#saveSubredditListBtn")
    page.wait_for_function("() => document.querySelector('#subredditListSelect')?.value.endsWith('22222222222222222222222222222222')")

    assert saved_payload["display_name"] == "Launch watchlist"
    assert saved_payload["subreddits"] == ["Silverado", "GMC"]

    page.click("#collectBtn")
    page.wait_for_function("() => document.querySelector('#statusBar')?.textContent.includes('Collector completed')")
    assert collect_payload["subreddit_list_id"] == "list_22222222222222222222222222222222"
    assert "since_days" not in collect_payload


def test_analyze_navigates_to_pipeline_runs_inside_settings(live_server, browser_page) -> None:
    page = browser_page
    page.route(
        "**/api/analyze",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"run_id": "new-run", "job_id": "job-1"}),
        ),
    )
    page.route(
        "**/api/pipeline/runs*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps([{"run_id": "new-run", "state": "running", "started_at": 1}]),
        ),
    )
    page.route(
        "**/api/pipeline/status*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"run_id": "new-run", "state": "running", "steps": []}),
        ),
    )
    _load_browser_with_data(page, live_server)
    page.click('.tab[data-view="gathering"]')
    page.click("#analyzeBtn")
    page.wait_for_selector("#settingsPipelineTab[aria-selected='true']")

    assert page.locator('.tab[data-view="settings"]').get_attribute("aria-selected") == "true"
    assert page.locator("#pipelineRunSelect").count() == 1


def test_cluster_prompt_preview_and_reset_are_interactive(live_server, browser_page) -> None:
    page = browser_page
    default_prompt = "Default prompt with every required placeholder and JSON contract"

    def validate_route(route) -> None:
        prompt = route.request.post_data_json["prompt"]
        valid = prompt == default_prompt
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "valid": valid,
                "missing_placeholders": [] if valid else ["cluster_size"],
                "unknown_placeholders": [],
                "missing_output_keys": [] if valid else ["short_label"],
                "errors": [] if valid else ["Missing required placeholders: cluster_size"],
            }),
        )

    page.route("**/api/config/cluster-prompt/validate", validate_route)
    page.route(
        "**/api/config/cluster-prompt/reset",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"prompt": default_prompt, "validation": {"valid": True}}),
        ),
    )
    _load_browser_with_data(page, live_server)
    page.click('.tab[data-view="settings"]')

    page.fill("#cfg-prompt-cluster", "broken")
    page.click("#validateClusterPromptBtn")
    page.wait_for_function("() => document.querySelector('#clusterPromptValidation')?.textContent.includes('cluster_size')")

    page.click("#resetClusterPromptBtn")
    page.wait_for_function(f"() => document.querySelector('#cfg-prompt-cluster')?.value === {json.dumps(default_prompt)}")
    page.wait_for_function("() => document.querySelector('#clusterPromptValidation')?.textContent === 'Prompt is valid.'")
    assert page.inner_text("#clusterPromptValidation") == "Prompt is valid."


def test_qa_recovery_is_on_standalone_tab_and_polling_stops_on_navigation(live_server, browser_page) -> None:
    page = browser_page

    page.route(
        "**/api/qa/status*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "state": "stale",
                "detail": "The Q&A index belongs to older classified data.",
                "recovery_action": "rebuild",
                "doc_count": 12,
            }),
        ),
    )
    page.route(
        "**/api/qa/build-index",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"state": "running", "job_id": "qa-job", "started": True}),
        ),
    )
    _load_browser_with_data(page, live_server)

    assert page.get_by_role("heading", name="Ask your evidence").count() == 0
    page.click('.tab[data-view="qa"]')

    assert page.get_by_role("heading", name="Ask your evidence").count() == 1
    assert page.get_by_role("button", name="Rebuild for current data").count() == 1
    assert page.locator('.tab[data-view="explorer"]').get_attribute("aria-selected") == "false"

    page.click("#qaBuildIndexBtn")
    assert page.evaluate("async () => (await import('/static/js/state.js')).state.qaPollTimer !== null") is True
    page.click('.tab[data-view="explorer"]')
    assert page.evaluate("async () => (await import('/static/js/state.js')).state.qaPollTimer === null") is True


def test_dashboard_report_previews_are_sanitized_and_follow_approved_order(
    live_server, browser_page
) -> None:
    page = browser_page
    page.route(
        "**/api/reports/preview*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "tag": live_server.tag,
                    "synthesis": {
                        "markdown": "# Synthesis\n\n**Priority** <script>window.reportXss=1</script>",
                        "formats": {"markdown": "ready", "pdf": "ready"},
                    },
                    "trend": {
                        "markdown": "# Trend report\n\n**Signals to watch**",
                        "formats": {"markdown": "ready", "pdf": "ready"},
                    },
                }
            ),
        ),
    )
    _load_browser_with_data(page, live_server)
    page.evaluate(
        """async () => {
            const { state } = await import('/static/js/state.js');
            state.trendsData = {
                ok: true,
                clusters: [{
                    cluster_id: 1,
                    cluster_size: 4,
                    label: { short_label: 'Brake vibration' },
                    trend_signal: {
                        confidence_banner: 'medium',
                        velocity: { valid: true, direction: 'rising', velocity: 0.2 },
                        zscore: { valid: true, direction: 'stable', zscore: 0.4 },
                    },
                }],
            };
            const { setView } = await import('/static/js/nav.js');
            setView('dashboard');
        }"""
    )

    assert page.locator("#synthesisReportPreview strong", has_text="Priority").count() == 1
    assert page.locator("#synthesisReportPreview script").count() == 0
    assert page.evaluate("() => window.reportXss") is None
    order = page.evaluate(
        """() => ({
            synthesis: document.querySelector('#synthesisReportCard').compareDocumentPosition(
                document.querySelector('[data-chart="sentiment"]')),
            trendAfterSignals: document.querySelector('[data-signal-grid]').compareDocumentPosition(
                document.querySelector('#trendReportCard')),
            trendBeforeCharts: document.querySelector('#trendReportCard').compareDocumentPosition(
                document.querySelector('#quadrantChart')),
        })"""
    )
    assert order == {"synthesis": 4, "trendAfterSignals": 4, "trendBeforeCharts": 4}
    assert page.locator(".report-preview-scroll").count() == 2
