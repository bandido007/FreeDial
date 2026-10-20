"""Angular's real production components against a real Django test API.

Only desk media/brain transports are deterministic fixtures; management reads,
OAuth and saves use Django through Playwright's request forwarding.
"""

import io
import json
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from django.core.management import call_command
from playwright.sync_api import expect, sync_playwright

from apps.agents.models import Agent
from apps.queues.models import Queue, SkillRuleSet
from apps.teams.models import Team

ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = ROOT / "test-artifacts/angular"
ORIGIN = os.environ.get("ANGULAR_TEST_URL", "http://127.0.0.1:4240")
AXE = ROOT / "apps/agentdesk/tests/vendor/axe-4.10.3.min.js"
pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        not os.environ.get("ANGULAR_TEST_URL"),
        reason="Run with ANGULAR_TEST_URL pointing at the Angular preview.",
    ),
]


@pytest.fixture
def angular_backend(live_server, make_user, api):
    global ORIGIN
    if os.environ.get("ANGULAR_TEST_URL") == "live":
        ORIGIN = live_server.url + "/app"
    call_command("seed_demo", password="test-demo-only", stdout=io.StringIO())
    user = make_user(
        email="angular-admin@example.org", is_superuser=True, is_staff=True, account_type="System"
    )
    client = api(user)
    paths = {
        "workspace": "/",
        "agents": "/manage/agents.agent",
        "agent-form": f"/manage/agents.agent?record={Agent.objects.get(name='Juma').pk}",
        "queue-form": f"/manage/queues.queue?record={Queue.objects.get(name='Claims').pk}",
        "rules": f"/manage/queues.skillruleset?record={SkillRuleSet.objects.get(name='R_CLAIMS').pk}",
        "team": f"/manage/teams.team?record={Team.objects.get(name='Front Desk').pk}",
        "users": "/manage/users.user",
        "roles": "/manage/rbac.role",
        "trunks": "/manage/telephony.trunk",
        "calls": "/manage/calls.call",
        "callbacks": "/manage/calls.callbacktask",
        "audit": "/manage/audit.auditlog",
        "reports": "/reports",
        "settings": "/manage/settings",
        "setup": "/setup",
    }
    session = {"access": client.access_token, "refresh": client.refresh_token, "expiresAt": 4102444800000}
    return live_server.url, session, paths


@pytest.fixture
def browser(angular_backend):
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=shutil.which("google-chrome") or shutil.which("chromium"))
        yield browser
        browser.close()


def open_page(browser, backend, size=(1366, 768), theme="light", kind=""):
    server, session, _paths = backend
    context = browser.new_context(
        viewport={"width": size[0], "height": size[1]}, reduced_motion="reduce", color_scheme=theme
    )
    context.add_cookies([{"name": "sessionid", "value": "unrelated-admin-cookie", "url": ORIGIN}])
    page = context.new_page()
    page.set_default_timeout(10000)
    page.add_init_script(
        "sessionStorage.setItem('algodial.angular.session',"
        + json.dumps(json.dumps(session))
        + ");localStorage.setItem('algodial.angular.theme','"
        + theme
        + "');sessionStorage.setItem('algodial.theme',JSON.stringify('"
        + theme
        + "'))"
    )
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    if kind:
        page.add_init_script("window.__nativeWebSocket=window.WebSocket")
        page.add_init_script(
            path=str(ROOT / f"apps/{'agentdesk' if kind == 'agent' else 'supervisor'}/tests/browser_stub.js")
        )
        page.add_init_script(
            """const FakeSocket=window.WebSocket;window.WebSocket=class {constructor(url,...args){return /\\/ws\\/(agent|supervisor)/.test(String(url))?new FakeSocket(url,...args):new __nativeWebSocket(url,...args)}};"""
        )

    def proxy(route):
        assert "cookie" not in route.request.all_headers(), "Angular API requests must omit admin cookies"
        parts = urlsplit(route.request.url)
        path = parts.path.removeprefix("/api")
        address = server + path + ("?" + parts.query if parts.query else "")
        response = route.fetch(url=address)
        route.fulfill(response=response)

    parts = urlsplit(ORIGIN)
    page.route(parts.scheme + "://" + parts.netloc + "/api/**", proxy)
    return context, page, errors


