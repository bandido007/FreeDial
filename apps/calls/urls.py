from django.urls import path

from apps.calls.views import call_recording, callback_recording

urlpatterns = [
    path("calls/callbacks/<int:pk>/recording", callback_recording, name="callback_recording"),
    path("calls/<int:pk>/recording", call_recording, name="call_recording"),
]
