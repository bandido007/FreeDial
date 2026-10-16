"""M4b, pure rules: numbers to and from E.164, which route an outside number takes, when to try the next
trunk, and how many channels a trunk has in use. No Asterisk, no database."""

import pytest

from contracts.carriers import (
    ChannelCount,
    NumberPlan,
    OutboundRule,
    RouteConfig,
    carrier_forms,
    dialled_to_e164,
    fail_over,
    for_trunk,
    pick_route,
    to_e164,
)

TZ = NumberPlan(country_code="255", national_prefix="0", international_prefix="00", national_length=9)


# ── numbers in: what a carrier sends, to E.164 ──────────────────────────────
@pytest.mark.parametrize(
    "raw",
    ["+255222123456", "00255222123456", "255222123456", "0222123456", "222123456", " +255 22 212-3456 "],
)
def test_every_form_of_a_tanzanian_number_becomes_one_e164(raw):
    assert to_e164(raw, TZ) == "+255222123456"


@pytest.mark.parametrize("raw", ["+254712345678", "00254712345678"])
def test_a_foreign_number_keeps_its_own_country(raw):
    assert to_e164(raw, TZ) == "+254712345678"


@pytest.mark.parametrize("raw", ["", "abc", "12", "02221234567890", "+12", "025512345"])
def test_what_is_not_a_number_is_none(raw):
    assert to_e164(raw, TZ) is None


def test_the_forms_a_carrier_may_send_for_a_did():
    """Written to the table [from-carrier] reads, so a known DID passes even with the brain down."""
    assert carrier_forms("+255222123456", TZ) == [
        "+255222123456",
        "00255222123456",
        "255222123456",
        "0222123456",
    ]
    for form in carrier_forms("+255222123456", TZ):
        assert to_e164(form, TZ) == "+255222123456", "each form comes back to the same DID"
    assert carrier_forms("+254712345678", TZ) == ["+254712345678", "00254712345678", "254712345678"]


# ── numbers out: what the agent typed, to what the trunk wants ───────────────
def test_the_agent_may_type_an_outside_line_prefix():
    rule = OutboundRule(external_prefix="9")
    assert dialled_to_e164("90712345678", rule, TZ) == "+255712345678"
    assert dialled_to_e164("0712345678", rule, TZ) == "+255712345678"
    assert dialled_to_e164("+255712345678", rule, TZ) == "+255712345678"


@pytest.mark.parametrize(
    ("rule", "sent"),
    [
        (OutboundRule(), "+255712345678"),  # the carrier takes E.164 as it is
        (OutboundRule(strip_digits=4, prefix="0"), "0712345678"),  # national: "+255" off, "0" on
        (OutboundRule(strip_digits=1), "255712345678"),  # international without the plus
    ],
)
def test_each_trunk_gets_the_format_it_wants(rule, sent):
    assert for_trunk("+255712345678", rule) == sent


# ── routes: an allow list; international and premium blocked by default ─────
MOBILE = RouteConfig(name="Mobiles", patterns=("+2556", "+2557"), trunks=("vodacom", "airtel"))
LANDLINE = RouteConfig(name="Landlines", patterns=("+25522", "+25523"), trunks=("ttcl",))
EVERYTHING_LOCAL = RouteConfig(name="Tanzania", patterns=("+255",), trunks=("ttcl",))
KENYA = RouteConfig(name="Kenya", patterns=("+254",), trunks=("safaricom",))
PREMIUM = RouteConfig(name="Premium (allowed on purpose)", patterns=("+2559",), trunks=("ttcl",))


def test_the_most_specific_pattern_wins():
    assert pick_route("+255712345678", [EVERYTHING_LOCAL, MOBILE], TZ) is MOBILE
    assert pick_route("+255222123456", [EVERYTHING_LOCAL, MOBILE, LANDLINE], TZ) is LANDLINE


def test_international_is_blocked_unless_a_route_names_that_country():
    assert pick_route("+254712345678", [EVERYTHING_LOCAL, MOBILE], TZ) is None
    assert pick_route("+254712345678", [EVERYTHING_LOCAL, KENYA], TZ) is KENYA


def test_premium_is_blocked_even_by_a_broad_route():
    plan = NumberPlan(country_code="255", national_length=9, premium_prefixes=("+2559",))
    assert pick_route("+255900123456", [EVERYTHING_LOCAL], plan) is None
    assert pick_route("+255900123456", [EVERYTHING_LOCAL, PREMIUM], plan) is PREMIUM


def test_an_inactive_route_is_never_taken():
    off = RouteConfig(name="Off", patterns=("+2557",), trunks=("x",), active=False)
    assert pick_route("+255712345678", [off], TZ) is None


# ── failover: only on carrier or network failures ───────────────────────────
@pytest.mark.parametrize("cause", [17, 19, 21, 16, 1, 28])  # measured: 486, 480, 603, 404
def test_the_person_or_the_number_decided_so_no_other_trunk_is_tried(cause):
    """Busy, no answer, rejected, a normal hangup, an unallocated or malformed number: another carrier
    would get the same answer."""
    assert not fail_over(cause)


@pytest.mark.parametrize("cause", [18, 27, 34, 38, 41, 42, 47, 102])
def test_the_carrier_or_the_network_failed_so_the_next_trunk_is_tried(cause):
    assert fail_over(cause)


def test_a_trunk_asterisk_cannot_reach_is_a_carrier_failure():
    assert fail_over(None, originate_failed=True)


# ── channels: calls in and out count against a trunk's limit ────────────────
def test_calls_in_and_out_share_the_trunks_channels():
    count = ChannelCount()
    count.take("vodacom", "in-1")
    count.take("vodacom", "out-1")
    assert count.in_use("vodacom") == 2
    assert not count.has_room("vodacom", 2) and count.has_room("vodacom", 3)
    assert count.has_room("vodacom", 0), "0 = no limit"
    count.release("in-1")
    count.release("in-1")  # twice changes nothing (rule 12)
    assert count.in_use("vodacom") == 1 and count.has_room("vodacom", 2)
