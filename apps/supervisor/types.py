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
    state_since = graphene.Float()
    queues = graphene.List(graphene.String)


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
    can_manage = graphene.Boolean()
    can = graphene.List(graphene.String, description="monitor, whisper, barge, force")
    agents = graphene.List(SupervisedAgentType)
    calls = graphene.List(GenericScalar, description="Live calls of your teams, as the brain describes them")
    queues = graphene.List(QueueNowType)
    not_ready_reasons = graphene.List(graphene.String)
    teams = graphene.List(GenericScalar)
    reason_limits = graphene.Field(GenericScalar)
    long_call_seconds = graphene.Int()


class AgentHistoryType(graphene.ObjectType):
    at = graphene.DateTime()
    type = graphene.String()
    description = graphene.String()


class MeasureNumbersType(graphene.ObjectType):
    """Section 6 signals as numbers; the page writes the words (English or Swahili)."""

    answered = graphene.Int()
    missed = graphene.Int()
    median_answer_seconds = graphene.Float()
    wrapups = graphene.Int()
    median_wrapup_seconds = graphene.Float()
    corrections = graphene.Int()
    transfers = graphene.Int()
    transfers_completed = graphene.Int()


class TeamMeasuresTotalType(MeasureNumbersType):
    summaries_shown = graphene.Int(description="AI summaries agents saw (one per call)")
    summaries_wrong = graphene.Int(description="Of those, flagged wrong by the agent")
    over_limit = graphene.Int(description="Times an agent stayed Not Ready past the reason's limit")
    acted_on = graphene.Int(description="Of those, a supervisor changed the agent's state")
    ended_by_agent = graphene.Int()
    median_minutes_to_action = graphene.Float()


class AgentMeasuresType(MeasureNumbersType):
    uid = graphene.String()
    number = graphene.String()
    name = graphene.String()


class TeamMeasuresType(graphene.ObjectType):
    since = graphene.DateTime()
    until = graphene.DateTime()
    team = graphene.Field(TeamMeasuresTotalType)
    agents = graphene.List(AgentMeasuresType)
