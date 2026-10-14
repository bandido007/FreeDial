"""The endpoint log: every API transaction, classified; unexpected failures
become a work queue for developers."""

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.audit import middleware, services, tasks
from apps.audit.enums import LogLevelChoices
from apps.audit.models import ErrorReportSubscription, RequestLog
from apps.core.exceptions import NotFoundError, ValidationError

pytestmark = pytest.mark.django_db


def graphql(client, query, variables=None, **extra):
    return client.post(
        "/graphql",
        data=json.dumps({"query": query, "variables": variables or {}}),
        content_type="application/json",
        **extra,
    )


def test_every_transaction_is_logged_with_sanitised_inputs(api, user):
    client = api(user)
    RequestLog.objects.all().delete()
    graphql(client, "query Me($secret: String) { meQuery { code } }", {"secret": "hunter2", "page": 1})
    row = RequestLog.objects.get()
    assert row.kind == "Query" and row.operations == "meQuery" and row.operation_name == "Me"
    assert row.user == user and row.user_email == user.email and row.level == "INFO"
    assert row.variables == {"secret": "[REDACTED]", "page": 1} and row.status_code == 200
    assert row.request_id and row.duration_ms >= 0


def test_sign_in_attempts_are_logged_without_the_password(sign_in, user):
    RequestLog.objects.all().delete()
    sign_in(user.email, "wrong")
    row = RequestLog.objects.get()
    assert row.kind == "Rest" and row.path == "/oauth2/access_token" and row.level == "WARNING"
    assert row.variables["password"] == "[REDACTED]" and row.variables["username"] == user.email


def test_expected_failures_are_warnings_and_unexpected_ones_are_errors(api, admin, monkeypatch):
    client = api(admin)
    RequestLog.objects.all().delete()
    graphql(client, 'mutation { resolveEndpointError(uid: "00000000-0000-7000-8000-000000000000") { code } }')
    assert RequestLog.objects.get().level == "WARNING"

    from apps.audit import selectors

    monkeypatch.setattr(selectors, "audit_stats", lambda: 1 / 0)
    RequestLog.objects.all().delete()
    response = graphql(client, "query { getAuditStats { code message } }")
    envelope = response.json()["data"]["getAuditStats"]
    assert (
        envelope["code"] == 9004
        and "reference:" in envelope["message"]
        and "division" not in envelope["message"]
    )
    row = RequestLog.objects.get()
    assert row.level == "ERROR" and row.error_type == "ZeroDivisionError" and "Traceback" in row.traceback
    assert not row.is_resolved and row.errors[0]["handled"] is False


def test_tooling_chatter_and_ide_pages_are_not_logged(api, user, settings):
    client = api(user)
    RequestLog.objects.all().delete()
    graphql(client, "query IntrospectionQuery { __schema { queryType { name } } }")
    client.options("/graphql")
    client.get("/healthz")
    assert RequestLog.objects.count() == 0
    settings.AUDIT_PERSIST_SUCCESS_REQUESTS = False
    graphql(client, "query { meQuery { code } }")
    assert RequestLog.objects.count() == 0


def test_batched_and_malformed_bodies_are_described_safely(api, user, settings):
    settings.GRAPHQL_BATCH_ENABLED = True
    client = api(user)
    RequestLog.objects.all().delete()
    client.post("/graphql", data="not json", content_type="application/json")
    assert RequestLog.objects.get().operations == ""
    assert middleware.top_level_fields("query { a { b } c(x: 1) { d } }") == ["a", "c"]
    assert middleware.top_level_fields("mutation Do($x: Int) { doThing(input: {x: $x}) { code } }") == [
        "doThing"
    ]
    assert middleware.sanitize({"nested": {"apiKey": "k", "ok": [1, {"token": "t"}]}}) == {
        "nested": {"apiKey": "[REDACTED]", "ok": [1, {"token": "[REDACTED]"}]}
    }


