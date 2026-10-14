"""What happened on calls, written only by the writer (:mod:`apps.calls.writer`).

* :class:`Event` — every brain event, append-only. Reports are built from it.
* :class:`Call` — one row per call (its linkedid), kept up to date from the events.
* :class:`Leg` — one row per channel of a call (customer, each agent rung).
* :class:`CallbackTask` — a caller to ring back: the queue overflowed to voicemail.
* :class:`Transfer` — an agent handed the call on (blind or consult) or made a conference.
* :class:`InteractionPart` — who served the caller, in order: the AI agent (its summary and
  transcript), then the agent who answered (and their wrap-up code).

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
    AI_HANDLED = "ai_handled", "Handled by the AI"
    FAILED = "failed"


class RecordingStatus(models.TextChoices):
    NONE = "", "No recording"
    PENDING = "pending", "Waiting for Asterisk's file"
    READY = "ready", "Ready to play"
    MISSING = "missing", "Asterisk kept no file"


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
    # M7: the stereo recording (left = the customer, right = what they heard), kept by
    # apps.calls.recordings after the call; ``recording`` is its key in the default storage.
    recording_status = models.CharField(
        max_length=20,
        choices=RecordingStatus.choices,
        default=RecordingStatus.NONE,
        blank=True,
        db_index=True,
    )
    recording = models.CharField(max_length=200, blank=True, help_text="Storage key of the stereo WAV")
    recording_seconds = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "calls_calls"
        ordering = ["-started_at"]

    def __str__(self):
        return self.linkedid


class LegRole(models.TextChoices):
    CUSTOMER = "customer"
    AGENT = "agent"
    AI = "ai"


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


class TransferStatus(models.TextChoices):
    STARTED = "started", "Ringing"
    ANSWERED = "answered", "Answered"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    FAILED = "failed", "Failed"
    CONFERENCE = "conference", "Became a conference"


class Transfer(models.Model):
    """One transfer of a call (M5), from the transfer.* events."""

    transfer_id = models.CharField(max_length=64, unique=True)
    call = models.ForeignKey(Call, on_delete=models.CASCADE, related_name="transfers")
    kind = models.CharField(max_length=20, help_text="blind or consult")
    target_kind = models.CharField(max_length=20, help_text="agent, queue or number")
    target = models.CharField(max_length=120)
    by_agent_uid = models.CharField(max_length=64, blank=True)
    target_agent_uid = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=20, choices=TransferStatus.choices, default=TransferStatus.STARTED)
    reason = models.CharField(max_length=200, blank=True)
    started_at = models.DateTimeField()
    answered_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "calls_transfers"
        ordering = ["started_at"]

    def __str__(self):
        return f"{self.kind} transfer to {self.target_kind} {self.target}: {self.status}"


class PartKind(models.TextChoices):
    AI = "ai", "AI agent"
    AGENT = "agent", "Agent"


class InteractionPart(models.Model):
    """One part of the caller's interaction (M8): the AI agent's, then a person's. Written by the writer."""

    call = models.ForeignKey(Call, on_delete=models.CASCADE, related_name="parts")
    kind = models.CharField(max_length=10, choices=PartKind.choices)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    ai_agent_uid = models.CharField(max_length=64, blank=True)
    agent_uid = models.CharField(max_length=64, blank=True)
    outcome = models.CharField(
        max_length=20, blank=True, help_text="AI: handoff, resolved, caller_left, failed"
    )
    reason = models.CharField(max_length=200, blank=True)
    summary = models.TextField(blank=True, help_text="What the AI learnt, as the agent saw it")
    transcript = models.JSONField(
        default=list, blank=True, help_text='[{"who": "caller" | "ai", "text", "at"}]'
    )
    wrapup_code = models.CharField(max_length=120, blank=True)

    class Meta:
        db_table = "calls_interaction_parts"
        ordering = ["started_at", "id"]

    def __str__(self):
        return f"{self.call} {self.kind}"
