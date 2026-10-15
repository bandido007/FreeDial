"""UX_PRINCIPLES.md section 3 and 8: "First page load after sign-in: under 3 s on a slow 3G-class
connection"; "works on a throttled slow network in Playwright".

The page is rendered by Django, then served with its static files by a small local web server, so
every byte really crosses Chrome's throttled network: WebPageTest's "3G Slow" profile (400 ms round
trip, 400 kbps). Production also compresses static files (WhiteNoise): this uncompressed run is the
harder case.
"""

import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from django.contrib.staticfiles import finders
from django.test import Client
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.django_db

SLOW_3G = {"offline": False, "latency": 400, "downloadThroughput": 50_000, "uploadThroughput": 50_000}
BUDGET_SECONDS = 3.0
TYPES = {".js": "application/javascript", ".css": "text/css", ".ttf": "font/ttf", ".woff2": "font/woff2"}


def serve(page_html: bytes):
    """A local web server: the rendered page at /agent/, static files under /static/."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/agent/"):
                body, kind = page_html, "text/html; charset=utf-8"
            elif self.path.startswith("/static/") and (
                found := finders.find(self.path[len("/static/") :].split("?")[0])
            ):
                with open(found, "rb") as file:
                    body = file.read()
                kind = TYPES.get(found[found.rfind(".") :], "application/octet-stream")
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_the_agent_page_is_usable_within_3_seconds_on_slow_3g():
    page_html = Client().get("/agent/").content  # rendered before the browser starts (ORM outside its loop)
    server = serve(page_html)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=shutil.which("google-chrome") or shutil.which("chromium")
            )
            page = browser.new_page(viewport={"width": 1366, "height": 768})
            cdp = page.context.new_cdp_session(page)
            cdp.send("Network.enable")
            cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
            cdp.send("Network.emulateNetworkConditions", SLOW_3G)
            loaded = []
            page.on("response", lambda response: loaded.append((response.url, len(response.body() or b""))))
            started = time.monotonic()
            page.goto(f"http://127.0.0.1:{server.server_port}/agent/", wait_until="commit")
            sign_in = page.locator("#signin-form button[type=submit]")
            while not (sign_in.count() and sign_in.inner_text().strip()):
                assert time.monotonic() - started < 15, "the sign-in form never became usable"
                time.sleep(0.02)
            took = time.monotonic() - started
            browser.close()
    finally:
        server.shutdown()
    assert took < BUDGET_SECONDS, f"sign-in usable after {took:.1f} s on 3G Slow (budget {BUDGET_SECONDS} s)"
    assert not any("jssip" in url for url, _ in loaded), "the 240 KB phone library waits for sign-in"
    first_load = sum(size for _, size in loaded)
    assert first_load < 150_000, f"first load {first_load // 1024} KB: keep it small (UI_DESIGN.md 7)"
