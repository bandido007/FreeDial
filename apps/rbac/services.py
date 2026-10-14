"""Write-side logic for roles, grants and the catalogue sync."""

import logging

from django.db import transaction
from django.utils import timezone

from apps.core.exceptions import DuplicateError, NotFoundError, ValidationError
from apps.rbac import cache, catalogue, selectors
from apps.rbac.models import Permission, PermissionGroup, Role, RolePermission, UserRole
from apps.rbac.permissions import SYSTEM_ROLE_NAME

logger = logging.getLogger(__name__)

SYSTEM_ROLE_LOCKED = 9140
ROLE_IN_USE = 9141
OWN_ADMIN_ROLE = 9142


# ── Roles ────────────────────────────────────────────────────────────────────
def create_role(*, name: str, description: str = "") -> Role:
    name = (name or "").strip()
    if not name:
        raise ValidationError("Role name is required", code=9005)
    if Role.objects.filter(name__iexact=name).exists():
        raise DuplicateError("A role with this name already exists")
    return Role.objects.create(name=name, description=description or "")


def update_role(*, uid, name: str | None = None, description: str | None = None) -> Role:
    role = selectors.get_role(uid)
    if role.is_system:
        raise ValidationError("System roles cannot be modified", code=SYSTEM_ROLE_LOCKED)

    if name is not None:
        name = name.strip()
        if not name:
            raise ValidationError("Role name cannot be empty", code=9005)
        if Role.objects.filter(name__iexact=name).exclude(pk=role.pk).exists():
            raise DuplicateError("A role with this name already exists")
        role.name = name
    if description is not None:
        role.description = description

    role.save()
    return role


def delete_role(*, uid) -> Role:
    role = selectors.get_role(uid)
    if role.is_system:
        raise ValidationError("System roles cannot be deleted", code=SYSTEM_ROLE_LOCKED)
    if role.role_users.filter(deleted=False).exists():
        raise ValidationError("Role is assigned to one or more users", code=ROLE_IN_USE)

    role.delete()  # soft delete
    return role


@transaction.atomic
def assign_permissions_to_role(*, role_uid, permission_uids: list) -> Role:
    """Replace a role's permission set with the supplied permissions (atomic)."""
    role = selectors.get_role(role_uid)
    if role.is_system:
        raise ValidationError(
            "System roles always hold every permission and cannot be edited.", code=SYSTEM_ROLE_LOCKED
        )

    wanted = list(dict.fromkeys(permission_uids or []))
    permissions = list(Permission.objects.filter(uid__in=wanted))
    if len(permissions) != len(wanted):
        raise NotFoundError("One or more of the supplied permissions were not found")

    _replace_permissions(role, permissions)
    logger.info("[assign_permissions_to_role] role=%s count=%s", role.name, len(permissions))
    return role


def _replace_permissions(role: Role, permissions) -> None:
    RolePermission.all_objects.filter(role=role).delete()
    RolePermission.objects.bulk_create(
        [RolePermission(role=role, permission=permission) for permission in permissions],
        ignore_conflicts=True,
    )
    cache.invalidate_all()  # bulk writes send no signals


def _grant_permissions(role: Role, permissions) -> int:
    """Add permissions to a role, leaving what it already holds untouched."""
    held = set(RolePermission.objects.filter(role=role).values_list("permission_id", flat=True))
    missing = [permission for permission in permissions if permission.pk not in held]
    RolePermission.objects.bulk_create(
        [RolePermission(role=role, permission=permission) for permission in missing],
        ignore_conflicts=True,
    )
    if missing:
        cache.invalidate_all()
    return len(missing)


# ── Grants ───────────────────────────────────────────────────────────────────
def grant_role(
    user, role: Role, *, source: str = UserRole.SOURCE_MANUAL, source_ref="", expires_at=None
) -> UserRole:
    """Give ``user`` a role. Idempotent: an existing live grant is returned as is."""
    grant, created = UserRole.objects.get_or_create(
        user=user,
        role=role,
        defaults={"source": source, "source_ref": str(source_ref or ""), "expires_at": expires_at},
    )
    if not created and expires_at != grant.expires_at and source == grant.source:
        grant.expires_at = expires_at
        grant.save(update_fields=["expires_at"])
    return grant


def revoke_role(user, role: Role) -> int:
    grants = UserRole.objects.filter(user=user, role=role)
    count = grants.count()
    for grant in grants:
        grant.delete()  # soft delete; sends signals → cache invalidated
    return count


def revoke_grants_from(*, source: str, source_ref="", user=None) -> int:
    """Retire every grant a given source made (optionally for one user)."""
    grants = UserRole.objects.filter(source=source)
    if source_ref:
        grants = grants.filter(source_ref=str(source_ref))
    if user is not None:
        grants = grants.filter(user=user)
    count = grants.update(deleted=True, deleted_at=timezone.now(), active=False)
    if count:
        cache.invalidate_all()
    return count


@transaction.atomic
def set_user_roles(*, user, role_uids: list, actor=None) -> list[Role]:
    """Set a user's roles to exactly the supplied set.

    An administrator cannot take the system role away from themselves — that
    is how a system ends up with nobody able to manage it.
    """
    role_uids = list(dict.fromkeys(role_uids or []))
    roles = list(Role.objects.filter(uid__in=role_uids))
    if len(roles) != len(role_uids):
        raise NotFoundError("One or more of the supplied roles were not found")

    target_ids = {role.id for role in roles}
    current = UserRole.objects.filter(user=user)
    current_ids = set(current.values_list("role_id", flat=True))

    if actor is not None and actor.pk == user.pk:
        system_role = selectors.get_role_by_name(SYSTEM_ROLE_NAME)
        if system_role and system_role.id in current_ids and system_role.id not in target_ids:
            raise ValidationError("You cannot remove your own administrator role.", code=OWN_ADMIN_ROLE)

    revoked = current.exclude(role_id__in=target_ids)
    revoked_names = list(revoked.values_list("role__name", flat=True))
    revoked.update(deleted=True, deleted_at=timezone.now(), active=False)
    for role in roles:
        if role.id not in current_ids:
            grant_role(user, role)
    cache.invalidate_all()

    logger.info(
        "[set_user_roles] user=%s -> %s (revoked %s) by %s",
        user.pk,
        [role.name for role in roles],
        revoked_names,
        getattr(actor, "pk", None),
    )
    return roles


