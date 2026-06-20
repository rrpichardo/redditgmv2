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
    assert page.inner_text("#clusterPromptValidation") == "Prompt is valid."
