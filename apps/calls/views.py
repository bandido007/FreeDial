"""Recordings played from the admin, only to people allowed to listen (``can_listen_recordings``).

* A voicemail message: fetched from Asterisk's stored recordings over ARI.
* A call's stereo recording (M7): our own WAV, kept after the call by :mod:`apps.calls.recordings`.
"""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404

from apps.calls.asterisk import fetch_recording
from apps.calls.models import Call, CallbackTask, RecordingStatus
from apps.calls.permissions import LISTEN_RECORDINGS
from apps.core.hooks import resolve_permissions


def _may_listen(request) -> None:
    user = request.user
    if not (user.is_superuser or LISTEN_RECORDINGS in resolve_permissions(user, request)):
        raise PermissionDenied


@login_required
def callback_recording(request, pk: int):
    _may_listen(request)
    task = get_object_or_404(CallbackTask, pk=pk)
    audio = fetch_recording(task.recording) if task.recording else None
    if audio is None:
        raise Http404("No message was left.")
    response = HttpResponse(audio, content_type="audio/wav")
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
def call_recording(request, pk: int):
    _may_listen(request)
    call = get_object_or_404(Call, pk=pk)
    if call.recording_status != RecordingStatus.READY or not call.recording:
        raise Http404("This call has no recording.")
    response = FileResponse(default_storage.open(call.recording, "rb"), content_type="audio/wav")
    response["Cache-Control"] = "private, no-store"
    return response
