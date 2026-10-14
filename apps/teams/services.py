"""Writes and rules of the teams app.

Keyword-only arguments, the rule checked first, the write second, an
``ApplicationError`` subclass raised with a status code when a rule says no.
"""

from django.db import transaction

from apps.core.exceptions import DuplicateError, ValidationError
from apps.teams import selectors
from apps.teams.models import Team

NAME_TAKEN = 10101
BOTH_ROLES = 10102


def _clean_name(name, *, except_pk=None) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise ValidationError("A team needs a name", code=9005)
    if Team.objects.filter(name__iexact=name).exclude(pk=except_pk).exists():
        raise DuplicateError(f"A team called {name} already exists.", code=NAME_TAKEN)
    return name


def check_members(supervisors, agents) -> None:
    both = {user.pk for user in supervisors} & {user.pk for user in agents}
    if both:
        raise ValidationError("Nobody can be a supervisor and an agent of the same team.", code=BOTH_ROLES)


@transaction.atomic
def create_team(*, actor, name, description="", supervisors=(), agents=()) -> Team:
    name = _clean_name(name)
    check_members(supervisors, agents)
    team = Team.objects.create(name=name, description=(description or "").strip(), created_by=actor)
    team.supervisors.set(supervisors)
    team.agents.set(agents)
    return team


@transaction.atomic
def update_team(*, uid, actor, name=None, description=None, supervisors=None, agents=None) -> Team:
    """``supervisors`` / ``agents``, when given, replace the current members."""
    team = selectors.get_team(uid)
    if name is not None:
        team.name = _clean_name(name, except_pk=team.pk)
    if description is not None:
        team.description = description.strip()
    new_supervisors = list(team.supervisors.all()) if supervisors is None else list(supervisors)
    new_agents = list(team.agents.all()) if agents is None else list(agents)
    check_members(new_supervisors, new_agents)
    team.updated_by = actor
    team.save()
    team.supervisors.set(new_supervisors)
    team.agents.set(new_agents)
    return team


@transaction.atomic
def delete_team(*, uid) -> Team:
    team = selectors.get_team(uid)
    team.delete()  # soft delete: the row stays for the trail
    return team
