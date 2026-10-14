"""Read-side queries for roles and permissions (selectors never mutate)."""

from django.db.models import Count, Q, QuerySet
from django.utils import timezone

from apps.core.exceptions import NotFoundError
from apps.rbac import cache
from apps.rbac.models import Permission, PermissionGroup, Role, UserRole


def _live_grants() -> Q:
    """Grants that count right now: not deleted, not past their expiry."""
    return Q(permission_roles__role__role_users__deleted=False) & (
        Q(permission_roles__role__role_users__expires_at__isnull=True)
        | Q(permission_roles__role__role_users__expires_at__gt=timezone.now())
    )


def get_user_permission_codes(user) -> set[str]:
    """The permission codes a user holds through their roles.

    Effective permissions = union of the permissions on every live role grant.
    Cached per user; any change to roles, role permissions or grants
    invalidates the cache (see :mod:`apps.rbac.cache`).
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return set()

    cached = cache.get(user.pk)
    if cached is not None:
        return cached

    codes = set(
        Permission.objects.filter(
            _live_grants(),
            permission_roles__deleted=False,
            permission_roles__role__deleted=False,
            permission_roles__role__active=True,
            permission_roles__role__role_users__user=user,
        )
        .values_list("code", flat=True)
        .distinct()
    )
    cache.put(user.pk, codes)
    return codes


def user_has_permission(user, *codes: str) -> bool:
    """True if the user holds at least one of the given permission codes."""
    return bool(set(codes) & get_user_permission_codes(user))


def live_user_roles(user) -> QuerySet[UserRole]:
    return UserRole.objects.filter(user=user).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
    )


def get_user_roles(user) -> QuerySet[Role]:
    return Role.objects.filter(pk__in=live_user_roles(user).values_list("role_id", flat=True)).distinct()


def user_role_names(user) -> set[str]:
    return set(get_user_roles(user).values_list("name", flat=True))


def get_role(uid) -> Role:
    role = Role.objects.filter(uid=uid).first()
    if role is None:
        raise NotFoundError("Role not found")
    return role


def get_role_by_name(name: str) -> Role | None:
    return Role.objects.filter(name__iexact=(name or "").strip()).first()


def roles_queryset(filters: Q | None = None) -> QuerySet[Role]:
    qs = (
        Role.objects.all()
        .prefetch_related("role_permissions__permission")
        .annotate(users_count=Count("role_users", filter=Q(role_users__deleted=False), distinct=True))
    )
    return qs.filter(filters) if filters is not None else qs


def permission_groups_queryset() -> QuerySet[PermissionGroup]:
    return PermissionGroup.objects.all().prefetch_related("permissions").distinct()
