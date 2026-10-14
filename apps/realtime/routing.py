from django.urls import path

from apps.realtime.consumers import AgentConsumer

websocket_urlpatterns = [
    path("ws/agent", AgentConsumer.as_asgi()),
]
