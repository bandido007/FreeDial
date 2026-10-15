"""Approved supervisor layout, role actions, team filtering and keyboard dialogs."""

import shutil
from pathlib import Path

import pytest
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from playwright.sync_api import expect, sync_playwright

from apps.supervisor.views import page_messages

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ARTIFACTS = ROOT / "test-artifacts" / "supervisor"
AXE = ROOT / "apps/agentdesk/tests/vendor/axe-4.10.3.min.js"


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
        "clientId": "test",
        "tokenUrl": "/oauth2/access_token",
        "revokeUrl": "/oauth2/revoke_token",
        "graphqlUrl": "/graphql",
        "liveUrl": "/ws/supervisor",
        "messages": page_messages(),
    }
    html = render_to_string("supervisor/index.html", {"config": config})
    page.add_init_script(path=str(HERE / "browser_stub.js"))

    def serve(route):
        url = route.request.url
        if url.endswith("/supervisor/"):
            route.fulfill(body=html, content_type="text/html")
        elif "jssip" in url:
            route.fulfill(body="/* test phone */", content_type="application/javascript")
        elif "/static/" in url:
            path = finders.find(url.split("/static/", 1)[1])
            route.fulfill(path=path) if path else route.abort()
        else:
            route.abort()

    page.route("https://desk.test/**", serve)
    page.goto("https://desk.test/supervisor/")
    page.fill("#email", "daudi@example.org")
    page.fill("#password", "test-only")
    page.click("button[type=submit]")
    expect(page.locator("#phone-chip")).to_have_text("Phone connected")
    expect(page.locator("#phone-chip")).to_have_attribute("data-sip-id", "websup1")
    page.select_option("#team-filter", "claims")
    yield page
    assert not errors, errors
    context.close()


@pytest.mark.parametrize("size", [(1366, 768), (1920, 1080)])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_supervisor_attention_layout_monitoring_and_accessibility(page, size, theme):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.evaluate("theme=>document.documentElement.dataset.theme=theme", theme)
    page.add_script_tag(path=str(AXE))
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    for state in ["team", "monitoring"]:
        if state == "monitoring":
            page.locator('[data-agent="peter"]').get_by_role("button", name="Monitor", exact=True).click()
        page.evaluate("document.fonts.ready")
        page.screenshot(path=str(ARTIFACTS / "latest-check.png"))
        assert page.evaluate(
            "document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight"
        ), page.evaluate(
            "({width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight,viewport:innerHeight})"
        )
        violations = page.evaluate(
            "async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa','wcag22aa']}})).violations"
        )
        assert not violations, [(v["id"], [n["target"] for n in v["nodes"]]) for v in violations]
        page.screenshot(path=str(ARTIFACTS / f"{state}-{theme}-{size[0]}x{size[1]}.png"))
    assert page.locator("#agents tr").first.get_attribute("data-agent") == "zawadi"
    assert page.locator("#agents tr").nth(1).get_attribute("data-agent") == "peter"
    assert page.evaluate("__supervisorTest.sockets") == 1


def test_team_filter_permissions_history_and_state_dialog(page):
    page.select_option("#team-filter", "front")
    expect(page.locator("#agents tr")).to_have_count(1)
    expect(page.locator("#agents")).to_contain_text("Juma")
    page.select_option("#team-filter", "claims")
    row = page.locator('[data-agent="zawadi"]')
    row.get_by_role("button", name="Change state").focus()
    page.evaluate(
        "__supervisorTest.event('agent.state',{agent:'halima',availability:'ready',activity:'idle'})"
    )
    expect(row.get_by_role("button", name="Change state")).to_be_focused()
    row.get_by_role("button", name="View history").click()
    expect(page.locator("#agent-history")).to_contain_text("Break")
    page.keyboard.press("Escape")
    row.get_by_role("button", name="Change state").click()
    page.get_by_role("button", name="Apply change", exact=True).click()
    expect(page.locator("#agent-action-error")).to_contain_text("Choose a reason")
    page.select_option("#agent-reason", "Meeting")
    page.get_by_role("button", name="Apply change", exact=True).click()
    expect(row).to_contain_text("Meeting")
    page.evaluate("__supervisorTest.data.can=['monitor']; __supervisorTest.socket.send()")
    expect(page.get_by_role("button", name="Change state", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Barge", exact=True)).to_have_count(0)


def test_a_delayed_snapshot_from_the_previous_sign_in_cannot_replace_the_new_desk(page):
    page.evaluate("""() => {
      const original=window.fetch;
      window.fetch=async(url,options={})=>{
        if(String(url).includes('graphql') && JSON.parse(options.body).query.includes('mySupervisorDesk')) {
          window.fetch=original;
          window.releaseOldSnapshot=()=>window.resolveOldSnapshot({json:async()=>({data:{answer:{status:true,data:{...__supervisorTest.data,name:'Previous session'}}}})});
          return new Promise(resolve=>window.resolveOldSnapshot=resolve);
        }
        return original(url,options);
      };
      __supervisorTest.event('agent.state',{agent:'zawadi',availability:'not_ready',activity:'idle',reason:'Break'});
    }""")
    page.wait_for_function("Boolean(window.releaseOldSnapshot)")
    page.click("#signout")
    expect(page.locator("#signin")).to_be_visible()
    page.fill("#password", "test-only")
    page.click("button[type=submit]")
    expect(page.locator("#desk")).to_be_visible()
    page.evaluate("async()=>{releaseOldSnapshot();await new Promise(resolve=>setTimeout(resolve,0));}")
    expect(page.locator("#me")).to_have_text("Daudi")
