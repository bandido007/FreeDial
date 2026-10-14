from django.urls import path

from apps.agentdesk.views import agent_page

urlpatterns = [
    path("agent/", agent_page, name="agent_page"),
]
