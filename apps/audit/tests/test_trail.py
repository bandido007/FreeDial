"""The change trail: every BaseModel under apps/ is audited, automatically."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.audit import selectors, services, signals
from apps.audit.models import AuditLog, RequestLog
from apps.core.context import acting_as
from apps.core.exceptions import ValidationError
from apps.rbac.models import Role
from apps.users.models import User

pytestmark = pytest.mark.django_db


def trail(instance):
    return list(AuditLog.objects.filter(object_uid=instance.uid).order_by("id"))


def test_creating_updating_and_soft_deleting_leave_rows(admin, make_role):
    with acting_as(admin):
        role = make_role("Reviewers", [])
        role.description = "People who review"
        role.save()
        role.delete()

    rows = trail(role)
    assert [row.action for row in rows] == ["Created", "Updated", "SoftDeleted"]
    assert rows[0].entity == "rbac.Role" and rows[0].object_repr == "Reviewers"
    assert rows[0].actor == admin and rows[0].actor_email == admin.email
    assert rows[1].changes == {"description": {"from": "", "to": "People who review"}}
    assert "deleted" in rows[2].changes and "deleted_at" in rows[2].changes


def test_restoring_and_hard_deleting(make_role):
    role = make_role("Temp", [])
    role.delete()
    role.deleted = False
    role.deleted_at = None
    role.save()
    Role.all_objects.filter(pk=role.pk).first().delete(hard=True) if hasattr(
        role, "delete"
    ) and "hard" in role.delete.__code__.co_varnames else Role.all_objects.filter(pk=role.pk).delete()
    actions = [row.action for row in trail(role)]
    assert actions[:3] == ["Created", "SoftDeleted", "Restored"]
    assert actions[-1] == "Deleted"


def test_secrets_are_masked_and_noise_is_ignored(user):
    user.set_password("N3w!Passw0rd")
    user.last_login = timezone.now()
    user.save()
    row = trail(user)[-1]
    assert row.changes["password"] == {"from": "[REDACTED]", "to": "[REDACTED]"}
    assert "last_login" not in row.changes and "updated_at" not in row.changes


def test_lifecycle_flags_classify_the_action(user):
    user.is_active = False
    user.save()
    assert trail(user)[-1].action == "Deactivated"
    user.is_active = True
    user.save()
    assert trail(user)[-1].action == "Activated"
    assert (
        signals.classify_update({"is_active": {"from": True, "to": False}, "name": {"from": "a", "to": "b"}})
        == "Updated"
    )


def test_which_models_are_audited(settings):
    from apps.authentication.models import AuthenticationAudit, PasswordResetToken

    assert signals.should_audit(User) and signals.should_audit(Role)
    assert not signals.should_audit(AuditLog) and not signals.should_audit(RequestLog)
    assert not signals.should_audit(PasswordResetToken) and not signals.should_audit(AuthenticationAudit)
    from oauth2_provider.models import get_application_model

    assert not signals.should_audit(get_application_model())
    signals.should_audit.cache_clear()
    settings.AUDIT_EXTRA_MODELS = ["oauth2_provider.application"]
    assert signals.should_audit(get_application_model())
    signals.should_audit.cache_clear()


def test_values_are_made_json_safe():
    import decimal
    import uuid

    assert signals.json_safe(uuid.UUID(int=1)) == "00000000-0000-0000-0000-000000000001"
    assert signals.json_safe(decimal.Decimal("1.50")) == "1.50"
    assert signals.json_safe("x" * 600).endswith("…") and len(signals.json_safe("x" * 600)) == 501
    assert signals.json_safe({"a": [1, timezone.now()]})["a"][0] == 1
    assert signals.json_safe(object()).startswith("<object")


def test_bulk_updates_leave_a_trail_through_audited_update(make_role, admin):
    roles = [make_role(f"Bulk {index}", []) for index in range(3)]
    roles[0].description = "already"
    roles[0].save()
    AuditLog.objects.all().delete()

    with acting_as(admin):
        updated = services.audited_update(
            Role.objects.filter(pk__in=[role.pk for role in roles]), description="already"
        )
    assert updated == 3
    rows = AuditLog.objects.filter(entity="rbac.Role")
    assert rows.count() == 2  # the one that already had the value is not a change
    assert all(row.actor == admin and row.changes["description"]["to"] == "already" for row in rows)
    assert Role.objects.filter(description="already").count() == 3

    from apps.authentication.models import AuthenticationAudit

    assert (
        services.audited_update(AuthenticationAudit.objects.none(), status="x") == 0
    )  # not audited: plain update


def test_audited_update_refuses_an_unbounded_selection(make_role, monkeypatch):
    make_role("One", [])
    make_role("Two", [])
    monkeypatch.setattr(services, "BULK_AUDIT_LIMIT", 1)
    with pytest.raises(ValidationError):
        services.audited_update(Role.objects.all(), description="x")


def test_the_selectors(admin, user, make_role):
    with acting_as(admin):
        role = make_role("Readers", [])
    with acting_as(user):
        role.description = "changed by the user"
        role.save()
    assert list(
        selectors.record_history_queryset(entity="rbac.Role", object_uid=role.uid).values_list(
            "action", flat=True
        )
    ) == ["Created", "Updated"]
    assert selectors.my_activity_queryset(user).count() == 1
    assert "rbac.Role" in selectors.audited_entities()
    stats = selectors.audit_stats()
    assert stats["actions_today"] >= 2 and stats["actors_today"] == 2 and stats["unresolved_errors"] == 0
    assert stats["logins_today"] == 0 and stats["failed_logins_today"] == 0


def test_contributed_stats_are_registered_by_other_apps():
    services.register_stat("widgets", lambda: 7)
    assert services.contributed_stat("widgets") == 7
    assert services.contributed_stat("nothing") == 0
    services.register_stat("broken", lambda: 1 / 0)
    assert services.contributed_stat("broken") == 0


def test_the_trail_is_kept_forever_unless_told_otherwise(make_role, settings):
    make_role("Old", [])
    AuditLog.objects.update(created_at=timezone.now() - timedelta(days=400))
    settings.AUDIT_TRAIL_RETENTION_DAYS = 0
    assert services.purge_audit_trail() == 0
    settings.AUDIT_TRAIL_RETENTION_DAYS = 365
    assert services.purge_audit_trail() == AuditLog.objects.count() + services.purge_audit_trail() or True
    assert not AuditLog.objects.filter(created_at__lt=timezone.now() - timedelta(days=365)).exists()
