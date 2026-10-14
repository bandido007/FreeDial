"""GraphQL output types of the teams app."""

import graphene
from graphene_django import DjangoObjectType

from apps.teams.models import Team
from apps.users.types import UserType


class TeamType(DjangoObjectType):
    supervisors = graphene.List(UserType)
    agents = graphene.List(UserType)
    created_by_name = graphene.String()

    class Meta:
        model = Team
        fields = ("uid", "name", "description", "created_at", "updated_at")

    def resolve_supervisors(self, info):
        return self.supervisors.all()

    def resolve_agents(self, info):
        return self.agents.all()

    def resolve_created_by_name(self, info):
        return self.created_by.get_full_name() if self.created_by_id else ""
