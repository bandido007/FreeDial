"""Pure call-control logic: state in, event in, new state and actions out.

Nothing here does I/O or knows Django: no sockets, no asyncio, no ORM. That keeps
every decision testable with plain function calls and replayable from a recording.

* ``brain``   — the decisions: calls, offers, cleanup, reconcile.
* ``agents``  — an agent's availability and activity.
* ``actions`` — what a decision asks the service to do.
* ``config``  — agents, queues and entry points as plain data.
"""
