"""Where the brain's configuration comes from.

The ORM is used here and only here, and only when loading: at start and when a
``config.changed`` command arrives. Never while handling a call event.
M0 needs only the connection settings; queues and agents load from M2 on.
"""

from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class BrainSettings:
    ari_url: str
    ari_user: str
    ari_password: str
    ari_app: str
    bus_redis_url: str
    commands_stream: str
    events_stream: str

    @classmethod
    def from_django(cls) -> "BrainSettings":
        return cls(
            ari_url=settings.ARI_URL,
            ari_user=settings.ARI_USER,
            ari_password=settings.ARI_PASSWORD,
            ari_app=settings.ARI_APP,
            bus_redis_url=settings.BUS_REDIS_URL,
            commands_stream=settings.BUS_COMMANDS_STREAM,
            events_stream=settings.BUS_EVENTS_STREAM,
        )
