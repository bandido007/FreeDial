"""Read-side queries for users (selectors never mutate)."""

from django.db.models import Q, QuerySet

from apps.core.enums import to_values
from apps.core.exceptions import NotFoundError
from apps.users.models import User


def get_user(uid, *, include_deleted: bool = False) -> User:
    """Fetch a single user by ``uid`` (raises :class:`NotFoundError`).

    ``include_deleted`` switches to ``all_objects`` so admin/restore flows can
    reach soft-deleted rows that the default manager hides.
    """
    manager = User.all_objects if include_deleted else User.objects
    user = manager.filter(uid=uid).first()
    if user is None:
        raise NotFoundError("User not found")
    return user


def get_user_by_email(email: str, *, include_deleted: bool = False) -> User | None:
    manager = User.all_objects if include_deleted else User.objects
    return manager.filter(email__iexact=(email or "").strip()).first()


def users_queryset(filters: Q | None = None, *, include_deleted: bool = False) -> QuerySet[User]:
    """Base user listing queryset with the joins the user types need.

    ``include_deleted`` reads through ``all_objects`` (soft-deleted rows visible)
    — only used when an authorised caller explicitly asks for deleted accounts.
    """
    manager = User.all_objects if include_deleted else User.objects
    qs = manager.all().prefetch_related("user_roles__role")
    if filters is not None:
        qs = qs.filter(filters)
    # A role filter joins user_roles, which can fan a user out into duplicate
    # rows; collapse them.
    return qs.distinct()


def visibility_scope_q(viewer) -> Q | None:
    """Row-level restriction on which users ``viewer`` may see.

    The starter has none: anyone holding ``can_view_users`` sees every account.
    **This is the place to add one** — return a ``Q`` to confine a viewer to
    their own branch, department or organisation, and ``None`` for no
    restriction. Both the listing and the single-user query honour it.
    """
    return None


def is_visible_to(viewer, user: User) -> bool:
    scope = visibility_scope_q(viewer)
    return scope is None or User.all_objects.filter(scope, pk=user.pk).exists()


def build_user_filters(filtering) -> Q:
    """Translate a ``UserFilterInput`` into a Django ``Q`` (structured filters).

    Free-text ``search`` matches across the human-identifying columns (email +
    names + phone) with OR; every other clause is ANDed on top. Enum fields are
    normalised to their raw values. Kept here (not in the resolver) so it is unit
    testable and the resolver stays thin.
    """
    filters = Q()
    if filtering is None:
        return filters

    if filtering.uid is not None:
        filters &= Q(uid=filtering.uid)
    if filtering.account_type is not None:
        filters &= Q(account_type=to_values(filtering.account_type))
    if filtering.auth_provider is not None:
        filters &= Q(auth_provider=to_values(filtering.auth_provider))
    if filtering.account_status is not None:
        filters &= Q(account_status=to_values(filtering.account_status))
    if filtering.gender is not None:
        filters &= Q(gender=to_values(filtering.gender))
    if filtering.role_uid is not None:
        filters &= Q(user_roles__role__uid=filtering.role_uid, user_roles__deleted=False)
    if filtering.is_active is not None:
        filters &= Q(is_active=filtering.is_active)
    if filtering.is_verified is not None:
        filters &= Q(is_verified=filtering.is_verified)
    if filtering.is_staff is not None:
        filters &= Q(is_staff=filtering.is_staff)
    if filtering.mfa_enabled is not None:
        filters &= Q(mfa_enabled=filtering.mfa_enabled)
    if filtering.invitation_pending is True:
        # Django marks an unusable password with a leading "!".
        filters &= Q(invited_at__isnull=False, password__startswith="!")  # noqa: S106
    # ``deleted=True`` is honoured only alongside the all_objects manager (the
    # resolver decides that); here we just narrow to deleted rows when asked.
    if filtering.deleted is True:
        filters &= Q(deleted=True)

    search = (filtering.search or "").strip()
    if search:
        filters &= (
            Q(email__icontains=search)
            | Q(first_name__icontains=search)
            | Q(middle_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(phone__icontains=search)
        )
    return filters
