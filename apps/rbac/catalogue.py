"""The permission catalogue, assembled from every installed app.

:func:`collect` reads each app's ``permissions.py`` (``GROUPS`` and ``ROLES``,
see :mod:`apps.core.catalogue`); :func:`problems` checks the result the way a
reviewer would. ``manage.py sync_rbac`` writes it to the database, and the
guard tests run :func:`problems` so a badly written permission fails CI.
"""

import re
from dataclasses import dataclass, field

from apps.core.catalogue import LEVELS, Group, Perm, RoleSpec
from apps.core.registry import app_modules

MODULE = "permissions"
MAX_NAME_LENGTH = 45
MIN_DESCRIPTION_LENGTH = 25
_CODE_RE = re.compile(r"^can_[a-z][a-z0-9_]*$")


@dataclass
class Catalogue:
    groups: list[tuple[str, Group]] = field(default_factory=list)  # (app_label, group)
    roles: dict[str, RoleSpec] = field(default_factory=dict)  # merged by name

    def permissions(self) -> list[tuple[str, Group, Perm]]:
        return [(label, group, perm) for label, group in self.groups for perm in group.permissions]

    def codes(self) -> set[str]:
        return {perm.code for _label, _group, perm in self.permissions()}


def collect() -> Catalogue:
    catalogue = Catalogue()
    for app_config, module in app_modules(MODULE):
        for group in getattr(module, "GROUPS", []) or []:
            catalogue.groups.append((app_config.label, group))
        for spec in getattr(module, "ROLES", []) or []:
            known = catalogue.roles.get(spec.name)
            if known is None:
                catalogue.roles[spec.name] = spec
                continue
            # Several apps may add to one role: merge, keeping first-seen order.
            merged = list(dict.fromkeys([*known.permissions, *spec.permissions]))
            catalogue.roles[spec.name] = RoleSpec(
                name=known.name,
                description=known.description or spec.description,
                permissions=merged,
                locked=known.locked or spec.locked,
            )
    return catalogue


def problems(catalogue: Catalogue | None = None) -> list[str]:
    """Everything wrong with the catalogue, as sentences a developer can act on."""
    catalogue = catalogue or collect()
    found: list[str] = []
    owners: dict[str, str] = {}
    group_names: dict[str, str] = {}

    for label, group in catalogue.groups:
        if group.name in group_names and group_names[group.name] != label:
            found.append(
                f"Group '{group.name}' is declared by both '{group_names[group.name]}' and '{label}'."
            )
        group_names.setdefault(group.name, label)
        if len((group.description or "").strip()) < 10:
            found.append(f"Group '{group.name}' ({label}) needs a one-line description.")

        for perm in group.permissions:
            where = f"{perm.code} ({label})"
            if not _CODE_RE.match(perm.code):
                found.append(f"{where}: the code must look like can_<verb>_<thing>, lower case.")
            if perm.code in owners:
                found.append(f"{where}: already declared by '{owners[perm.code]}'.")
            owners.setdefault(perm.code, label)

            name = (perm.name or "").strip()
            if not name:
                found.append(f"{where}: the name is missing.")
            elif len(name) > MAX_NAME_LENGTH:
                found.append(f"{where}: the name is longer than {MAX_NAME_LENGTH} characters.")
            if "/" in name:
                found.append(f"{where}: the name must not contain a slash — pick one verb.")
            if name == perm.code or name.startswith("can_"):
                found.append(f"{where}: the name repeats the code; write a verb phrase a person reads.")

            description = (perm.description or "").strip()
            if len(description) < MIN_DESCRIPTION_LENGTH:
                found.append(f"{where}: the description is too short to assign the permission confidently.")
            elif not description.endswith((".", "!", "?")):
                found.append(f"{where}: the description must be a full sentence.")
            if perm.code.startswith("can_view_") and "read-only" not in description.lower():
                found.append(f"{where}: a can_view_ permission must say 'Read-only.'")
            if perm.level and perm.level not in LEVELS:
                found.append(f"{where}: unknown level '{perm.level}'.")

    known_codes = set(owners)
    for spec in catalogue.roles.values():
        unknown = [code for code in spec.permissions if code not in known_codes]
        if unknown:
            found.append(f"Role '{spec.name}' lists permissions nobody declares: {', '.join(unknown)}.")
    return found
