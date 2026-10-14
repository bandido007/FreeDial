"""The permission catalogue: what apps declare, and how it reaches the database."""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.core.catalogue import Group, Perm, RoleSpec, derive_level
from apps.rbac import catalogue, services
from apps.rbac.models import Permission, PermissionGroup, Role, RolePermission
from apps.rbac.permissions import SYSTEM_ROLE_NAME

pytestmark = pytest.mark.django_db


def make(groups, roles=None):
    return catalogue.Catalogue(
        groups=[("test", group) for group in groups],
        roles={role.name: role for role in roles or []},
    )


GOOD = Perm("can_view_things", "View things", "See the list of things and open one. Read-only.")


def test_the_shipped_catalogue_is_well_written():
    """Every permission is written for the person ticking it on a role."""
    assert catalogue.problems() == []


def test_every_app_contributes():
    codes = catalogue.collect().codes()
    for code in ("can_view_users", "can_view_roles", "can_view_audit_logs", "can_view_notifications"):
        assert code in codes


def test_levels_are_derived_from_the_verb():
    assert derive_level("can_view_users") == "VIEW"
    assert derive_level("can_edit_user") == "CHANGE"
    assert derive_level("can_delete_user") == "DECIDE"
    assert derive_level("can_approve_invoices") == "DECIDE"
    assert Perm("can_manage_x", "n", "d", level="DECIDE").access_level == "DECIDE"


@pytest.mark.parametrize(
    "perm,expected",
    [
        (Perm("view_things", "View things", "See the list of things. Read-only."), "can_<verb>_<thing>"),
        (Perm("can_view_things", "", "See the list of things. Read-only."), "name is missing"),
        (Perm("can_view_things", "x" * 46, "See the list of things. Read-only."), "longer than"),
        (Perm("can_edit_things", "Add/edit things", "Add a thing or change one that exists."), "slash"),
        (
            Perm("can_edit_things", "can_edit_things", "Add a thing or change one that exists."),
            "repeats the code",
        ),
        (Perm("can_edit_things", "Edit things", "Too short."), "too short"),
        (Perm("can_edit_things", "Edit things", "Add a thing or change one that exists"), "full sentence"),
        (Perm("can_view_things", "View things", "See the list of things and open one."), "Read-only"),
        (Perm("can_edit_things", "Edit things", "Add a thing or change one.", level="HUGE"), "unknown level"),
    ],
)
def test_badly_written_permissions_are_reported(perm, expected):
    found = catalogue.problems(make([Group("Things", "Everything about things.", [perm])]))
    assert any(expected in problem for problem in found), found


def test_duplicates_and_unknown_codes_are_reported():
    group = Group("Things", "Everything about things.", [GOOD, GOOD])
    role = RoleSpec(name="Clerk", permissions=["can_view_things", "can_fly"])
    found = catalogue.problems(make([group], [role]))
    assert any("already declared" in problem for problem in found)
    assert any("can_fly" in problem for problem in found)
    assert catalogue.problems(make([Group("Things", "", [GOOD])]))


def test_sync_writes_permissions_and_keeps_the_system_role_complete():
    result = services.sync_catalogue()
    assert result["permissions"] == Permission.objects.count() > 20
    system = Role.objects.get(name=SYSTEM_ROLE_NAME)
    assert system.is_system
    assert system.role_permissions.count() == Permission.objects.count()
    assert Permission.objects.get(code="can_view_users").level == "VIEW"
    assert PermissionGroup.objects.get(name="User Management").app_label == "users"


def test_sync_is_idempotent():
    services.sync_catalogue()
    counts = (Permission.objects.count(), Role.objects.count(), RolePermission.objects.count())
    result = services.sync_catalogue()
    assert counts == (Permission.objects.count(), Role.objects.count(), RolePermission.objects.count())
    assert result["new"] == [] and result["retired"] == []


def test_a_declared_role_is_created_with_its_permissions():
    services.sync_catalogue()
    auditor = Role.objects.get(name="Security Auditor")
    held = set(auditor.role_permissions.values_list("permission__code", flat=True))
    # Several apps add to the same role: their lists are merged.
    assert {"can_view_roles", "can_view_users", "can_view_audit_logs"} <= held


def test_permissions_an_administrator_removed_are_not_put_back():
    services.sync_catalogue()
    auditor = Role.objects.get(name="Security Auditor")
    RolePermission.all_objects.filter(role=auditor, permission__code="can_view_users").delete()

    services.sync_catalogue()
    held = set(auditor.role_permissions.values_list("permission__code", flat=True))
    assert "can_view_users" not in held


def test_a_permission_nobody_declares_is_retired():
    services.sync_catalogue()
    group = PermissionGroup.objects.first()
    stale = Permission.objects.create(code="can_do_the_old_thing", name="Old", group=group)
    role = Role.objects.create(name="Legacy")
    RolePermission.objects.create(role=role, permission=stale)

    result = services.sync_catalogue()
    assert result["retired"] == ["can_do_the_old_thing"]
    assert not Permission.objects.filter(code="can_do_the_old_thing").exists()
    assert not RolePermission.objects.filter(role=role).exists()


def test_a_retired_permission_comes_back_when_declared_again():
    services.sync_catalogue()
    Permission.objects.filter(code="can_view_users").update(deleted=True)
    result = services.sync_catalogue()
    assert "can_view_users" in result["new"]
    assert Permission.objects.filter(code="can_view_users").exists()


def test_the_command_checks_and_syncs(settings, capsys):
    call_command("sync_rbac", "--check")
    assert "Catalogue is valid" in capsys.readouterr().out

    settings.DEFAULT_ADMIN_EMAIL = "root@example.org"
    settings.DEFAULT_ADMIN_PASSWORD = "Str0ng!Passw0rd"
    call_command("sync_rbac")
    from django.contrib.auth import get_user_model

    root = get_user_model().objects.get(email="root@example.org")
    assert root.account_type == "System" and root.is_superuser and root.is_verified
    assert root.user_roles.filter(role__name=SYSTEM_ROLE_NAME).exists()


def test_the_command_refuses_a_broken_catalogue(monkeypatch):
    monkeypatch.setattr(catalogue, "problems", lambda declared=None: ["can_x: the name is missing."])
    with pytest.raises(CommandError):
        call_command("sync_rbac", "--check")
    with pytest.raises(CommandError):
        call_command("sync_rbac")


def test_without_credentials_no_administrator_is_created(settings):
    assert services.ensure_bootstrap_admin(email="", password="") is None