def test_the_error_workflow(admin, user):
    error = RequestLog.objects.create(
        kind="Query",
        method="POST",
        path="/graphql",
        level="ERROR",
        status_code=200,
        error_type="KeyError",
        operations="getThing",
    )
    twin = RequestLog.objects.create(
        kind="Query",
        method="POST",
        path="/graphql",
        level="ERROR",
        status_code=200,
        error_type="KeyError",
        operations="getThing",
    )
    other = RequestLog.objects.create(
        kind="Query",
        method="POST",
        path="/graphql",
        level="ERROR",
        status_code=200,
        error_type="ValueError",
        operations="getThing",
    )
    warning = RequestLog.objects.create(
        kind="Query", method="POST", path="/graphql", level="WARNING", status_code=200
    )

    with pytest.raises(ValidationError) as excinfo:
        services.resolve_endpoint_error(uid=warning.uid, resolver=admin)
    assert excinfo.value.code == 9220
    with pytest.raises(NotFoundError):
        services.resolve_endpoint_error(uid="00000000-0000-7000-8000-000000000000")

    resolved = services.resolve_endpoint_error(uid=error.uid, note="fixed in 1.2", resolver=admin)
    assert (
        resolved.is_resolved and resolved.resolved_by == admin and resolved.resolution_note == "fixed in 1.2"
    )
    with pytest.raises(ValidationError) as excinfo:
        services.resolve_endpoint_error(uid=error.uid, resolver=admin)
    assert excinfo.value.code == 9221

    assert services.resolve_similar_errors(uid=twin.uid, note="same", resolver=admin) == 1
    other.refresh_from_db()
    assert not other.is_resolved

    reopened = services.reopen_endpoint_error(uid=error.uid)
    assert not reopened.is_resolved and reopened.resolved_by is None
    with pytest.raises(ValidationError) as excinfo:
        services.reopen_endpoint_error(uid=other.uid)
    assert excinfo.value.code == 9222


def test_the_daily_error_report(admin, user, make_user):
    from apps.notifications.models import Notification

    assert services.send_error_report() == {"errors": 0, "unresolved": 0, "recipients": 0, "sent": False}
    RequestLog.objects.create(
        kind="Query",
        method="POST",
        path="/graphql",
        level="ERROR",
        status_code=200,
        error_type="KeyError",
        operations="getThing",
        created_at=timezone.localtime() - timedelta(days=1),  # yesterday: the daily window
    )
    assert services.send_error_report()["recipients"] == 0

    inactive = make_user(is_active=False)
    subscriptions = services.set_error_report_recipients(user_uids=[admin.uid, user.uid, inactive.uid])
    assert len(subscriptions) == 3
    with pytest.raises(ValidationError):
        services.set_error_report_recipients(user_uids=["00000000-0000-7000-8000-000000000000"])

    outcome = tasks.send_daily_error_report()
    assert outcome["errors"] == 1 and outcome["recipients"] == 2 and outcome["sent"]
    digests = Notification.objects.filter(subject__contains="1 endpoint error(s)")
    assert sorted(digests.values_list("receiver__email", flat=True)) == sorted([admin.email, user.email])

    services.set_error_report_recipients(user_uids=[])
    assert ErrorReportSubscription.objects.count() == 0


def test_logs_age_out_by_severity_but_open_errors_stay(settings):
    old = timezone.now() - timedelta(days=200)
    for level in ("INFO", "WARNING", "ERROR"):
        RequestLog.objects.create(
            kind="Query", method="POST", path="/graphql", level=level, status_code=200, created_at=old
        )
    RequestLog.objects.create(
        kind="Query",
        method="POST",
        path="/graphql",
        level="ERROR",
        status_code=200,
        created_at=old,
        is_resolved=True,
    )
    RequestLog.objects.create(kind="Query", method="POST", path="/graphql", level="INFO", status_code=200)

    purged = tasks.purge_logs()
    assert purged == {"INFO": 1, "WARNING": 1, "RESOLVED_ERROR": 1, "AUDIT_TRAIL": 0}
    assert RequestLog.objects.count() == 2
    assert RequestLog.objects.filter(level=LogLevelChoices.ERROR.value, is_resolved=False).exists()
