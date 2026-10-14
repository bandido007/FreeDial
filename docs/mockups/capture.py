"""Capture section 8.1 static mockups. Run from the repository root.

Requires Playwright with Chromium or Chrome and Pillow.
Starts and stops an isolated loopback static server automatically.
"""

import json
import shutil
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
STATES = ["not-ready", "ready", "incoming", "talking", "transfer", "wrap-up", "phone-problem", "supervisor"]
SIZES = [(1366, 768), (1920, 1080)]


def capture():
    results = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(ROOT)))
    Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=shutil.which("google-chrome") or shutil.which("chromium"))
        for theme in ["light", "dark"]:
            for width, height in SIZES:
                page = browser.new_page(
                    viewport={"width": width, "height": height},
                    device_scale_factor=1,
                    reduced_motion="reduce",
                    color_scheme=theme,
                )
                errors = []
                page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                for state in STATES:
                    page.goto(f"{base_url}/index.html?state={state}&theme={theme}")
                    page.evaluate("document.fonts.ready")
                    assert page.evaluate('document.fonts.check("15px Atkinson")'), "Font did not load"
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                        f"Horizontal overflow: {state}"
                    )
                    assert page.evaluate("document.documentElement.scrollHeight <= innerHeight"), (
                        f"Vertical overflow: {state}"
                    )
                    assert not errors, errors
                    filename = f"{state}-{theme}-{width}x{height}.png"
                    page.screenshot(path=str(ROOT / "screenshots" / filename), full_page=False)
                    results.append(
                        {
                            "file": filename,
                            "width": width,
                            "height": height,
                            "theme": theme,
                            "state": state,
                            "overflow": False,
                            "script_errors": [],
                        }
                    )
                # Extra specified error/monitoring variants, outside the required 32-image matrix.
                for state, extra in [
                    ("not-ready", "variant=missed"),
                    ("phone-problem", "variant=microphone"),
                    ("supervisor", "monitor=1"),
                ]:
                    page.goto(f"{base_url}/index.html?state={state}&theme={theme}&{extra}")
                    page.evaluate("document.fonts.ready")
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight"
                    ), page.evaluate("({height:document.documentElement.scrollHeight,viewport:innerHeight})")
                    page.screenshot(
                        path=str(
                            ROOT
                            / "screenshots"
                            / f"{state}-{extra.split('=')[1]}-{theme}-{width}x{height}.png"
                        )
                    )
                page.close()
        browser.close()
    server.shutdown()
    server.server_close()
    (ROOT / "screenshots" / "manifest.json").write_text(json.dumps(results, indent=2) + "\n")
    for theme in ["light", "dark"]:
        sheet = Image.new("RGB", (1256, 8 * 224 + 48), "#141C1E" if theme == "dark" else "#F4F6F5")
        draw = ImageDraw.Draw(sheet)
        ink = "#E6ECEA" if theme == "dark" else "#1C2B2D"
        draw.text(
            (16, 12), f"AlgoDial static mockups / {theme} / left 1366 x 768 / right 1920 x 1080", fill=ink
        )
        for row, state in enumerate(STATES):
            for col, (width, height) in enumerate(SIZES):
                x, y = 16 + col * 624, 48 + row * 224
                draw.text((x, y), state, fill=ink)
                image = Image.open(ROOT / "screenshots" / f"{state}-{theme}-{width}x{height}.png").convert(
                    "RGB"
                )
                image.thumbnail((608, 192))
                sheet.paste(image, (x, y + 20))
        sheet.save(ROOT / "screenshots" / f"overview-{theme}.jpg", quality=90)
    print(f"Captured and checked {len(results)} required screenshots, plus 12 supplemental scenarios.")  # noqa: T201


if __name__ == "__main__":
    capture()
