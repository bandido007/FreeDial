"""M4b: the DID forms table [from-carrier] reads (``CarrierNumber``).

Every entry point whose number is E.164 is a DID. For each, every form a carrier may send it in (by
each live trunk's number plan) maps to it, so only known DIDs reach the brain, even when it is down.
Rebuilt whole after any change to an entry point or a trunk: a few rows per DID.
"""

from django.db import transaction

from apps.queues.models import CarrierNumber, EntryPoint
from apps.telephony.models import Trunk
from contracts.carriers import NumberPlan, carrier_forms


def plans() -> list[NumberPlan]:
    found = {
        NumberPlan(
            country_code=trunk.country_code,
            national_prefix=trunk.national_prefix,
            international_prefix=trunk.international_prefix,
            national_length=trunk.national_length,
        )
        for trunk in Trunk.objects.filter(active=True)
    }
    return sorted(found, key=lambda plan: plan.country_code) or [NumberPlan()]


@transaction.atomic
def rebuild() -> None:
    wanted: dict[str, str] = {}
    dids = EntryPoint.objects.filter(active=True, number__startswith="+").values_list("number", flat=True)
    for did in dids:
        for plan in plans():
            for form in carrier_forms(did, plan):
                wanted.setdefault(form, did)  # the first DID to claim a form keeps it
    CarrierNumber.objects.exclude(form__in=wanted).delete()
    have = dict(CarrierNumber.objects.values_list("form", "did"))
    for form, did in wanted.items():
        if have.get(form) != did:
            CarrierNumber.objects.update_or_create(form=form, defaults={"did": did})