def capture(page, name, size, theme):
    page.evaluate("document.fonts.ready")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ARTIFACTS / f"{name}-{theme}-{size[0]}x{size[1]}.png"))
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), name
    (ARTIFACTS / f"{name}-{theme}-{size[0]}x{size[1]}-full.png").unlink(missing_ok=True)
    if page.evaluate("document.documentElement.scrollHeight > innerHeight"):
        page.screenshot(path=str(ARTIFACTS / f"{name}-{theme}-{size[0]}x{size[1]}-full.png"), full_page=True)
    page.evaluate(AXE.read_text())
    violations = page.evaluate(
        "async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa','wcag22aa']}})).violations"
    )
    assert not violations, (name, [(v["id"], [n["target"] for n in v["nodes"]]) for v in violations])


@pytest.mark.parametrize("size", [(1366, 768), (1920, 1080)])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_management_pages_use_only_existing_features_and_the_real_api(browser, angular_backend, size, theme):
    context, page, errors = open_page(browser, angular_backend, size, theme)
    for name, path in angular_backend[2].items():
        page.goto(ORIGIN + path)
        expect(page.locator(".app-topbar")).to_be_visible(timeout=10000)
        expect(page.locator(".sidebar .nav-group").first).to_be_visible()
        if "?record=" in path:
            expect(page.locator(".editor-panel")).to_be_visible(timeout=10000)
        elif path.startswith("/manage/") and path != "/manage/settings":
            expect(page.locator(".table-footer")).to_be_visible()
        elif path == "/setup":
            expect(page.locator(".setup-progress")).to_be_visible()
        elif path == "/reports":
            expect(page.locator(".report-section").first).to_be_visible()
        expect(page.locator(".sidebar nav")).not_to_contain_text("Workflows")
        capture(page, name, size, theme)
        assert not errors, errors
    context.close()


def test_real_save_inline_validation_routing_preview_and_permission_navigation(browser, angular_backend):
    context, page, errors = open_page(browser, angular_backend)
    page.goto(ORIGIN + "/manage/agents.skill?record=new")
    expect(page.locator(".editor-panel")).to_be_visible(timeout=10000)
    save = page.get_by_role("button", name="Save changes", exact=True)
    expect(save).to_be_in_viewport()
    page.locator('ad-field:has(label:text("Name")) input').fill("angular_skill")
    page.get_by_role("button", name="Save changes", exact=True).click()
    expect(page.locator(".alert.success")).to_contain_text("Saved")
    page.goto(ORIGIN + angular_backend[2]["rules"])
    expect(page.locator(".editor-panel")).to_be_visible(timeout=10000)
    page.select_option('[name="previewAgent"]', label="7101 Juma")
    page.select_option('[name="previewLanguage"]', "swahili")
    page.fill('[name="previewWait"]', "29")
    expect(page.locator(".rule-result")).to_contain_text("Does not match")
    page.fill('[name="previewWait"]', "30")
    expect(page.locator(".rule-result")).to_contain_text("All required skills match")
    page.locator('ad-field:has(label:text("Name")) input').fill("Unsaved draft")
    page.get_by_role("button", name="Sign out", exact=True).click()
    expect(page.locator("ad-login")).to_be_visible()
    assert page.evaluate("sessionStorage.getItem('algodial.angular.session')") is None
    assert not errors, errors
    context.close()


