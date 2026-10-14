"""Resolver decorators: failure envelopes, no leaked internals, one permission lookup."""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from apps.core import decorators, hooks
from apps.core.context import set_request_id
from apps.core.exceptions import NotFoundError
from apps.rbac.models import Role

pytestmark = pytest.mark.django_db


class Info:
    """The slice of graphene's resolve info the decorators read."""

    field_name = "someField"
    parent_type = None
    variable_values: dict = {}

    def __init__(self, user):
        self.context = RequestFactory().post("/graphql")
        self.context.user = user


def test_an_unexpected_error_does_not_leak_its_text(settings, user):
    settings.EXPOSE_ERROR_DETAILS = False
    set_request_id("req-12345678")

    @decorators.log_errors_and_warnings
    def resolver(root, info):
        raise RuntimeError('relation "secret_table" does not exist at /srv/app/models.py')

    envelope = resolver(None, Info(user))
    set_request_id(None)

    assert envelope["status"] is False
    assert envelope["code"] == 9004
    assert "secret_table" not in envelope["message"]
    assert "/srv/app" not in envelope["message"]
    assert "req-12345678" in envelope["message"]


def test_a_developer_sees_the_error_text(settings, user):
    settings.EXPOSE_ERROR_DETAILS = True

    @decorators.log_errors_and_warnings
    def resolver(root, info):
        raise RuntimeError("boom")

    assert "boom" in resolver(None, Info(user))["message"]


def test_an_application_error_keeps_its_code_and_message(user):
    @decorators.log_errors_and_warnings
    def resolver(root, info):
        raise NotFoundError("Invoice not found")

    envelope = resolver(None, Info(user))
    assert (envelope["code"], envelope["message"]) == (9003, "Invoice not found")


def test_a_failed_resolver_rolls_its_writes_back(user):
    @decorators.log_errors_and_warnings
    def resolver(root, info):
        Role.objects.create(name="half-written")
        raise NotFoundError("no")

    resolver(None, Info(user))
    assert not Role.objects.filter(name="half-written").exists()


def test_failures_are_announced_to_the_api_error_hook(user):
    seen = []

    def listener(sender, error=None, operation="", handled=True, **kwargs):
        seen.append((type(error).__name__, operation, handled))

    hooks.api_error.connect(listener, dispatch_uid="test-listener")
    try:

        @decorators.log_errors_and_warnings
        def crash(root, info):
            raise RuntimeError("boom")

        @decorators.log_errors_and_warnings
        def refuse(root, info):
            raise NotFoundError("no")

        crash(None, Info(user))
        refuse(None, Info(user))
    finally:
        hooks.api_error.disconnect(dispatch_uid="test-listener")

    assert ("RuntimeError", "someField", False) in seen
    assert ("NotFoundError", "someField", True) in seen


def test_anonymous_callers_are_refused():
    @decorators.authenticated()
    def resolver(root, info):
        return "reached"

    assert resolver(None, Info(AnonymousUser()))["code"] == 9000


def test_a_guard_without_a_rule_denies(user):
    @decorators.has_permission()
    def resolver(root, info):
        return "reached"

    assert resolver(None, Info(user))["code"] == 9000


def test_permission_or_account_type_lets_you_in(user, grant):
    @decorators.has_permission(permissions=["can_view_roles"], user_types=["System"])
    def resolver(root, info):
        return "reached"

    assert resolver(None, Info(user))["code"] == 9000
    grant(user, "can_view_roles")
    assert resolver(None, Info(user)) == "reached"
    assert resolver.required_permissions == ("can_view_roles",)


def test_compare_both_requires_permission_and_account_type(user, grant):
    @decorators.has_permission(permissions=["can_view_roles"], user_types=["System"], compare_both=True)
    def resolver(root, info):
        return "reached"

    grant(user, "can_view_roles")
    assert resolver(None, Info(user))["code"] == 9000
    user.account_type = "System"
    assert resolver(None, Info(user)) == "reached"


def test_permissions_are_resolved_once_per_request(user, grant, monkeypatch):
    grant(user, "can_view_roles")
    calls = []
    real = hooks._permission_resolver

    def counting(person):
        calls.append(person.pk)
        return real(person)

    monkeypatch.setattr(hooks, "_permission_resolver", counting)
    info = Info(user)
    for _ in range(5):
        assert "can_view_roles" in hooks.resolve_permissions(user, info.context)
    assert len(calls) == 1

    hooks.forget_permissions(info.context)
    hooks.resolve_permissions(user, info.context)
    assert len(calls) == 2
