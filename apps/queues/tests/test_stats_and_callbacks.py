"""Service-level stats, overflow in the call records, and callback tasks from voicemail."""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.calls import writer
from apps.calls.models import Call, CallbackTask
from apps.core.exceptions import ValidationError
from apps.queues import services as queues
from contracts import messages as m
from contracts.envelope import Event

pytestmark = pytest.mark.django_db


@pytest.fixture
def test_a_queue_cannot_overflow_into_itself():
    queue = queues.save_queue(actor=None, name="Support")
    with pytest.raises(ValidationError):
        queues.validate_overflow(queue, queue)


def test_the_writer_follows_a_call_into_its_overflow_queue():
    writer.write(
        Event(
            m.CALL_STARTED,
            {"call": "c1", "leg": "c1", "entry": "800", "queue": "Support", "caller": "x", "sip_call_id": ""},
        )
    )
    writer.write(
        Event(
            m.CALL_OVERFLOWED,
            {"call": "c1", "from_queue": "Support", "to_queue": "Backup", "reason": "max wait reached"},
        )
    )
    assert Call.objects.get(linkedid="c1").queue == "Backup"


def make_call(queue, *, started, waited=None, talked=60, result="answered"):
    answered = started + timedelta(seconds=waited) if waited is not None else None
    ended = (answered or started) + timedelta(seconds=talked if answered else 30)
    return Call.objects.create(
        linkedid=f"c{Call.objects.count() + 1}",
        queue=queue,
        started_at=started,
        answered_at=answered,
        ended_at=ended,
        result=result,
    )


def test_service_level_counts_calls_answered_within_the_target_of_all_offered(gql, make_user, grant):
    queue = queues.save_queue(actor=None, name="Support", service_level_seconds=20, service_level_percent=80)
    now = timezone.now()
    for waited in (5, 10, 20, 40):  # three within 20 s
        make_call("Support", started=now - timedelta(minutes=30), waited=waited)
    make_call("Support", started=now - timedelta(minutes=20), result="abandoned")
    make_call("Support", started=now - timedelta(minutes=10), result="overflow")
    make_call("Other", started=now - timedelta(minutes=10), waited=1)
    make_call("Support", started=now - timedelta(days=3), waited=1)  # outside the period

    viewer = make_user(account_type="Internal")
    grant(viewer, "can_view_queues")
    answer = gql(
        "query S($uid: UUID!, $since: DateTime) { getQueueStats(uid: $uid, since: $since) { code data {"
        " offered answered abandoned overflowed answeredWithinTarget serviceLevelPercent targetPercent metTarget"
        " averageWaitSeconds longestWaitSeconds averageTalkSeconds } } }",
        {"uid": str(queue.uid), "since": (now - timedelta(hours=1)).isoformat()},
        user=viewer,
    )
    stats = answer["data"]
    assert (stats["offered"], stats["answered"], stats["abandoned"], stats["overflowed"]) == (6, 4, 1, 1)
    assert stats["answeredWithinTarget"] == 3
    assert (
        stats["serviceLevelPercent"] == 50.0 and stats["targetPercent"] == 80 and stats["metTarget"] is False
    )
    assert (stats["averageWaitSeconds"], stats["longestWaitSeconds"], stats["averageTalkSeconds"]) == (
        18.8,
        40.0,
        60.0,
    )


def test_stats_need_permission(gql, user):
    queue = queues.save_queue(actor=None, name="Support")
    answer = gql(
        "query S($uid: UUID!) { getQueueStats(uid: $uid) { code } }", {"uid": str(queue.uid)}, user=user
    )
    assert answer["code"] == 9000


def test_a_callback_request_makes_one_task_and_the_call_ends_as_voicemail():
    started = Event(
        m.CALL_STARTED,
        {"call": "c9", "leg": "c9", "entry": "800", "queue": "Claims", "caller": "x", "sip_call_id": ""},
    )
    request = Event(
        m.CALLBACK_REQUESTED,
        {
            "call": "c9",
            "queue": "Claims",
            "caller": "0712345678",
            "caller_name": "Mteja",
            "language": "swahili",
            "recording": "callback-c9",
            "duration": 12,
        },
    )
    ended = Event(m.CALL_ENDED, {"call": "c9", "result": "voicemail", "reason": "message left"})
    for event in (started, request, request, ended):
        writer.write(event)
    [task] = CallbackTask.objects.all()
    assert (task.caller, task.queue, task.recording, task.duration, task.status) == (
        "0712345678",
        "Claims",
        "callback-c9",
        12,
        "open",
    )
    assert Call.objects.get(linkedid="c9").result == "voicemail"


def test_the_callback_page_and_its_message_player(admin, make_user, grant, monkeypatch):
    from apps.calls import views

    task = CallbackTask.objects.create(
        linkedid="c9",
        queue="Claims",
        caller="0712",
        recording="callback-c9",
        duration=5,
        created_at=timezone.now(),
    )
    monkeypatch.setattr(
        views, "fetch_recording", lambda name: b"RIFF....WAVE" if name == "callback-c9" else None
    )

    browser = Client()
    browser.force_login(admin)
    page = browser.get(f"/admin/calls/callbacktask/{task.pk}/change/")
    assert page.status_code == 200 and f"/calls/callbacks/{task.pk}/recording".encode() in page.content
    audio = browser.get(f"/calls/callbacks/{task.pk}/recording")
    assert (
        audio.status_code == 200
        and audio["Content-Type"] == "audio/wav"
        and audio.content.startswith(b"RIFF")
    )

    stranger = make_user(account_type="Internal", is_staff=True)
    browser.force_login(stranger)
    assert browser.get(f"/calls/callbacks/{task.pk}/recording").status_code == 403
    listener = make_user(account_type="Internal", is_staff=True)
    grant(listener, "can_listen_recordings")
    browser.force_login(listener)
    assert browser.get(f"/calls/callbacks/{task.pk}/recording").status_code == 200
