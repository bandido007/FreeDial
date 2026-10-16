"""M4b: the rules for calls through carriers (trunks), as pure functions shared by the web (the DID
forms table, the route check before a transfer) and the brain (dialling out, failover, channels).

- Numbers in: whatever form a carrier sends (+255…, 00255…, 255…, 0…) becomes one E.164 number, by
  the trunk's ``NumberPlan``; ``carrier_forms`` lists those forms for a DID, so the dialplan can let
  known DIDs through even when the brain is down (POC_PLAN.md M4b, decision B).
- Numbers out: what an agent typed becomes E.164, then the format the trunk wants (``OutboundRule``).
- Routes: an allow list of E.164 prefixes; the longest match wins. A foreign number needs a route
  naming its country, a premium number a route naming the premium prefix.
- Failover: only when the carrier or the network failed, never when the person or the number decided.
- Channels: calls in and out count against a trunk's ``max_channels``.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

DIGITS = re.compile(r"^\d+$")
E164_MIN, E164_MAX = 7, 15  # digits after the "+"


@dataclass(frozen=True)
class NumberPlan:
    """How numbers are written where a trunk's carrier is (Tanzania by default)."""

    country_code: str = "255"
    national_prefix: str = "0"  # 0222123456
    international_prefix: str = "00"  # 00255222123456
    national_length: int = 9  # digits after the country code
    premium_prefixes: tuple[str, ...] = ()  # E.164 prefixes no broad route may reach, e.g. +2559


@dataclass(frozen=True)
class OutboundRule:
    """What a trunk's carrier wants to receive, from an E.164 number."""

    strip_digits: int = 0  # characters taken off the front of E.164 ("+255" is 4)
    prefix: str = ""  # then put in front ("0" for national)
    external_prefix: str = ""  # what agents may type first for an outside line ("9"), removed


@dataclass(frozen=True)
class RouteConfig:
    name: str
    patterns: tuple[str, ...]  # E.164 prefixes allowed, e.g. +2557
    trunks: tuple[str, ...]  # trunk names, in the order they are tried
    caller_id: str = ""
    active: bool = True


def _clean(raw: str) -> str:
    return re.sub(r"[\s\-().]", "", raw or "")


def to_e164(raw: str, plan: NumberPlan) -> str | None:
    """The E.164 form of a number written any way the plan's country writes it; None if it is not one."""
    number = _clean(raw)
    if number.startswith("+"):
        rest = number[1:]
    elif not DIGITS.match(number):
        return None
    elif plan.international_prefix and number.startswith(plan.international_prefix):
        rest = number[len(plan.international_prefix) :]
    elif plan.national_prefix and number.startswith(plan.national_prefix):
        national = number[len(plan.national_prefix) :]
        if len(national) != plan.national_length:
            return None
        rest = plan.country_code + national
    elif (
        number.startswith(plan.country_code) and len(number) - len(plan.country_code) == plan.national_length
    ):
        rest = number
    elif len(number) == plan.national_length:
        rest = plan.country_code + number  # a subscriber number without its prefix
    else:
        return None
    return f"+{rest}" if DIGITS.match(rest) and E164_MIN <= len(rest) <= E164_MAX else None


def carrier_forms(e164: str, plan: NumberPlan) -> list[str]:
    """Every form a carrier may send for this number: E.164, international, without the plus, national."""
    rest = e164.removeprefix("+")
    forms = [e164]
    if plan.international_prefix:
        forms.append(plan.international_prefix + rest)
    forms.append(rest)
    if rest.startswith(plan.country_code) and plan.national_prefix:
        forms.append(plan.national_prefix + rest[len(plan.country_code) :])
    return forms


def dialled_to_e164(raw: str, rule: OutboundRule, plan: NumberPlan) -> str | None:
    """What an agent typed (maybe after the outside-line prefix), as E.164."""
    number = _clean(raw)
    if rule.external_prefix and number.startswith(rule.external_prefix) and not number.startswith("+"):
        stripped = to_e164(number[len(rule.external_prefix) :], plan)
        if stripped:
            return stripped
    return to_e164(number, plan)


def for_trunk(e164: str, rule: OutboundRule) -> str:
    """The number as this trunk's carrier wants it."""
    return rule.prefix + e164[rule.strip_digits :]


def pick_route(e164: str, routes: Sequence[RouteConfig], plan: NumberPlan) -> RouteConfig | None:
    """The active route whose pattern matches the most digits; None: the number is not allowed."""
    premium = next((p for p in plan.premium_prefixes if e164.startswith(p)), None)
    best: tuple[int, RouteConfig] | None = None
    for route in routes:
        if not route.active:
            continue
        for pattern in route.patterns:
            if not e164.startswith(pattern):
                continue
            if premium and not pattern.startswith(premium):
                continue  # a premium number needs a route that names the premium prefix
            if best is None or len(pattern) > best[0]:
                best = (len(pattern), route)
    return best[1] if best else None


# Hangup causes (Q.850, as Asterisk reports them) that mean the carrier or the network failed, so
# another trunk may succeed. Busy (17), no answer (19), rejected (21), a normal hangup (16) and a
# wrong number (1, 28) are the callee's or the number's answer: every carrier would give the same.
CARRIER_FAILURES = frozenset(
    {
        34,  # no circuit/channel available (SIP 503)
        38,  # network out of order (SIP 502)
        41,  # temporary failure
        42,  # switching equipment congestion
        47,  # resource unavailable
        102,  # recovery on timer expiry (no response: SIP 408)
    }
)


def fail_over(cause: int | None, *, originate_failed: bool = False) -> bool:
    """Try the next trunk? Yes when Asterisk could not reach the trunk at all, or the carrier failed."""
    return originate_failed or (cause is not None and cause in CARRIER_FAILURES)


class ChannelCount:
    """Channels in use per trunk: each call leg in or out takes one until it is released."""

    def __init__(self) -> None:
        self._trunk_of: dict[str, str] = {}  # leg -> trunk

    def take(self, trunk: str, leg: str) -> None:
        self._trunk_of[leg] = trunk

    def release(self, leg: str) -> None:
        self._trunk_of.pop(leg, None)

    def in_use(self, trunk: str) -> int:
        return sum(1 for owner in self._trunk_of.values() if owner == trunk)

    def has_room(self, trunk: str, max_channels: int) -> bool:
        return max_channels <= 0 or self.in_use(trunk) < max_channels