def retire_expired_grants() -> int:
    """Soft-delete grants whose expiry has passed (they already stopped counting)."""
    expired = UserRole.objects.filter(expires_at__isnull=False, expires_at__lte=timezone.now())
    count = expired.update(deleted=True, deleted_at=timezone.now(), active=False)
    if count:
        cache.invalidate_all()
    logger.info("[retire_expired_grants] retired %s grant(s)", count)
    return count


# ── Catalogue sync ───────────────────────────────────────────────────────────
@transaction.atomic
def sync_catalogue(*, log=None) -> dict:
    """Write the declared catalogue to the database. Safe to run on every deploy.

    * Groups and permissions are created or updated from the declarations.
    * A permission no app declares any more is retired and removed from roles.
    * The system role is given every permission.
    * A declared role receives its permissions when the role is first created,
      plus any permission that is **new in this run**. Permissions an
      administrator removed from a role are not put back.
    """
    log = log or (lambda message: None)
    declared = catalogue.collect()
    issues = catalogue.problems(declared)
    if issues:
        raise ValidationError("The permission catalogue is invalid:\n- " + "\n- ".join(issues), code=9002)

    by_code: dict[str, Permission] = {}
    new_codes: set[str] = set()
    for label, group_spec in declared.groups:
        group, _ = PermissionGroup.all_objects.update_or_create(
            name=group_spec.name,
            defaults={
                "description": group_spec.description,
                "app_label": label,
                "deleted": False,
                "deleted_at": None,
                "active": True,
            },
        )
        for spec in group_spec.permissions:
            existing = Permission.all_objects.filter(code=spec.code).first()
            was_live = existing is not None and not existing.deleted
            permission, _ = Permission.all_objects.update_or_create(
                code=spec.code,
                defaults={
                    "name": spec.name,
                    "description": spec.description,
                    "level": spec.access_level,
                    "group": group,
                    "deleted": False,
                    "deleted_at": None,
                    "active": True,
                },
            )
            by_code[spec.code] = permission
            if not was_live:
                new_codes.add(spec.code)
    log(f"  • {len(by_code)} permissions in {len(declared.groups)} groups ({len(new_codes)} new)")

    retired = Permission.objects.exclude(code__in=by_code.keys())
    retired_codes = list(retired.values_list("code", flat=True))
    if retired_codes:
        RolePermission.all_objects.filter(permission__in=retired).delete()
        retired.update(deleted=True, deleted_at=timezone.now(), active=False)
        log(f"  • retired {len(retired_codes)} permission(s): {', '.join(sorted(retired_codes))}")
    PermissionGroup.objects.exclude(name__in=[group.name for _l, group in declared.groups]).filter(
        permissions__isnull=True
    ).update(deleted=True, deleted_at=timezone.now(), active=False)

    system_role, _ = Role.all_objects.update_or_create(
        name=SYSTEM_ROLE_NAME,
        defaults={
            "description": "Full access to every part of the system.",
            "is_system": True,
            "deleted": False,
            "deleted_at": None,
            "active": True,
        },
    )
    _grant_permissions(system_role, by_code.values())
    log(f"  • '{system_role.name}' holds {system_role.role_permissions.count()} permissions")

    for role_spec in declared.roles.values():
        if role_spec.name == SYSTEM_ROLE_NAME:
            continue
        known_role = Role.objects.filter(name__iexact=role_spec.name).first()
        created = known_role is None
        if known_role is None:
            role = Role.objects.create(
                name=role_spec.name, description=role_spec.description, is_system=role_spec.locked
            )
            codes = role_spec.permissions
        else:
            role = known_role
            codes = [code for code in role_spec.permissions if code in new_codes]
            if role_spec.locked and not role.is_system:
                role.is_system = True
                role.save(update_fields=["is_system"])
        added = _grant_permissions(role, [by_code[code] for code in codes])
        log(f"  • role '{role.name}' {'created' if created else 'kept'} (+{added} permission(s))")

    cache.invalidate_all()
    return {
        "permissions": len(by_code),
        "new": sorted(new_codes),
        "retired": sorted(retired_codes),
        "roles": len(declared.roles) + 1,
    }


def ensure_bootstrap_admin(*, email, password, first_name="System", last_name="Administrator", log=None):
    """Create the first administrator (once) and give them the system role."""
    from django.contrib.auth import get_user_model

    log = log or (lambda message: None)
    if not email or not password:
        log("  • bootstrap admin skipped (set DEFAULT_ADMIN_EMAIL and DEFAULT_ADMIN_PASSWORD)")
        return None

    User = get_user_model()
    manager = getattr(User, "all_objects", User._default_manager)
    user = manager.filter(email__iexact=email).first()
    created = user is None
    if created:
        user = User.objects.create_superuser(
            email=email, password=password, first_name=first_name, last_name=last_name
        )
    system_role = selectors.get_role_by_name(SYSTEM_ROLE_NAME)
    if system_role is not None:
        grant_role(user, system_role, source="bootstrap")
    log(f"  • bootstrap admin {email} ({'created' if created else 'exists'})")
    return user
