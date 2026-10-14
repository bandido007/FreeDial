"""A voicemail message, played from the admin: fetched from Asterisk's stored recordings over ARI."""

import requests
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404

from apps.calls.models import CallbackTask
from apps.calls.permissions import LISTEN_RECORDINGS
from apps.core.hooks import resolve_permissions


def fetch_recording(name: str) -> bytes | None:
    """The WAV Asterisk stored under ``name``, or None if it has none."""
    response = requests.get(
        f"{settings.ARI_URL}/recordings/stored/{name}/file",
        auth=(settings.ARI_USER, settings.ARI_PASSWORD),
        timeout=10,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content


@login_required
def callback_recording(request, pk: int):
    user = request.user
    if not (user.is_superuser or LISTEN_RECORDINGS in resolve_permissions(user, request)):
        raise PermissionDenied
    task = get_object_or_404(CallbackTask, pk=pk)
    audio = fetch_recording(task.recording) if task.recording else None
    if audio is None:
        raise Http404("No message was left.")
    response = HttpResponse(audio, content_type="audio/wav")
    response["Cache-Control"] = "private, no-store"
    return response
