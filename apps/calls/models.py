"""What happened on calls, written only by the writer (:mod:`apps.calls.writer`).

* :class:`Event` — every brain event, append-only. Reports are built from it.
* :class:`Call` — one row per call (its linkedid), kept up to date from the events.
* :class:`Leg` — one row per channel of a call (customer, each agent rung).
* :class:`CallbackTask` — a caller to ring back: the queue overflowed to voicemail.

Machine-written records, so plain models: no soft delete and no audit entries
(the Event log is the trail). Ids are strings: Asterisk's ids are not all numbers.
"""

from django.conf import settings
from django.db import models


class Event(models.Model):
    event_id = models.UUIDField(unique=True, help_text="The envelope's event_id; a repeat is ignored")
    type = models.CharField(max_length=60, db_index=True)
    at = models.DateTimeField(db_index=True, help_text="When the brain decided it")
    call_id = models.CharField(max_length=64, blank=True, db_index=True, help_text="The call's linkedid")
    leg_id = models.CharField(max_length=64, blank=True)
    agent_uid = models.CharField(max_length=64, blank=True, db_index=True)
    command_id = models.CharField(max_length=64, blank=True)
    data = models.JSONField(default=dict)
    written_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "calls_events"
        ordering = ["at", "id"]

    def __str__(self):
        return f"{self.at:%Y-%m-%d %H:%M:%S} {self.type} {self.call_id or self.agent_uid}"


class CallResult(models.TextChoices):
    ANSWERED = "answered"
    ABANDONED = "abandoned"
    OVERFLOW = "overflow"
    VOICEMAIL = "voicemail"
    FAILED = "failed"


class Call(models.Model):
    linkedid = models.CharField(max_length=64, unique=True)
    entry = models.CharField(max_length=40, blank=True, help_text="The number dialled")
    queue = models.CharField(max_length=120, blank=True)
    caller = models.CharField(max_length=80, blank=True)
    caller_name = models.CharField(max_length=80, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    result = models.CharField(max_length=20, choices=CallResult.choices, blank=True)
    end_reason = models.CharField(max_length=200, blank=True)
    agent_uid = models.CharField(max_length=64, blank=True, help_text="The agent who answered it")

    class Meta:
        db_table = "calls_calls"
        ordering = ["-started_at"]

    def __str__(self):
        return self.linkedid


class LegRole(models.TextChoices):
    CUSTOMER = "customer"
    AGENT = "agent"


class Leg(models.Model):
    uniqueid = models.CharField(max_length=64, unique=True, help_text="Asterisk's channel id")
    call = models.ForeignKey(Call, on_delete=models.CASCADE, related_name="legs")
    role = models.CharField(max_length=20, choices=LegRole.choices)
    endpoint = models.CharField(max_length=80, blank=True)
    agent_uid = models.CharField(max_length=64, blank=True)
    sip_call_id = models.CharField(max_length=200, blank=True, help_text="Joins this leg to SIP traces")
    started_at = models.DateTimeField(null=True, blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(
        max_length=40, blank=True, help_text="For an agent leg: answered, rejected, no_answer…"
    )
    cause = models.IntegerField(null=True, blank=True, help_text="Q.850 hangup cause, when Asterisk gave one")

    class Meta:
        db_table = "calls_legs"
        ordering = ["started_at", "id"]

    def __str__(self):
        return self.uniqueid


class CallbackStatus(models.TextChoices):
    OPEN = "open", "Open"
    DONE = "done", "Called back"
    CANCELLED = "cancelled", "Cancelled"


class CallbackTask(models.Model):
    """A caller who left the queue for voicemail, to be called back. One per call.

    Made by the writer from ``callback.requested``; ``recording`` names the message Asterisk stored
    (empty when the caller hung up before the tone).
    """

    linkedid = models.CharField(max_length=64, unique=True)
    call = models.ForeignKey(Call, on_delete=models.SET_NULL, null=True, blank=True, related_name="callbacks")
    queue = models.CharField(max_length=120)
    caller = models.CharField(max_length=80, blank=True)
    caller_name = models.CharField(max_length=80, blank=True)
    language = models.CharField(max_length=40, blank=True)
    recording = models.CharField(max_length=120, blank=True, help_text="Asterisk's stored recording name")
    duration = models.PositiveIntegerField(null=True, blank=True, help_text="Seconds of message")
    status = models.CharField(max_length=20, choices=CallbackStatus.choices, default=CallbackStatus.OPEN)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField()
    done_at = models.DateTimeField(null=True, blank=True)
    done_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        db_table = "calls_callback_tasks"
        ordering = ["status", "created_at"]

    def __str__(self):
        return f"call back {self.caller} ({self.queue})"
