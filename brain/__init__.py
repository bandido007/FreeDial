"""The brain: AlgoDial's call control.

    core/       pure decisions: call and agent state machines (no I/O, no Django)
    adapters/   the outside world: ARI, the Redis Streams bus, config loading
    service.py  wires core to adapters; started by ``manage.py run_brain``
    sim/        a fake ARI and scenarios with many virtual agents and calls

import-linter enforces the direction: management -> service -> adapters -> core.
"""