def test_mobile_navigation_search_and_keyboard_focus(browser, angular_backend):
    context, page, errors = open_page(browser, angular_backend, (390, 844))
    page.goto(ORIGIN + "/")
    toggle = page.get_by_role("button", name="Toggle navigation", exact=True)
    expect(page.locator(".page-heading")).to_contain_text("Welcome")
    toggle.click()
    search = page.get_by_role("searchbox", name="Find a page")
    expect(search).to_be_focused()
    search.fill("Agents")
    link = page.locator(".sidebar nav").get_by_role("link", name="Agents", exact=True)
    expect(link).to_be_visible()
    link.click()
    expect(page.locator(".breadcrumb strong")).to_have_text("Agents")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    toggle.click()
    expect(search).to_have_value("")
    expect(link).to_be_visible()
    page.keyboard.press("Escape")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(toggle).to_be_focused()
    capture(page, "agents-mobile", (390, 844), "light")
    assert not errors, errors
    context.close()


@pytest.mark.parametrize("size", [(1366, 768), (1920, 1080)])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_agent_states_use_the_angular_host_and_retained_phone_session(
    browser, angular_backend, size, theme, language="en"
):
    context, page, errors = open_page(browser, angular_backend, size, theme, "agent")
    page.goto(ORIGIN + "/agent")
    expect(page.locator("#phone-chip")).to_have_attribute("data-sip-id", "web7001")
    labels = (
        json.loads((ROOT / "apps/agentdesk/translations/sw.json").read_text()) if language == "sw" else {}
    )
    if language == "sw":
        page.locator(".language-control").get_by_role("button", name="SW", exact=True).click()

    def take(name):
        capture(page, name + ("-sw" if language == "sw" else ""), size, theme)

    take("agent-not-ready")
    page.keyboard.press("r")
    expect(page.locator("#state-title")).to_contain_text(labels.get("Ready", "Ready"))
    take("agent-ready")
    page.evaluate("__deskTest.offer()")
    take("agent-incoming")
    page.click("#answer")
    take("agent-talking")
    page.click("#hold")
    expect(page.locator("#state-title")).to_contain_text(labels.get("On hold", "On hold"))
    take("agent-on-hold")
    page.click("#resume")
    page.locator('.sidebar a[routerlink="/"]').first.click()
    expect(page.locator(".page-heading")).to_contain_text("Welcome")
    page.locator('.sidebar a[routerlink="/agent"]').click()
    expect(page.locator("#state-title")).to_contain_text(labels.get("On a call", "On a call"))
    assert page.evaluate("__deskTest.sockets") == 1
    page.click("#transfer-toggle")
    take("agent-transfer-form")
    assert page.locator("#transfer-go").evaluate("el=>el.getBoundingClientRect().bottom <= innerHeight"), (
        "Transfer action must fit the laptop viewport"
    )
    page.select_option("#transfer-kind", "consult")
    page.click("#transfer-go")
    expect(page.locator("#consult-complete")).to_be_visible()
    take("agent-transfer")
    assert page.locator("#consult-complete").evaluate("el=>el.getBoundingClientRect().bottom <= innerHeight")
    page.click("#consult-cancel")
    page.evaluate("__deskTest.wrap()")
    take("agent-wrap-up")
    page.select_option("#wrapup-code", "Information given")
    page.click("#wrapup-done")
    page.evaluate("__deskTest.ua.emit('disconnected')")
    take("agent-phone-problem")
    assert not errors, errors
    context.close()


@pytest.mark.parametrize("size", [(1366, 768), (1920, 1080)])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_supervisor_page_and_monitoring_use_the_angular_host(
    browser, angular_backend, size, theme, language="en"
):
    context, page, errors = open_page(browser, angular_backend, size, theme, "supervisor")
    page.goto(ORIGIN + "/supervisor")
    expect(page.locator("#phone-chip")).to_have_attribute("data-sip-id", "websup1")
    labels = (
        json.loads((ROOT / "apps/supervisor/translations/sw.json").read_text()) if language == "sw" else {}
    )
    if language == "sw":
        page.locator(".language-control").get_by_role("button", name="SW", exact=True).click()
    page.select_option("#team-filter", "claims")
    capture(page, "supervisor" + ("-sw" if language == "sw" else ""), size, theme)
    page.locator('[data-agent="peter"]').get_by_role(
        "button", name=labels.get("Monitor", "Monitor"), exact=True
    ).click()
    capture(page, "supervisor-monitoring" + ("-sw" if language == "sw" else ""), size, theme)
    assert not errors, errors
    context.close()


