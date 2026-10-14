"""An AI agent's settings, read from the database at the start of each call (so an admin change
reaches the next call without a restart, rule 10). The only place the worker touches the ORM."""

from asgiref.sync import sync_to_async

from aiworker.core.settings import AgentSettings, Route


def _load(uid: str) -> AgentSettings | None:
    from django.db import close_old_connections

    from apps.ai.models import AiAgent

    close_old_connections()  # a long-running worker: never use a connection the database dropped

    agent = AiAgent.objects.filter(uid=uid, deleted=False, active=True).first()
    if agent is None:
        return None
    routes = tuple(
        Route(
            queue=route.queue.name,
            when=route.when,
            skills={route.skill.name: route.min_level} if route.skill and not route.skill.deleted else {},
        )
        for route in agent.routes.select_related("queue", "skill").filter(queue__deleted=False)
    )
    greetings = {
        language: text.strip()
        for language, text in (("swahili", agent.greeting_swahili), ("english", agent.greeting_english))
        if text.strip()
    }
    return AgentSettings(
        uid=str(agent.uid),
        name=agent.name,
        instructions=agent.instructions,
        greetings=greetings or {"english": f"Hello, this is {agent.name}. How can I help?"},
        routes=routes,
        sensitive_topics=tuple(agent.topics()),
        max_failed_turns=agent.max_failed_turns,
        voice=agent.voice,
        stt_model=agent.stt_model,
        llm_model=agent.llm_model,
        tts_model=agent.tts_model,
    )


load = sync_to_async(_load, thread_sensitive=False)
