"""The agent page when the admin gave the agent a desk phone (Agent -> Phone, e.g. 1001).

Calls ring the desk phone: the agent picks it up, hangs up, mutes and dials on it. The page still
shows the offer with the caller's context, and Hold, Transfer, wrap-up and Ready / Not Ready work as
always, because those go through the brain. The page never asks for the microphone.
"""

import pytest
from playwright.sync_api import expect

from apps.agentdesk.tests.test_browser import browser, check_view, open_desk  # noqa: F401

ONLINE = "window.__deskPhone = {sipId: '1001', state: 'online'};"
OFFLINE = "window.__deskPhone = {sipId: '1001', state: 'offline'};"


@pytest.fixture
def desk_page(browser):  # noqa: F811
    errors = []
    context, page = open_desk(browser, errors, init_script=ONLINE)
    yield page
    assert not errors, errors
    context.close()


def test_sign_in_uses_the_desk_phone_and_never_the_microphone(desk_page):
    expect(desk_page.locator("#phone-chip")).to_have_text("Desk phone connected")
    expect(desk_page.locator("#phone-chip")).to_have_attribute("data-sip-id", "1001")
    assert desk_page.evaluate("__deskTest.micAsked") == 0
    assert desk_page.evaluate("__deskTest.ua === undefined"), "no browser phone is started"
    desk_page.click("#state-menu-toggle")
    expect(desk_page.locator("#state-title")).to_have_text("Ready")


def test_a_call_is_answered_and_ended_on_the_phone(desk_page):
    desk_page.evaluate("__deskTest.ready(); __deskTest.deskOffer()")
    expect(desk_page.locator("#incoming-offer")).to_be_visible()
    expect(desk_page.locator("#offer-hint")).to_have_text("Pick up phone 1001 to answer.")
    expect(desk_page.locator("#answer")).to_be_hidden()
    expect(desk_page.locator("#reject")).to_be_hidden()
    expect(desk_page.locator("#offer-summary")).to_contain_text("claim was rejected")

    desk_page.evaluate("__deskTest.pickUp()")
    expect(desk_page.locator("#state-title")).to_have_text("On a call")
    expect(desk_page.locator("#phone-hint")).to_have_text("Hang up, mute and send digits on phone 1001.")
    for gone in ("#mute", "#keypad-toggle", "#hangup"):
        expect(desk_page.locator(gone)).to_be_hidden()
    expect(desk_page.locator("#hold")).to_be_enabled()
    check_view(desk_page)
    desk_page.click("#hold")
    expect(desk_page.locator("#state-title")).to_have_text("On hold")
    desk_page.click("#resume")

    desk_page.evaluate("__deskTest.hangUpOnPhone()")
    expect(desk_page.locator("#state-title")).to_have_text("Wrap-up")
    expect(desk_page.locator("#wrapup")).to_be_visible()


def test_the_hints_are_in_swahili_too(desk_page):
    desk_page.click("[data-language=sw]")
    desk_page.evaluate("__deskTest.ready(); __deskTest.deskOffer()")
    expect(desk_page.locator("#offer-hint")).to_have_text("Inua simu 1001 ili upokee.")
    expect(desk_page.locator("#phone-chip")).to_have_text("Simu ya mezani imeunganishwa")


def test_an_unregistered_desk_phone_blocks_ready_until_it_is_back(browser):  # noqa: F811
    errors = []
    context, page = open_desk(browser, errors, init_script=OFFLINE)
    expect(page.locator("#state-title")).to_have_text("Phone not connected")
    expect(page.locator("#activity")).to_contain_text("Phone 1001 is not connected")
    expect(page.locator("#ready")).to_be_disabled()
    page.evaluate("window.__deskPhone.state = 'online'")
    page.click("#reconnect")
    expect(page.locator("#phone-chip")).to_have_text("Desk phone connected")
    expect(page.locator("#state-title")).not_to_have_text("Phone not connected")
    assert not errors, errors
    context.close()


def test_the_page_notices_the_phone_coming_and_going_by_itself(browser):  # noqa: F811
    """No Reconnect needed: a phone switched on later connects; one switched off pauses the calls."""
    errors = []
    context, page = open_desk(browser, errors, init_script=OFFLINE, config={"deskPhoneCheckMs": 150})
    expect(page.locator("#state-title")).to_have_text("Phone not connected")
    page.evaluate("window.__deskPhone.state = 'online'")
    expect(page.locator("#phone-chip")).to_have_text("Desk phone connected")
    page.click("#state-menu-toggle")
    expect(page.locator("#state-title")).to_have_text("Ready")

    page.evaluate("window.__deskPhone.state = 'offline'")
    expect(page.locator("#state-title")).to_have_text("Phone not connected", timeout=3000)
    expect(page.locator("#availability")).to_have_text("Not Ready")
    assert page.evaluate("__deskTest.commands.some(c => c.query.includes('Phone not connected'))"), (
        "calls are paused at once, as when the browser phone drops"
    )
    page.evaluate("window.__deskPhone.state = 'unknown'")
    page.wait_for_timeout(600)
    expect(page.locator("#state-title")).to_have_text("Phone not connected"), "no answer changes nothing"
    assert not errors, errors
    context.close()
