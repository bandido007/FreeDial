"""The shared management UI with real Django-rendered forms and demo-centre records."""

import io
import json
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.core.management import call_command
from django.test import Client
from playwright.sync_api import expect, sync_playwright

from apps.agents.models import Agent, Skill
from apps.ai.models import AiAgent
from apps.core.crypto import encrypt_secret
from apps.queues.models import EntryPoint, Queue, SkillRuleSet
from apps.rbac.models import Role
from apps.teams.models import Team
from apps.telephony.models import SipAccount

ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = ROOT / "test-artifacts" / "management"
AXE = ROOT / "apps/agentdesk/tests/vendor/axe-4.10.3.min.js"
pytestmark = pytest.mark.django_db


@pytest.fixture
def management_pages():
    call_command("seed_demo", password="test-demo-only", stdout=io.StringIO())
    person = get_user_model().objects.create_superuser(email="ui-browser@example.org", password="test-only")
    client = Client()
    client.force_login(person)
    account = SipAccount.objects.create(
        sip_id="ui1003",
        kind="SOFTPHONE",
        display_name="Juma",
        password_encrypted=encrypt_secret("test-only"),
        codecs="ulaw,alaw",
        context="lab",
    )
    objects = {
        "user": get_user_model().objects.get(email="juma@example.org"),
        "role": Role.objects.first(),
        "account": account,
        "agent": Agent.objects.get(name="Juma"),
        "skill": Skill.objects.get(name="claims"),
        "team": Team.objects.get(name="Front Desk"),
        "queue": Queue.objects.get(name="Claims"),
        "rules": SkillRuleSet.objects.get(name="R_CLAIMS"),
        "entry": EntryPoint.objects.get(number="803"),
        "ai": AiAgent.objects.get(name="Jambo AI"),
    }
    paths = {
        "management": "/admin/",
        "records": "/admin/agents/agent/",
        "reports": "/admin/reports/report/",
        "login": "/admin/login/",
        "settings": "/admin/configuration/systemsetting/?key=SUPERVISOR_LONG_CALL_SECONDS",
    }
    for name, obj in objects.items():
        paths[name] = f"/admin/{obj._meta.app_label}/{obj._meta.model_name}/{obj.pk}/change/"
    paths["delete"] = f"/admin/queues/queue/{objects['queue'].pk}/delete/"
    pages = {}
    for name, path in paths.items():
        response = Client().get(path) if name == "login" else client.get(path)
        assert response.status_code == 200, (path, response.status_code)
        pages[path] = (name, response.content)
    i18n = client.get("/admin/jsi18n/").content
    return pages, paths, i18n, objects


@pytest.fixture
def browser(management_pages):
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=shutil.which("google-chrome") or shutil.which("chromium"))
        yield browser
        browser.close()


def open_page(browser, pages, i18n, size=(1366, 768), theme="light"):
    context = browser.new_context(
        viewport={"width": size[0], "height": size[1]}, reduced_motion="reduce", color_scheme=theme
    )
    page = context.new_page()
    page.set_default_timeout(5000)
    page.add_init_script("sessionStorage.setItem('algodial.theme',JSON.stringify('" + theme + "'))")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def serve(route):
        parts = urlsplit(route.request.url)
        path = parts.path
        address = path + ("?" + parts.query if parts.query else "")
        if address in pages:
            route.fulfill(body=pages[address][1], content_type="text/html; charset=utf-8")
        elif path == "/admin/jsi18n/":
            route.fulfill(body=i18n, content_type="application/javascript")
        elif path.startswith("/static/"):
            asset = finders.find(path.removeprefix("/static/"))
            route.fulfill(path=asset) if asset else route.abort()
        elif path == "/admin/autocomplete/":
            route.fulfill(json={"results": [], "pagination": {"more": False}})
        else:
            route.abort()

    page.route("https://desk.test/**", serve)
    return context, page, errors


@pytest.mark.parametrize("size", [(1366, 768), (1920, 1080)])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_all_management_surfaces_share_accessible_light_and_dark_forms(
    browser, management_pages, size, theme
):
    pages, paths, i18n, _ = management_pages
    context, page, errors = open_page(browser, pages, i18n, size, theme)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    findings = []
    for name, path in paths.items():
        page.goto("https://desk.test" + path)
        page.evaluate("document.fonts.ready")
        page.screenshot(path=str(ARTIFACTS / f"{name}-{theme}-{size[0]}x{size[1]}.png"))
        if page.evaluate("document.documentElement.scrollHeight > innerHeight"):
            page.screenshot(
                path=str(ARTIFACTS / f"{name}-{theme}-{size[0]}x{size[1]}-full.png"), full_page=True
            )
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), name
        assert page.evaluate("document.fonts.check('15px Atkinson')"), name
        page.add_script_tag(path=str(AXE))
        violations = page.evaluate(
            "async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa','wcag22aa']}})).violations"
        )
        if violations:
            findings.append((name, [(v["id"], [n["target"] for n in v["nodes"]]) for v in violations]))
        assert not errors, (name, errors)
    context.close()
    (ARTIFACTS / f"findings-{theme}-{size[0]}.json").write_text(json.dumps(findings, indent=2))
    assert not findings, findings


def test_rule_preview_changes_at_the_step_boundary_and_checks_language_floor(browser, management_pages):
    pages, paths, i18n, objects = management_pages
    context, page, errors = open_page(browser, pages, i18n)
    page.goto("https://desk.test" + paths["rules"])
    page.select_option("#rule-test-agent", str(objects["agent"].uid))
    page.select_option("#rule-test-language", "swahili")
    page.fill("#rule-test-wait", "29")
    expect(page.locator("#rule-result")).to_contain_text("Does not match")
    page.fill("#rule-test-wait", "30")
    expect(page.locator("#rule-result")).to_contain_text("All required skills match")
    page.fill('[name="requirements-0-min_level"]', "3")
    expect(page.locator("#rule-validation")).to_contain_text("Every step needs")
    assert not errors, errors
    context.close()
