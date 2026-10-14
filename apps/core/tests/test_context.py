"""Request context: acting user, request ID, and work postponed to the end."""

import json
import logging

import pytest
from django.test import Client

from apps.core import context
from apps.core.logs import JsonFormatter, RequestContextFilter
from apps.rbac.models import Role

pytestmark = pytest.mark.django_db


def test_acting_as_stamps_writes_outside_a_request(user):
    assert context.get_current_user() is None
    with context.acting_as(user):
        role = Role.objects.create(name="created-in-a-task")
    assert role.created_by == user
    assert role.updated_by == user
    assert context.get_current_user() is None


def test_acting_as_restores_the_previous_actor(user, make_user):
    other = make_user()
    with context.acting_as(user):
        with context.acting_as(other):
            assert context.get_current_user() == other
        assert context.get_current_user() == user


def test_a_partial_save_still_records_who_and_when(user):
    role = Role.objects.create(name="partial")
    before = role.updated_at
    with context.acting_as(user):
        role.description = "changed"
        role.save(update_fields=["description"])
    role.refresh_from_db()
    assert role.updated_by == user
    assert role.updated_at >= before


def test_soft_delete_and_restore(user):
    role = Role.objects.create(name="temporary")
    with context.acting_as(user):
        role.delete()
    assert not Role.objects.filter(pk=role.pk).exists()
    gone = Role.all_objects.get(pk=role.pk)
    assert gone.deleted and gone.deleted_by == user
    gone.restore()
    assert Role.objects.filter(pk=role.pk).exists()


def test_after_request_runs_now_when_there_is_no_request():
    assert context.after_request(lambda: None) is False


def test_after_request_runs_postponed_work_in_order_and_isolated():
    ran = []
    token = context.open_after_request_queue()
    assert context.after_request(lambda: ran.append("first")) is True
    context.after_request(lambda: 1 / 0)  # a failure must not stop the rest
    context.after_request(lambda: ran.append("third"))
    assert ran == []
    context.run_after_request_queue(token)
    assert ran == ["first", "third"]
    assert context.after_request(lambda: None) is False


def test_every_response_carries_a_request_id():
    response = Client().get("/healthz")
    assert len(response["X-Request-ID"]) >= 8


def test_a_well_formed_request_id_is_kept_and_a_malformed_one_replaced():
    assert Client().get("/healthz", HTTP_X_REQUEST_ID="trace-abc-12345")["X-Request-ID"] == "trace-abc-12345"
    injected = Client().get("/healthz", HTTP_X_REQUEST_ID="x\r\nSet-Cookie: a=b")
    assert "Set-Cookie" not in injected["X-Request-ID"]
    assert Client().get("/healthz", HTTP_X_REQUEST_ID="short")["X-Request-ID"] != "short"


def test_log_records_carry_the_request_id_and_render_as_json():
    context.set_request_id("req-abcdef12")
    record = logging.LogRecord("apps.test", logging.WARNING, __file__, 1, "hello %s", ("world",), None)
    record.invoice = "INV-1"
    RequestContextFilter().filter(record)
    line = json.loads(JsonFormatter().format(record))
    context.set_request_id(None)

    assert line["message"] == "hello world"
    assert line["level"] == "WARNING"
    assert line["request_id"] == "req-abcdef12"
    assert line["invoice"] == "INV-1"
    assert line["environment"] == "testing"


def test_json_logs_include_the_exception():
    try:
        raise ValueError("broken")
    except ValueError:
        import sys

        record = logging.LogRecord("apps.test", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    RequestContextFilter().filter(record)
    line = json.loads(JsonFormatter().format(record))
    assert "ValueError: broken" in line["exception"]
    assert line["request_id"] == "-"
