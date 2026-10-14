"""GraphQL input types of the teams app."""

import graphene


class TeamInput(graphene.InputObjectType):
    uid = graphene.UUID(description="Required when updating")
    name = graphene.String(description="Required when creating")
    description = graphene.String()
    supervisor_uids = graphene.List(graphene.UUID, description="Replaces the supervisors when given")
    agent_uids = graphene.List(graphene.UUID, description="Replaces the agents when given")


class TeamFilterInput(graphene.InputObjectType):
    uid = graphene.UUID()
    search = graphene.String(description="Free text across name and description")
