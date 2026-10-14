"""GraphQL mutations of the teams app.

A mutation checks that the arguments are there, calls one service, and wraps
the outcome. Rules live in ``services.py``.
"""

import graphene

from apps.core.decorators import has_permission, log_errors_and_warnings
from apps.core.graphql.context import current_user
from apps.core.graphql.response import Response
from apps.core.utils import Utils
from apps.teams import selectors, services
from apps.teams.inputs import TeamInput
from apps.teams.permissions import MANAGE_TEAMS
from apps.teams.types import TeamType


def _members(uids):
    return None if uids is None else selectors.users_by_uid(uids)


class SaveTeam(graphene.Mutation):
    """Create (no uid) or update (uid) a team. Member lists, when given, replace the current ones."""

    class Arguments:
        input = TeamInput(required=True)

    Output = Response.get_response_type(TeamType)

    @classmethod
    @has_permission(permissions=[MANAGE_TEAMS])
    @log_errors_and_warnings
    def mutate(cls, root, info, input):
        actor = current_user(info)
        supervisors, agents = _members(input.supervisor_uids), _members(input.agent_uids)
        if input.uid:
            team = services.update_team(
                uid=input.uid,
                actor=actor,
                name=input.name,
                description=input.description,
                supervisors=supervisors,
                agents=agents,
            )
            return Response.build_response(code=9001, info=info, data=team)
        if not Utils.empty_and_null_checker(input, ["name"]):
            return Response.build_response(code=9005, info=info)
        team = services.create_team(
            actor=actor,
            name=input.name,
            description=input.description or "",
            supervisors=supervisors or [],
            agents=agents or [],
        )
        return Response.build_response(code=9001, info=info, data=team)


class DeleteTeam(graphene.Mutation):
    class Arguments:
        uid = graphene.UUID(required=True)

    Output = Response.get_response_type(TeamType)

    @classmethod
    @has_permission(permissions=[MANAGE_TEAMS])
    @log_errors_and_warnings
    def mutate(cls, root, info, uid):
        return Response.build_response(code=9001, info=info, data=services.delete_team(uid=uid))


class Mutation(graphene.ObjectType):
    save_team = SaveTeam.Field(description="Create or update a team. permissions=['can_manage_teams']")
    delete_team = DeleteTeam.Field(description="Remove a team. permissions=['can_manage_teams']")
