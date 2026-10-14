import pytest

from contracts import messages as m
from contracts.envelope import Command, ContractError, Event


def test_a_known_type_must_carry_its_fields():
    with pytest.raises(ContractError, match="endpoint"):
        m.check(Command(type=m.AGENT_LOGIN, data={"agent": "a"}))
    m.check(Command(type=m.AGENT_LOGIN, data={"agent": "a", "endpoint": "PJSIP/1001"}))


def test_extra_fields_are_fine_and_unknown_types_pass_through():
    m.check(Event(type=m.LEG_ENDED, data={"call": "c", "leg": "l", "role": "agent", "cause": 16, "new": 1}))
    m.check(Event(type="something.added.later", data={}))


def test_every_type_name_is_dotted_and_unique():
    names = list(m.COMMANDS) + list(m.EVENTS)
    assert len(names) == len(set(names))
    assert all("." in name for name in names)
