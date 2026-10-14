"""GraphQL types of the supervisor page."""

import graphene
from graphene.types.generic import GenericScalar


class SupervisedAgentType(graphene.ObjectType):
    uid = graphene.String()
    number = graphene.String()
    name = graphene.String()
    availability = graphene.String()
    activity = graphene.String()
    reason = graphene.String()
    call = graphene.String()
    idle_since = graphene.Float()


class QueueNowType(graphene.ObjectType):
    name = graphene.String()
    waiting = graphene.Int()
    longest_wait = graphene.Int(description="Seconds the longest-waiting caller has waited")
    service_level_percent = graphene.Float(description="Today")
    target_percent = graphene.Int()
    target_seconds = graphene.Int()
    offered = graphene.Int()
    answered = graphene.Int()


class SupervisorDeskType(graphene.ObjectType):
    user = graphene.String()
    name = graphene.String()
    can = graphene.List(graphene.String, description="monitor, whisper, barge, force")
    agents = graphene.List(SupervisedAgentType)
    calls = graphene.List(GenericScalar, description="Live calls of your teams, as the brain describes them")
    queues = graphene.List(QueueNowType)
    not_ready_reasons = graphene.List(graphene.String)
