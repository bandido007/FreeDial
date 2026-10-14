"""The agent's own screen data. Live calls remain brain-owned Redis snapshots.

Only queue counts and the signed-in agent's call leave this layer; callers in
other queues and colleagues' personal statistics never reach the agent page.
"""

import json
from datetime import datetime, time
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.agents.models import Agent
from apps.calls import bus
from apps.calls.models import Event, Leg
from apps.queues.models import Queue
from contracts import messages as m


def screen_data(agent: Agent, state: dict[str, Any]) -> dict[str, Any]:
    uid = str(agent.uid)
    now = timezone.now()
    midnight = timezone.make_aware(datetime.combine(timezone.localdate(now), time.min))
    queues = list(Queue.objects.filter(active=True, agents=agent).order_by("name"))
    raw = bus.client().hgetall(settings.BUS_CALLS_KEY)
    calls = [json.loads(value) for value in raw.values()] if isinstance(raw, dict) else []
    call_id = state.get("call") or state.get("wrapup_call")
    current = next(
        (
            c
            for c in calls
            if c.get("call") == call_id and (c.get("agent") == uid or uid in c.get("ringing", []))
        ),
        None,
    )
    last_state = Event.objects.filter(type=m.AGENT_STATE, agent_uid=uid).order_by("-at", "-id").first()
    state_since = (
        last_state.at
        if last_state
        and all(last_state.data.get(k) == state.get(k) for k in ("activity", "availability", "call"))
        else None
    )
    if call_id:
        offered = (
            Event.objects.filter(type=m.CALL_OFFERED, agent_uid=uid, call_id=call_id)
            .order_by("-at", "-id")
            .first()
        )
        leg = (
            Leg.objects.filter(agent_uid=uid, call__linkedid=call_id)
            .select_related("call")
            .order_by("-started_at")
            .first()
        )
        # The writer can lag behind the live call. Prefer the live snapshot.
        current = {**(offered.data if offered else {}), **(current or {})}
        ticket = Event.objects.filter(type=m.ROUTING_TICKET, call_id=call_id).order_by("-at", "-id").first()
        if ticket:
            current.setdefault("language", ticket.data.get("language"))
        if leg:
            current.setdefault("caller", leg.call.caller)
            current.setdefault("caller_name", leg.call.caller_name)
            current.setdefault("queue", leg.call.queue)
            current.setdefault("ended_at", leg.ended_at.timestamp() if leg.ended_at else None)
            current.setdefault("connected_at", leg.answered_at.timestamp() if leg.answered_at else None)
            if leg.answered_at:
                queued = (
                    Event.objects.filter(type=m.CALL_QUEUED, call_id=call_id, at__lte=leg.answered_at)
                    .order_by("-at", "-id")
                    .first()
                )
                if queued:
                    current["wait_seconds"] = max(0, (leg.answered_at - queued.at).total_seconds())
        current["call"] = call_id
        queue = next((q for q in queues if q.name == current.get("queue")), None)
        # Transferred calls can come from a queue outside the agent's memberships.
        if queue is None:
            queue = Queue.objects.filter(name=current.get("queue"), active=True).first()
        current["ring_timeout"] = queue.ring_timeout if queue else 20
        current["wrapup_seconds"] = queue.wrapup_seconds if queue else 0
        if offered:
            current["offered_at"] = offered.at.timestamp()
        waiting_since = current.get("queue_entered_at") or current.get("waiting_since")
        if current.get("connected_at") and waiting_since:
            current.setdefault("wait_seconds", max(0, current["connected_at"] - waiting_since))
    return {
        "state_since": state_since,
        "current_call": current,
        "queues": [
            {
                "name": q.name,
                "ring_timeout": q.ring_timeout,
                "wrapup_seconds": q.wrapup_seconds,
                **queue_load(q.name, calls, now.timestamp()),
            }
            for q in queues
        ],
        "my_day": my_day(uid, midnight, now),
    }


def queue_load(name: str, calls: list[dict[str, Any]], now: float) -> dict[str, Any]:
    waiting = [c for c in calls if c.get("queue") == name and c.get("state") in ("waiting", "offering")]
    return {
        "waiting": len(waiting),
        "longest_wait": max(
            (max(0, now - (c.get("queue_entered_at") or c.get("waiting_since", now))) for c in waiting),
            default=0,
        ),
    }


def my_day(uid: str, since: datetime, now: datetime) -> dict[str, Any]:
    legs = list(Leg.objects.filter(agent_uid=uid, answered_at__gte=since, answered_at__lte=now))
    talks = [
        max(0, (leg.ended_at - leg.answered_at).total_seconds())
        for leg in legs
        if leg.ended_at is not None and leg.answered_at is not None
    ]
    talk = sum(talks)
    states = list(
        Event.objects.filter(type=m.AGENT_STATE, agent_uid=uid, at__gte=since, at__lte=now).order_by(
            "at", "id"
        )
    )
    before = (
        Event.objects.filter(type=m.AGENT_STATE, agent_uid=uid, at__lt=since).order_by("-at", "-id").first()
    )
    cursor, previous, not_ready = since, before.data if before else {}, 0.0
    for event in states:
        if previous.get("availability") == "not_ready" and previous.get("activity") in (
            "idle",
            "not_responding",
        ):
            not_ready += (event.at - cursor).total_seconds()
        previous, cursor = event.data, event.at
    if previous.get("availability") == "not_ready" and previous.get("activity") in ("idle", "not_responding"):
        not_ready += (now - cursor).total_seconds()
    return {
        "calls": len(legs),
        "average_talk_seconds": talk / len(talks) if talks else None,
        "not_ready_seconds": max(0, not_ready),
    }
