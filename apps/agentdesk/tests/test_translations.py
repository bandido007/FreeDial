"""Every label the agent and supervisor pages show exists in English and Swahili, with the same
{placeholders} (a missing one would show "{name}" to an agent). UI_DESIGN.md 2.7, UX_PRINCIPLES.md 8."""

import json
import re
from pathlib import Path

import pytest

from apps.agentdesk.views import page_messages as agent_messages
from apps.supervisor.views import page_messages as supervisor_messages

ROOT = Path(__file__).resolve().parents[3]
PAGES = {
    "agentdesk": agent_messages,
    "supervisor": supervisor_messages,
}


def placeholders(text):
    return sorted(re.findall(r"\{(\w+)\}", text))


@pytest.mark.parametrize("app", sorted(PAGES))
def test_swahili_has_every_label_with_the_same_placeholders(app):
    messages = PAGES[app]()
    english, swahili = messages["en"], messages["sw"]
    missing = sorted(set(english) - set(swahili))
    assert not missing, f"{app}: no Swahili for {missing}"
    for key, text in english.items():
        assert swahili[key].strip(), f"{app}: empty Swahili for {key!r}"
        assert placeholders(swahili[key]) == placeholders(text), f"{app}: placeholders differ for {key!r}"
    extra = sorted(set(swahili) - set(english) - {"_status"})
    assert not extra, f"{app}: Swahili labels nothing uses: {extra}"


@pytest.mark.parametrize("app", sorted(PAGES))
def test_every_label_the_page_uses_is_in_the_english_file(app):
    """t("...") calls and data-i18n attributes: a label missing here would never be translated."""
    code = (ROOT / f"apps/{app}/static/{app}/app.js").read_text()
    markup = (ROOT / f"apps/{app}/templates/{app}/index.html").read_text()
    used = set(re.findall(r'\bt\(\s*"((?:[^"\\]|\\.)*)"', code))
    used |= set(re.findall(r'data-(?:i18n|placeholder)="([^"]*)"', markup))
    english = json.loads((ROOT / f"apps/{app}/translations/en.json").read_text())
    assert not sorted(used - set(english)), f"{app}: not in en.json: {sorted(used - set(english))}"
