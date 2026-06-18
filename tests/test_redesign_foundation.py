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
