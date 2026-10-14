"""Writes and rules of the agents app."""

import re

from django.db import transaction

from apps.agents.models import Agent
from apps.core.exceptions import DuplicateError, NotFoundError, ValidationError

NOT_FOUND = 10200
NUMBER_TAKEN = 10201
BAD_NUMBER = 10202
USER_TAKEN = 10203

NUMBER_PATTERN = re.compile(r"^\d{2,20}$")


def get_agent(uid) -> Agent:
    agent = Agent.objects.filter(uid=uid).first()
    if agent is None:
        raise NotFoundError("Agent not found.", code=NOT_FOUND)
    return agent


def validate_agent(*, number, name, user=None, except_pk=None) -> tuple[str, str]:
    number = (number or "").strip()
    name = " ".join((name or "").split())
    if not NUMBER_PATTERN.match(number):
        raise ValidationError("An agent number is 2 to 20 digits.", code=BAD_NUMBER)
    if not name:
        raise ValidationError("An agent needs a name.", code=9005)
    if Agent.objects.filter(number=number).exclude(pk=except_pk).exists():
        raise DuplicateError(f"Agent {number} already exists.", code=NUMBER_TAKEN)
    if user is not None and Agent.all_objects.filter(user=user).exclude(pk=except_pk).exists():
        raise DuplicateError("This user is already another agent.", code=USER_TAKEN)
    return number, name


@transaction.atomic
def create_agent(*, actor, number, name, user=None) -> Agent:
    number, name = validate_agent(number=number, name=name, user=user)
    return Agent.objects.create(number=number, name=name, user=user, created_by=actor)


@transaction.atomic
def update_agent(*, uid, actor, number=None, name=None, user=None, clear_user=False) -> Agent:
    agent = get_agent(uid)
    new_user = None if clear_user else (user or agent.user)
    agent.number, agent.name = validate_agent(
        number=agent.number if number is None else number,
        name=agent.name if name is None else name,
        user=new_user,
        except_pk=agent.pk,
    )
    agent.user = new_user
    agent.updated_by = actor
    agent.save()
    return agent


@transaction.atomic
def delete_agent(*, uid) -> Agent:
    agent = get_agent(uid)
    agent.user = None  # the user may become another agent later
    agent.save(update_fields=["user"])
    agent.delete()  # soft delete; the brain logs the agent out once any call ends
    return agent
