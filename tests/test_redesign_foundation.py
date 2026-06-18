"""Phase 0 redesign foundation tests.

Separate from tests/test_phase0.py (the original project's correctness suite).
Characterization tests here guard behavior across the module-split refactor.
"""
import json
import urllib.request


def _get(url: str):
    return urllib.request.urlopen(url, timeout=10).read().decode()


def test_api_run_serves_chart_payload(live_server):
    raw = _get(f"{live_server.url}/api/run?tag={live_server.tag}")
    data = json.loads(raw)
    assert "chart_specs" in data, "run payload must expose chart_specs at top level"
    assert "chart_data" in data, "run payload must expose chart_data at top level"
    assert "sentiment" in data["chart_data"], "sentiment chart data expected from golden fixture"


def test_index_serves_seven_tabs(live_server):
    html = _get(f"{live_server.url}/")
    for view in ["dashboard", "collect", "classify", "explore", "briefing", "trends", "qa"]:
        assert f'data-view="{view}"' in html, f"tab {view} missing from index.html"


# ---------------------------------------------------------------------------
# Chart contract tests — require Playwright + Chromium (skip gracefully if absent)
# ---------------------------------------------------------------------------

SAMPLES = {
    "bar": ({"type": "bar", "x_field": "sentiment", "y_field": "count", "value_format": "count", "minimum_rows": 1},
            [{"sentiment": "positive", "count": 5}, {"sentiment": "negative", "count": 3}]),
    "grouped_bar": ({"type": "grouped_bar", "x_field": "powertrain", "series_keys": ["a", "b"], "value_format": "pct", "minimum_rows": 1},
                    [{"powertrain": "EV", "a": 10, "b": 4}, {"powertrain": "ICE", "a": 6, "b": 2}]),
    "stacked_bar": ({"type": "stacked_bar", "x_field": "vehicle_mentioned", "series_field": "issue_severity", "value_field": "count", "series_order": ["critical", "minor"], "minimum_rows": 1},
                    [{"vehicle_mentioned": "X", "issue_severity": "critical", "count": 3}, {"vehicle_mentioned": "X", "issue_severity": "minor", "count": 2}]),
    "stacked_bar_100": ({"type": "stacked_bar_100", "x_field": "vehicle", "series_order": ["negative", "positive"], "minimum_rows": 1},
                        [{"vehicle": "X", "negative": 4, "positive": 6}, {"vehicle": "Y", "negative": 1, "positive": 9}]),
    "scatter": ({"type": "scatter", "x_field": "volume", "y_field": "pct_negative", "label_field": "theme", "color_field": "priority", "color_map": {"Fix now": "#c2413b"}, "minimum_rows": 1},
                [{"volume": 12, "pct_negative": 80, "theme": "range", "priority": "Fix now"}]),
    "heatmap": ({"type": "heatmap", "minimum_rows": 1},
                {"rows": ["a", "b"], "columns": ["x", "y"], "values": [[1, 2], [3, None]]}),
}

EXPECTED_SERIES_TYPE = {
    "bar": "bar", "grouped_bar": "bar", "stacked_bar": "bar",
    "stacked_bar_100": "bar", "scatter": "scatter", "heatmap": "heatmap",
}


def test_chart_contract_all_types_map_to_echarts(live_server, browser_page):
    page = browser_page
    page.goto(live_server.url)
    for ctype, (spec, data) in SAMPLES.items():
        series_type = page.evaluate(
            """async ([spec, data]) => {
                const m = await import('/static/js/charts.js');
                const o = m.buildChartOption(spec, data);
                return (o && Array.isArray(o.series) && o.series.length) ? o.series[0].type : null;
            }""",
            [spec, data],
        )
        assert series_type == EXPECTED_SERIES_TYPE[ctype], f"{ctype} mapped to {series_type}"


