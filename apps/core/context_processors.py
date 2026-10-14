"""Workspace links derived from the registered permission hook, without importing another app."""

from django.urls import reverse

from apps.core.hooks import resolve_permissions


def workspace_navigation(request):
    user = request.user
    codes = resolve_permissions(user, request)
    all_access = user.is_authenticated and user.is_superuser
    links = []
    if all_access or "can_take_calls" in codes:
        links.append({"label": "Agent workspace", "url": reverse("agent_page")})
    if all_access or codes.intersection(
        {"can_monitor_calls", "can_whisper_calls", "can_barge_calls", "can_force_agent_state"}
    ):
        links.append({"label": "Supervisor workspace", "url": reverse("supervisor_page")})
    return {"workspace_links": links}
