"""Pure call-control logic: state in, event in, new state and actions out.

Nothing here does I/O or knows Django: no sockets, no asyncio, no ORM. That keeps
every decision testable with plain function calls and replayable in brain/sim.
The call and agent state machines arrive in M2.
"""