def test_render_into_draws_canvas_and_disposes(live_server, browser_page):
    page = browser_page
    page.goto(live_server.url)
    # Inject ECharts as a global before calling renderChartInto, which needs window.echarts.
    # (index.html will load echarts formally in Task 5; this is the interim bridge.)
    page.add_script_tag(url=f"{live_server.url}/static/vendor/echarts.min.js")
    spec, data = SAMPLES["bar"]
    result = page.evaluate(
        """async ([spec, data]) => {
            const m = await import('/static/js/charts.js');
            const el = document.createElement('div');
            el.style.width = '400px'; el.style.height = '300px';
            document.body.appendChild(el);
            m.renderChartInto(el, spec, data);
            const drawn = el.querySelector('canvas') !== null;
            m.disposeChart(el);
            const cleared = el.querySelector('canvas') === null;
            return { drawn, cleared };
        }""",
        [spec, data],
    )
    assert result["drawn"], "renderChartInto must create a canvas"
    assert result["cleared"], "disposeChart must remove the canvas"


def test_design_tokens_defined(live_server, browser_page):
    page = browser_page
    page.goto(live_server.url)
    required = ["--status-failed", "--status-running", "--status-done", "--data-negative", "--radius-lg"]
    for token in required:
        value = page.evaluate(
            "t => getComputedStyle(document.documentElement).getPropertyValue(t).trim()", token
        )
        assert value, f"design token {token} is not defined"
    # Architecture-review invariant: failed != data-negative (separate hue lanes)
    failed = page.evaluate("() => getComputedStyle(document.documentElement).getPropertyValue('--status-failed').trim()")
    negative = page.evaluate("() => getComputedStyle(document.documentElement).getPropertyValue('--data-negative').trim()")
    assert failed != negative, "system 'failed' must not reuse the data-negative red"


# ---------------------------------------------------------------------------
# Integration smoke tests — guard the split + ECharts wiring (Task 5 targets)
# ---------------------------------------------------------------------------

def _load_app_with_data(page, live_server):
    # Navigate to the app and trigger a data load via the tag input.
    # expect_response is a context manager — we enter it before the action that
    # fires the request, so Playwright can capture the response reliably.
    page.goto(live_server.url)
    page.fill("#tagInput", live_server.tag, timeout=5000)  # explicit timeout so fill failures surface immediately
    with page.expect_response(lambda r: "/api/run" in r.url and r.status == 200, timeout=15000) as resp_info:
        page.dispatch_event("#tagInput", "change")
    # resp_info.value is available for debugging after this block


def test_explore_charts_render_as_echarts_canvas(live_server, browser_page):
    # RED phase: current app uses hand-drawn HTML/SVG, not ECharts.
    # This test defines the target — Task 5 makes it green by wiring ECharts.
    page = browser_page
    _load_app_with_data(page, live_server)
    page.click('.tab[data-view="explore"]')
    # ECharts renders into <canvas> elements; none exist in the current monolith
    page.wait_for_selector("[data-chart] canvas", timeout=30000)
    canvas_count = page.eval_on_selector_all("[data-chart] canvas", "els => els.length")
    assert canvas_count >= 5, f"expected >=5 ECharts canvases in Explore, got {canvas_count}"


def test_all_tabs_no_console_errors(live_server, browser_page):
    # Guard: navigating through every tab should not produce JS errors
    page = browser_page
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    _load_app_with_data(page, live_server)
    for view in ["dashboard", "collect", "classify", "explore", "briefing", "trends", "qa"]:
        page.click(f'.tab[data-view="{view}"]')
        page.wait_for_timeout(500)  # bumped from 200ms to give each tab time to settle
    assert errors == [], f"console/page errors: {errors}"


def test_no_horizontal_overflow_three_viewports(live_server, browser_page):
    # Guard: no horizontal scrollbar at desktop, tablet, or mobile widths
    page = browser_page
    for width, height in [(1440, 900), (768, 1024), (375, 812)]:
        page.set_viewport_size({"width": width, "height": height})
        _load_app_with_data(page, live_server)
        for view in ["dashboard", "explore"]:
            page.click(f'.tab[data-view="{view}"]')
            page.wait_for_timeout(300)
            # scrollWidth > clientWidth + 1 means real overflow (1px tolerance for rounding)
            html_overflow = page.evaluate(
                "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
            )
            body_overflow = page.evaluate(
                "() => document.body.scrollWidth > document.body.clientWidth + 1"
            )
            assert not html_overflow, f"html horizontal overflow on {view} at {width}px"
            assert not body_overflow, f"body horizontal overflow on {view} at {width}px"
