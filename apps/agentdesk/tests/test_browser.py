"""Exercise the production markup and JS with deterministic SIP/API fixtures.

Real OAuth, commands and isolation are covered by test_desk/test_live; the lab
script verifies WebRTC against Asterisk. These tests cover the visible workflow.
"""

import shutil
from pathlib import Path

import pytest
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from playwright.sync_api import expect, sync_playwright

from apps.agentdesk.views import english_messages

HERE = Path(__file__).resolve().parent
ARTIFACTS = HERE.parents[2] / "test-artifacts" / "agentdesk"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=shutil.which("google-chrome") or shutil.which("chromium"))
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1366, "height": 768}, reduced_motion="reduce")
    page = context.new_page()
    page.set_default_timeout(5000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    config = {
        "clientId": "browser-test",
        "tokenUrl": "/oauth2/access_token",
        "revokeUrl": "/oauth2/revoke_token",
        "graphqlUrl": "/graphql",
        "liveUrl": "/ws/agent",
        "messages": english_messages(),
    }
    html = render_to_string("agentdesk/index.html", {"config": config})
    page.add_init_script(path=str(HERE / "browser_stub.js"))

    def serve(route):
        url = route.request.url
        if url.endswith("/agent/"):
            route.fulfill(body=html, content_type="text/html")
        elif "jssip-3.12.0.min.js" in url:
            route.fulfill(
                body="/* SIP is replaced only in this test. */", content_type="application/javascript"
            )
        else:
            path = finders.find(url.split("/static/", 1)[1]) if "/static/" in url else None
            if path:
                route.fulfill(path=path)
            else:
                route.abort()

    page.route("https://desk.test/**", serve)
    page.goto("https://desk.test/agent/")
    assert not errors, errors
    expect(page.locator("#signin")).to_be_visible(timeout=5000)
    page.fill("#email", "juma@example.org")
    page.fill("#password", "test-only")
    page.click("button[type=submit]")
    expect(page.locator("#phone-chip")).to_have_text("Phone connected")
    expect(page.locator("#phone-chip")).to_have_attribute("data-sip-id", "web7001")
    expect(page.locator("#live-chip")).to_have_text("Live: on")
    yield page
    assert not errors, errors
    context.close()


def check_view(page):
    page.evaluate("document.fonts.ready")
    assert page.evaluate("document.fonts.check('15px Atkinson')")
    dimensions = page.evaluate(
        "({width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight,viewport:innerHeight})"
    )
    assert dimensions["width"] <= page.viewport_size["width"], dimensions
    assert dimensions["height"] <= page.viewport_size["height"], dimensions
    if not page.evaluate("Boolean(window.axe)"):
        page.add_script_tag(path=str(HERE / "vendor" / "axe-4.10.3.min.js"))
    results = page.evaluate(
        "async () => (await axe.run(document, {runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa','wcag22aa']}})).violations"
    )
    assert not results, [(v["id"], [n["target"] for n in v["nodes"]]) for v in results]


@pytest.mark.parametrize("size", [(1366, 768), (1920, 1080)])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_approved_states_fit_and_pass_accessibility(page, size, theme):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.evaluate("theme => document.documentElement.dataset.theme=theme", theme)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    def capture(state):
        check_view(page)
        page.screenshot(path=str(ARTIFACTS / f"{state}-{theme}-{size[0]}x{size[1]}.png"))

    capture("not-ready")
    page.evaluate("__deskTest.ready()")
    capture("ready")
    page.evaluate("__deskTest.offer()")
    capture("incoming")
    page.click("#answer")
    capture("talking")
    page.click("#transfer-toggle")
    page.select_option("#transfer-kind", "consult")
    page.click("#transfer-go")
    capture("transfer")
    page.click("#consult-cancel")
    page.click("#hangup")
    capture("wrap-up")
    page.select_option("#wrapup-code", "Information given")
    page.click("#wrapup-done")
    page.evaluate("__deskTest.ua.emit('disconnected')")
    capture("phone-problem")
    assert page.evaluate("__deskTest.sockets") == 1


def focus_by_tab(page, selector):
    for _ in range(40):
        if page.locator(selector).evaluate("element => element === document.activeElement"):
            return
        page.keyboard.press("Tab")
    raise AssertionError(f"Not reachable by Tab: {selector}")


def test_keyboard_call_workflow_preserves_notes_and_finishes_not_ready(page):
    # Use the keyboard for every action; shortcuts stay inactive in editable fields.
    page.keyboard.press("r")
    expect(page.locator("#state-menu")).to_be_visible()
    page.keyboard.press("Enter")  # focused Go Ready
    expect(page.locator("#state-title")).to_have_text("Ready")
    page.evaluate("__deskTest.offer()")
    page.keyboard.press("Enter")
    expect(page.locator("#state-title")).to_have_text("On a call")
    page.keyboard.press("h")
    expect(page.locator("#state-title")).to_have_text("On hold")
    page.keyboard.press("h")
    page.keyboard.press("m")
    expect(page.locator("#mute")).to_have_attribute("aria-pressed", "true")
    focus_by_tab(page, "#notes")
    page.keyboard.type("Receipt requested. h m t e")
    assert page.evaluate("__deskTest.state.activity") == "talking"
    page.keyboard.press("Tab")
    page.keyboard.press("t")
    page.keyboard.press("ArrowDown")  # consult
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")
    expect(page.locator("#consult")).to_be_visible()
    focus_by_tab(page, "#consult-cancel")
    page.keyboard.press("Enter")
    page.keyboard.press("e")
    expect(page.locator("#wrapup")).to_be_visible()
    expect(page.locator("#notes")).to_have_value("Receipt requested. h m t e")
    focus_by_tab(page, "#wrapup-code")
    page.keyboard.press("i")
    page.keyboard.press("Tab")  # notes
    page.keyboard.press("Tab")  # finish ready
    page.keyboard.press("Tab")  # finish not ready
    page.keyboard.press("Enter")
    expect(page.locator("#notice")).to_contain_text("Choose a reason")
    page.keyboard.press("b")  # focused wrap-up reason
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Enter")
    expect(page.locator("#state-title")).to_have_text("Not ready: Break")
    saved = page.evaluate("__deskTest.commands.find(c => c.query.includes('finishMyWrapup')).variables")
    assert saved == {
        "code": "Information given",
        "notes": "Receipt requested. h m t e",
        "ready": False,
        "reason": "Break",
    }


def test_mic_problem_blocks_ready_and_reconnect_recovers_without_going_ready(page):
    page.evaluate("__deskTest.ready(); __deskTest.ua.emit('disconnected')")
    expect(page.locator("#empty-description")).to_contain_text("Press Reconnect")
    assert page.locator("#ready").is_disabled()
    page.evaluate(
        "() => { navigator.mediaDevices.getUserMedia=async()=>{throw new DOMException('blocked','NotAllowedError')}; }"
    )
    page.click("#reconnect")
    expect(page.locator("#empty-description")).to_contain_text("blocking the microphone")
    page.evaluate("() => { navigator.mediaDevices.getUserMedia=async()=>({getTracks:()=>[{stop(){}}]}); }")
    page.click("#reconnect")
    expect(page.locator("#phone-chip")).to_have_text("Phone connected")
    expect(page.locator("#phone-chip")).to_have_attribute("data-sip-id", "web7001")
    assert page.evaluate("window.agentDesk.state.availability") == "not_ready"


def test_missing_code_keeps_wrapup_and_keypad_sends_only_valid_digits(page):
    page.evaluate("__deskTest.ready(); __deskTest.offer()")
    page.click("#answer")
    page.click("#keypad-toggle")
    page.fill("#digits", "12#")
    page.click("#send-digits")
    assert page.evaluate("__deskTest.digits") == "12#"
    page.click("#hangup")
    page.click("#wrapup-done")
    expect(page.locator("#notice")).to_have_text("Choose a wrap-up code to finish this call.")
    assert page.evaluate("window.agentDesk.state.activity") == "wrapup"


def test_timeout_keeps_an_accessible_draft_and_a_phone_problem_blocks_finishing_ready(page):
    page.evaluate("__deskTest.ready(); __deskTest.offer()")
    page.click("#answer")
    page.fill("#notes", "The member will send a receipt.")
    page.click("#hangup")
    page.evaluate("__deskTest.ua.emit('disconnected')")
    expect(page.locator("#wrapup-done")).to_be_disabled()
    expect(page.locator("#wrapup-not-ready")).to_be_enabled()
    page.evaluate(
        "__deskTest.event('agent.wrapped_up',{by:'timeout'}); __deskTest.change({activity:'idle',wrapupCall:null})"
    )
    expect(page.locator("#unsaved-notes")).to_be_visible()
    page.locator("#unsaved-notes summary").click()
    expect(page.locator("#recovery-notes")).to_have_value("The member will send a receipt.")


def test_next_sip_offer_does_not_show_the_previous_callers_context(page):
    page.evaluate("__deskTest.ready(); __deskTest.offer()")
    page.click("#answer")
    page.click("#hangup")
    page.select_option("#wrapup-code", "Information given")
    page.click("#wrapup-done")
    page.evaluate("__deskTest.incomingBeforeOffer()")
    expect(page.locator("#offer-caller")).to_have_text("+255 713 000 456")
    expect(page.locator("#offer-summary")).to_have_text("No AI summary for this call.")


def test_delayed_snapshot_from_previous_signin_cannot_replace_the_next_agents_desk(page):
    page.evaluate("""() => {
      const original = fetch;
      let delayed = false;
      window.__releaseOldDesk = null;
      window.fetch = async (url, options) => {
        const answer = await original(url, options);
        if (!delayed && String(url).includes('graphql') && options.body.includes('myAgentDesk')) {
          delayed = true;
          return await new Promise(resolve => { window.__releaseOldDesk = () => resolve(answer); });
        }
        return answer;
      };
      __deskTest.socket.onmessage({data:JSON.stringify({type:'queues.changed'})});
    }""")
    page.wait_for_function("Boolean(window.__releaseOldDesk)")
    page.click("#signout")
    page.evaluate("__deskTest.uid='neema'; __deskTest.name='Neema'")
    page.fill("#email", "neema@example.org")
    page.fill("#password", "test-only")
    page.click("button[type=submit]")
    expect(page.locator("#agent-name")).to_have_text("Neema")
    page.evaluate("window.__releaseOldDesk()")
    expect(page.locator("#agent-name")).to_have_text("Neema")
