"""ROUTING_DESIGN.md: skills with kinds, rule steps with minimum levels, the language floor (T10)."""

import pytest
from django.test import Client

from apps.agents import services as agents
from apps.core.exceptions import DuplicateError, ValidationError
from apps.queues import services as queues

pytestmark = pytest.mark.django_db

LANGUAGE = "$language"


@pytest.fixture
def skills():
    for name, kind in (
        ("swahili", "language"),
        ("english", "language"),
        ("claims", "topic"),
        ("tier2", "tier"),
    ):
        agents.create_skill(actor=None, name=name, kind=kind)


def test_skills_have_a_kind_and_a_usable_name(skills):
    assert agents.create_skill(actor=None, name="membership", kind="topic").kind == "topic"
    for name, kind in (("Customer Care", "topic"), ("membership2", "colour")):
        with pytest.raises(ValidationError):
            agents.create_skill(actor=None, name=name, kind=kind)
    with pytest.raises(DuplicateError):
        agents.create_skill(actor=None, name="SWAHILI", kind="language")


def test_an_agent_gets_levels_from_1_to_10(skills):
    agent = agents.create_agent(actor=None, number="7001", name="Asha")
    agents.set_agent_skills(agent=agent, levels={"swahili": 9, "claims": 4})
    for bad in ({"swahili": 11}, {"swahili": 0}, {"french": 5}):
        with pytest.raises(ValidationError):
            agents.set_agent_skills(agent=agent, levels=bad)


def test_a_rule_set_is_steps_of_minimum_levels(skills):
    rule_set = queues.save_skill_rule_set(
        actor=None,
        name="R_CLAIMS",
        steps=[
            (30, {LANGUAGE: 6, "claims": 7}),
            (90, {LANGUAGE: 5, "claims": 4}),
            (None, {LANGUAGE: 4, "claims": 1}),
        ],
    )
    assert queues.rule_steps(rule_set) == [
        (30, {LANGUAGE: 6, "claims": 7}),
        (90, {LANGUAGE: 5, "claims": 4}),
        (None, {LANGUAGE: 4, "claims": 1}),
    ]


def test_t10_a_step_without_the_language_floor_is_refused(skills):
    for steps in (
        [(None, {"claims": 1})],  # no $language at all
        [(30, {LANGUAGE: 6, "claims": 7}), (None, {"claims": 1})],  # one step forgot it
        [(None, {LANGUAGE: 3, "claims": 1})],  # below 4
    ):
        with pytest.raises(ValidationError, match="language"):
            queues.save_skill_rule_set(actor=None, name="Bad", steps=steps)


def test_steps_must_end_with_forever_and_name_known_skills_at_valid_levels(skills):
    for steps in (
        [(30, {LANGUAGE: 6})],  # never says what happens after 30 s
        [(None, {LANGUAGE: 6}), (None, {LANGUAGE: 4})],  # two forever steps
        [(60, {LANGUAGE: 6}), (30, {LANGUAGE: 5}), (None, {LANGUAGE: 4})],  # out of order
        [(None, {LANGUAGE: 4, "fraud": 2})],  # unknown skill
        [(None, {LANGUAGE: 4, "claims": 11})],  # level out of range
    ):
        with pytest.raises(ValidationError):
            queues.save_skill_rule_set(actor=None, name="Bad", steps=steps)


def test_the_admin_refuses_a_rule_set_without_the_language_floor(admin, skills):
    browser = Client()
    browser.force_login(admin)
    response = browser.post(
        "/admin/queues/skillruleset/add/",
        {
            "name": "No language",
            "active": "on",
            "requirements-TOTAL_FORMS": "1",
            "requirements-INITIAL_FORMS": "0",
            "requirements-0-until_seconds": "",
            "requirements-0-skill": "claims",
            "requirements-0-min_level": "3",
        },
    )
    assert response.status_code == 200 and b"language" in response.content
    assert not queues.SkillRuleSet.objects.filter(name="No language").exists()
