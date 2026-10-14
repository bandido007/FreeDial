""" "Give me my data": a portable snapshot of everything held about a person.

The snapshot is assembled from **sections**. ``users`` contributes the profile
and the consent history; every other app adds what it holds by registering a
section from its ``AppConfig.ready()``::

    from apps.users import data_export

    def _my_invoices(user) -> list[dict]:
        return [{"number": i.number, "total": str(i.total)} for i in user.invoices.all()]

    data_export.register_section("invoices", _my_invoices)

A section returns plain JSON-serialisable data. One failing section is reported
inside the export and never blocks the others — a person's right to their data
must not depend on every module being healthy.
"""

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

Section = Callable[[object], object]
_sections: dict[str, Section] = {}


def register_section(name: str, builder: Section) -> None:
    _sections[name] = builder


def registered_sections() -> list[str]:
    return sorted(_sections)


def _profile(user) -> dict:
    return {
        "uid": str(user.uid),
        "email": user.email,
        "full_name": user.get_full_name(),
        "first_name": user.first_name,
        "middle_name": user.middle_name,
        "last_name": user.last_name,
        "phone": user.phone,
        "gender": user.gender,
        "date_of_birth": str(user.date_of_birth) if user.date_of_birth else None,
        "account_type": user.account_type,
        "auth_provider": user.auth_provider,
        "account_status": user.account_status,
        "is_verified": user.is_verified,
        "phone_verified": user.phone_verified,
        "date_joined": str(user.date_joined),
        "last_login": str(user.last_login) if user.last_login else None,
    }


def _consents(user) -> list[dict]:
    return [
        {"document": consent.document, "version": consent.version, "accepted_at": str(consent.accepted_at)}
        for consent in user.consents.all()
    ]


def _roles(user) -> list[str]:
    return sorted(user.user_roles.values_list("role__name", flat=True))


register_section("profile", _profile)
register_section("consents", _consents)
register_section("roles", _roles)


def build(user) -> dict:
    """Every registered section for ``user``."""
    snapshot: dict = {}
    for name in registered_sections():
        try:
            snapshot[name] = _sections[name](user)
        except Exception:  # noqa: BLE001
            logger.exception("[data_export] section '%s' failed for user %s", name, user.pk)
            snapshot[name] = {"error": "This section could not be produced. Please contact support."}
    return snapshot