@pytest.mark.parametrize("size", [(1366, 768), (1920, 1080)])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_login_theme_and_keyboard_labels(browser, angular_backend, size, theme):
    context, page, errors = open_page(browser, angular_backend, size, theme)
    page.goto(ORIGIN + "/login")
    expect(page.get_by_label("Email address")).to_be_visible()
    expect(page.get_by_label("Password", exact=True)).to_be_visible()
    capture(page, "login", size, theme)
    assert not errors, errors
    context.close()


@pytest.fixture
def account_browser(angular_backend, request, make_user, settings):
    from apps.authentication import mfa_services, totp

    required = request.param == "required"
    if required:
        settings.MFA_REQUIRED_ACCOUNT_TYPES = ["External"]
    user = make_user(email="angular-account@example.org", must_change_password=required)
    secret = ""
    if not required:
        secret = mfa_services.begin_enrollment(user=user)["secret"]
        mfa_services.confirm_enrollment(user=user, code=totp._hotp(secret, totp.current_step()))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=shutil.which("google-chrome") or shutil.which("chromium")
        )
        yield browser, user.email, secret
        browser.close()


@pytest.mark.parametrize("account_browser", ["otp"], indirect=True)
def test_existing_otp_sign_in_uses_the_real_oauth_endpoint(account_browser, angular_backend):
    from apps.authentication import totp
    from conftest import PASSWORD

    browser, email, secret = account_browser
    context, page, errors = open_page(browser, angular_backend)
    page.goto(ORIGIN + "/login")
    page.get_by_label("Email address").fill(email)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.get_by_label("Two-factor code").fill(totp._hotp(secret, totp.current_step() + 1))
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app-topbar")).to_be_visible()
    expect(page.locator(".profile-name")).to_contain_text(email)
    assert not errors, errors
    context.close()


@pytest.mark.parametrize("account_browser", ["required"], indirect=True)
def test_required_password_and_mfa_steps_finish_with_recovery_codes(account_browser, angular_backend):
    from apps.authentication import totp
    from conftest import PASSWORD

    browser, email, _secret = account_browser
    context, page, errors = open_page(browser, angular_backend)
    page.goto(ORIGIN + "/login")
    page.get_by_label("Email address").fill(email)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.get_by_label("Current password").fill(PASSWORD)
    page.get_by_label("New password", exact=True).fill("Changed-for-ui-482!")
    page.get_by_label("Confirm new password").fill("Changed-for-ui-482!")
    page.get_by_role("button", name="Continue", exact=True).click()
    expect(page.locator("code.secret")).to_be_visible()
    secret = page.locator("code.secret").inner_text()
    page.get_by_label("Two-factor code").fill(totp._hotp(secret, totp.current_step()))
    page.get_by_role("button", name="Continue", exact=True).click()
    expect(page.locator("pre.secret")).to_be_visible()
    assert len(page.locator("pre.secret").inner_text().splitlines()) == 10
    page.get_by_role("button", name="I saved my codes — continue", exact=True).click()
    expect(page.locator(".app-topbar")).to_be_visible()
    expect(page.locator(".profile-name")).to_contain_text(email)
    assert not errors, errors
    context.close()


def test_swahili_agent_labels_fit_the_angular_laptop_layout(browser, angular_backend):
    test_agent_states_use_the_angular_host_and_retained_phone_session(
        browser, angular_backend, (1366, 768), "light", "sw"
    )


def test_swahili_supervisor_labels_fit_the_angular_laptop_layout(browser, angular_backend):
    test_supervisor_page_and_monitoring_use_the_angular_host(
        browser, angular_backend, (1366, 768), "light", "sw"
    )
