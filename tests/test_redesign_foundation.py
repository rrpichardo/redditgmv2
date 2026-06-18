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
