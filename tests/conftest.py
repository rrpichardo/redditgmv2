"""Fixtures for redesign browser/integration tests.

live_server: boots a real uvicorn against ROOT, seeded with the golden fixture
under a throwaway tag (runtime/ is gitignored). browser_page: a Playwright
Chromium page; skips (does not fail) when Playwright/Chromium is unavailable.
"""
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
TEST_TAG = "redesign_test_fixture"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Server:
    def __init__(self, url: str, tag: str):
        self.url = url
        self.tag = tag


@pytest.fixture(scope="session")
def live_server():
    # Seed classified data so /api/run returns chart payloads for the test tag
    dest = ROOT / "runtime" / TEST_TAG / "classified" / "classified_posts.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "golden_classified.csv", dest)

    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/", timeout=1)
                break
            except Exception:
                time.sleep(0.1)
        else:
            proc.terminate()
            pytest.fail("live_server did not become ready")
        yield _Server(base, TEST_TAG)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(ROOT / "runtime" / TEST_TAG, ignore_errors=True)


@pytest.fixture
def browser_page():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        pytest.skip("playwright not installed")
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            pytest.skip("chromium not installed (run: playwright install chromium)")
        page = browser.new_page()
        try:
            yield page
        finally:
            browser.close()
