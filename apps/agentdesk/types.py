"""GraphQL types of the agents app: what the agent page reads."""

import graphene


class AgentLiveStateType(graphene.ObjectType):
    """The agent's state as the brain last saved it."""

    availability = graphene.String(description="logged_out, ready or not_ready")
    activity = graphene.String(description="idle, offered, talking, on_hold, wrapup or not_responding")
    reason = graphene.String()
    call = graphene.String(description="The call the agent is on (its linkedid)")
    wrapup_call = graphene.String(description="The call the agent is wrapping up")
    endpoint = graphene.String()


class TransferAgentType(graphene.ObjectType):
    uid = graphene.String()
    number = graphene.String()
    name = graphene.String()
    availability = graphene.String()
    activity = graphene.String()


class TransferTargetsType(graphene.ObjectType):
    agents = graphene.List(TransferAgentType)
    queues = graphene.List(graphene.String)


class AgentQueueType(graphene.ObjectType):
    name = graphene.String()
    waiting = graphene.Int()
    longest_wait = graphene.Float()
    ring_timeout = graphene.Int()
    wrapup_seconds = graphene.Int()


class AgentDayType(graphene.ObjectType):
    calls = graphene.Int()
    average_talk_seconds = graphene.Float()
    not_ready_seconds = graphene.Float()


class AgentCallType(graphene.ObjectType):
    call = graphene.String()
    caller = graphene.String()
    caller_name = graphene.String()
    queue = graphene.String()
    language = graphene.String()
    summary = graphene.String()
    ai_reason = graphene.String()
    connected_at = graphene.Float()
    ended_at = graphene.Float()
    offered_at = graphene.Float()
    wait_seconds = graphene.Float()
    ring_timeout = graphene.Int()
    wrapup_seconds = graphene.Int()


class AgentDeskType(graphene.ObjectType):
    uid = graphene.String()
    number = graphene.String()
    name = graphene.String()
    state = graphene.Field(AgentLiveStateType)
    not_ready_reasons = graphene.List(graphene.String)
    wrapup_codes = graphene.List(graphene.String)
    transfer_targets = graphene.Field(TransferTargetsType)
    state_since = graphene.DateTime()
    queues = graphene.List(AgentQueueType)
    my_day = graphene.Field(AgentDayType)
    current_call = graphene.Field(AgentCallType)


class AgentPhoneType(graphene.ObjectType):
    """The browser phone's account for this session. The password works until the next sign-in."""

    sip_id = graphene.String()
    password = graphene.String()
    ws_url = graphene.String(description="Asterisk's SIP WebSocket")
    domain = graphene.String()
    endpoint = graphene.String()
    command_id = graphene.String()


class CommandSentType(graphene.ObjectType):
    """The command the brain was sent; its answer arrives on the live WebSocket."""

    command_id = graphene.String()
