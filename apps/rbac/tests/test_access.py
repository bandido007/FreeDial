"""Who may do what: resolving permissions, caching them, and managing roles."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.exceptions import DuplicateError, NotFoundError, ValidationError
from apps.rbac import cache, selectors, services
from apps.rbac.models import Permission, Role, UserRole
from apps.rbac.permissions import SYSTEM_ROLE_NAME

pytestmark = pytest.mark.django_db


def test_permissions_are_the_union_of_every_role(user, make_role):
    services.grant_role(user, make_role("Reader", ["can_view_users"]))
    services.grant_role(user, make_role("Editor", ["can_edit_user", "can_view_users"]))
    assert selectors.get_user_permission_codes(user) == {"can_view_users", "can_edit_user"}
    assert selectors.user_has_permission(user, "can_edit_user", "can_fly")
    assert selectors.user_role_names(user) == {"Reader", "Editor"}


def test_anonymous_holds_nothing():
    from django.contrib.auth.models import AnonymousUser

    assert selectors.get_user_permission_codes(AnonymousUser()) == set()
    assert selectors.get_user_permission_codes(None) == set()


def test_the_answer_is_cached_and_a_change_invalidates_it(user, make_role, django_assert_num_queries):
    role = make_role("Reader", ["can_view_users"])
    services.grant_role(user, role)
    assert selectors.get_user_permission_codes(user) == {"can_view_users"}

    with django_assert_num_queries(0):
        assert selectors.get_user_permission_codes(user) == {"can_view_users"}

    services.revoke_role(user, role)
    assert selectors.get_user_permission_codes(user) == set()


def test_changing_a_roles_permissions_reaches_its_holders_at_once(user, make_role):
    role = make_role("Reader", ["can_view_users"])
    services.grant_role(user, role)
    selectors.get_user_permission_codes(user)

    wanted = list(Permission.objects.filter(code__in=["can_view_roles"]).values_list("uid", flat=True))
    services.assign_permissions_to_role(role_uid=role.uid, permission_uids=wanted)
    assert selectors.get_user_permission_codes(user) == {"can_view_roles"}


def test_caching_can_be_switched_off(user, make_role, settings):
    settings.RBAC_PERMISSION_CACHE_SECONDS = 0
    services.grant_role(user, make_role("Reader", ["can_view_users"]))
    assert cache.get(user.pk) is None
    assert selectors.get_user_permission_codes(user) == {"can_view_users"}
    assert cache.get(user.pk) is None


def test_an_expired_grant_stops_counting_and_is_tidied_away(user, make_role):
    role = make_role("Temporary", ["can_view_users"])
    services.grant_role(user, role, expires_at=timezone.now() + timedelta(hours=1))
    assert selectors.get_user_permission_codes(user) == {"can_view_users"}

    UserRole.objects.filter(user=user).update(expires_at=timezone.now() - timedelta(minutes=1))
    cache.invalidate_all()
    assert selectors.get_user_permission_codes(user) == set()
    assert list(selectors.get_user_roles(user)) == []

    assert services.retire_expired_grants() == 1
    assert not UserRole.objects.filter(user=user).exists()


def test_a_deleted_or_inactive_role_grants_nothing(user, make_role):
    role = make_role("Reader", ["can_view_users"])
    services.grant_role(user, role)
    role.deactivate()
    assert selectors.get_user_permission_codes(user) == set()


def test_granting_twice_keeps_one_grant(user, make_role):
    role = make_role("Reader", ["can_view_users"])
    first = services.grant_role(user, role)
    second = services.grant_role(user, role)
    assert first.pk == second.pk
    assert UserRole.objects.filter(user=user, role=role).count() == 1


def test_grants_can_be_revoked_by_the_source_that_made_them(user, make_user, make_role):
    role = make_role("From directory", ["can_view_users"])
    other = make_user()
    services.grant_role(user, role, source="directory", source_ref="abc")
    services.grant_role(other, role, source="directory", source_ref="xyz")
    services.grant_role(user, make_role("By hand", ["can_view_roles"]))

    assert services.revoke_grants_from(source="directory", source_ref="abc") == 1
    assert selectors.user_role_names(user) == {"By hand"}
    assert selectors.user_role_names(other) == {"From directory"}
    assert services.revoke_grants_from(source="directory", user=other) == 1


def test_set_user_roles_replaces_the_set(user, make_role, admin):
    reader, editor = make_role("Reader", []), make_role("Editor", [])
    services.set_user_roles(user=user, role_uids=[reader.uid], actor=admin)
    services.set_user_roles(user=user, role_uids=[editor.uid, editor.uid], actor=admin)
    assert selectors.user_role_names(user) == {"Editor"}
    with pytest.raises(NotFoundError):
        services.set_user_roles(user=user, role_uids=["00000000-0000-7000-8000-000000000000"], actor=admin)


def test_an_administrator_cannot_remove_their_own_system_role(admin, make_role):
    other = make_role("Other", [])
    with pytest.raises(ValidationError) as excinfo:
        services.set_user_roles(user=admin, role_uids=[other.uid], actor=admin)
    assert excinfo.value.code == 9142
    assert SYSTEM_ROLE_NAME in selectors.user_role_names(admin)


def test_role_rules(catalogue, user):
    role = services.create_role(name="  Clerk  ", description="Files things")
    assert role.name == "Clerk"
    with pytest.raises(DuplicateError):
        services.create_role(name="clerk")
    with pytest.raises(ValidationError):
        services.create_role(name="  ")

    services.update_role(uid=role.uid, name="Senior clerk", description="")
    assert Role.objects.get(pk=role.pk).name == "Senior clerk"
    with pytest.raises(ValidationError):
        services.update_role(uid=role.uid, name=" ")

    services.grant_role(user, role)
    with pytest.raises(ValidationError) as excinfo:
        services.delete_role(uid=role.uid)
    assert excinfo.value.code == 9141
    services.revoke_role(user, role)
    services.delete_role(uid=role.uid)
    assert not Role.objects.filter(pk=role.pk).exists()


def test_system_roles_are_locked(catalogue):
    system = Role.objects.get(name=SYSTEM_ROLE_NAME)
    for action in (
        lambda: services.update_role(uid=system.uid, name="Renamed"),
        lambda: services.delete_role(uid=system.uid),
        lambda: services.assign_permissions_to_role(role_uid=system.uid, permission_uids=[]),
    ):
        with pytest.raises(ValidationError) as excinfo:
            action()
        assert excinfo.value.code == 9140


def test_unknown_permissions_are_refused(make_role):
    role = make_role("Reader", [])
    with pytest.raises(NotFoundError):
        services.assign_permissions_to_role(
            role_uid=role.uid, permission_uids=["00000000-0000-7000-8000-000000000000"]
        )
