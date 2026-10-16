"""M4b on the web side: this centre's number plan, and which outbound route an outside number takes.

The rules themselves are pure and shared with the brain (``contracts.carriers``); this reads the
configuration they apply to. The agent page asks it before a transfer to an outside number, so a
number no route allows, or an agent without the route's permission, is refused at once with a
plain message. The brain checks the route again when it dials.
"""

from apps.core.hooks import policy
from apps.telephony.models import OutboundRoute, Trunk
from contracts.carriers import NumberPlan, OutboundRule, RouteConfig, dialled_to_e164, pick_route, to_e164


def centre_plan() -> NumberPlan:
    return NumberPlan(
        country_code=str(policy("NUMBER_COUNTRY_CODE", "255")),
        national_prefix=str(policy("NUMBER_NATIONAL_PREFIX", "0")),
        international_prefix=str(policy("NUMBER_INTERNATIONAL_PREFIX", "00")),
        national_length=int(policy("NUMBER_NATIONAL_LENGTH", 9)),
        premium_prefixes=tuple(policy("NUMBER_PREMIUM_PREFIXES", []) or ()),
    )


def outside_number(target: str) -> str | None:
    """The E.164 form of what an agent typed, or None for an internal number (a SIP id, an extension)."""
    plan = centre_plan()
    found = to_e164(target, plan)
    prefixes = (
        Trunk.objects.filter(active=True)
        .exclude(external_prefix="")
        .values_list("external_prefix", flat=True)
    )
    for prefix in prefixes:
        if found:
            break
        found = dialled_to_e164(target, OutboundRule(external_prefix=prefix), plan)
    return found


def route_for(number: str) -> OutboundRoute | None:
    """The active route that allows this E.164 number, or None."""
    routes = {route.pk: route for route in OutboundRoute.objects.filter(active=True)}
    configs = [
        RouteConfig(name=str(pk), patterns=tuple(route.pattern_list()), trunks=("-",))
        for pk, route in routes.items()
    ]
    picked = pick_route(number, configs, centre_plan())
    return routes[int(picked.name)] if picked else None
