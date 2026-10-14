"""GraphQL types of the queues app."""

import graphene


class QueueStatsType(graphene.ObjectType):
    """A queue's calls over a period, against its service-level target."""

    queue = graphene.String()
    since = graphene.DateTime()
    until = graphene.DateTime()
    offered = graphene.Int()
    answered = graphene.Int()
    abandoned = graphene.Int()
    overflowed = graphene.Int()
    answered_within_target = graphene.Int()
    target_seconds = graphene.Int()
    target_percent = graphene.Int()
    service_level_percent = graphene.Float(description="Answered within the target, of all calls offered")
    met_target = graphene.Boolean()
    average_wait_seconds = graphene.Float()
    longest_wait_seconds = graphene.Float()
    average_talk_seconds = graphene.Float()
