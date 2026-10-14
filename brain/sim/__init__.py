"""Simulation: a fake Asterisk (ARI) and a fake bus, to drive the real brain without either.

``FakeAri`` behaves like Asterisk 22.11 as recorded on the lab; ``FakeBus`` keeps
events, commands and agent states in memory. The brain's tests run the real
service and core against them.
"""

from brain.sim.fake_ari import FakeAri
from brain.sim.fake_bus import FakeBus

__all__ = ["FakeAri", "FakeBus"]
