"""GraphQL types of the reports app (definitions: apps.reports.compute and docs/REPORTS.md)."""

import graphene
from graphene.types.generic import GenericScalar


class QueueReportType(graphene.ObjectType):
    queue = graphene.String()
    offered = graphene.Int()
    answered = graphene.Int()
    answered_within = graphene.Int(description="Answered within the queue's service-level seconds")
    target_seconds = graphene.Int()
    target_percent = graphene.Int()
    service_level_percent = graphene.Float(description="Answered within target, of all offered")
    met_target = graphene.Boolean()
    abandoned = graphene.Int()
    short_abandoned = graphene.Int(description="Abandoned within the first 5 seconds (included in abandoned)")
    abandon_rate_percent = graphene.Float()
    overflowed = graphene.Int(description="Left unanswered for another queue")
    voicemail = graphene.Int()
    average_speed_of_answer = graphene.Float(description="Seconds")
    longest_wait = graphene.Float(description="Seconds")
    average_talk = graphene.Float(description="Seconds, hold included")
    average_wrapup = graphene.Float(description="Seconds")
    average_handle_time = graphene.Float(description="Seconds: talk + wrap-up")


class AgentReportType(graphene.ObjectType):
    agent = graphene.String(description="The agent's uid")
    name = graphene.String()
    answered = graphene.Int()
    average_handle_time = graphene.Float(description="Seconds")
    seconds = GenericScalar(
        description="Seconds per state: available, ringing, talking, on_hold, wrapup, not_responding, "
        "'not_ready: <reason>'"
    )


class AiReportType(graphene.ObjectType):
    ai_agent = graphene.String(description="The AI agent's uid")
    name = graphene.String()
    calls = graphene.Int()
    resolved = graphene.Int()
    containment_percent = graphene.Float(description="Resolved by the AI, of its calls")
    handed_off = graphene.Int()
    reasons = GenericScalar(description="Handoffs per reason")
    failed = graphene.Int()
    caller_left = graphene.Int()
    average_seconds = graphene.Float(description="Seconds the caller spent with the AI")


class ExperienceReportType(graphene.ObjectType):
    """One HEART metric of UX_PRINCIPLES.md section 6, with what it is based on."""

    user = graphene.String()
    goal = graphene.String()
    metric = graphene.String()
    value = graphene.String()
    based_on = graphene.String()


class ReportType(graphene.ObjectType):
    since = graphene.DateTime()
    until = graphene.DateTime()
    queues = graphene.List(QueueReportType)
    total = graphene.Field(QueueReportType)
    agents = graphene.List(AgentReportType)
    ai = graphene.List(AiReportType)
    experience = graphene.List(ExperienceReportType, description="How well AlgoDial works for its users")
