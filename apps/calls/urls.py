from django.urls import path

from apps.calls.views import callback_recording

urlpatterns = [
    path("calls/callbacks/<int:pk>/recording", callback_recording, name="callback_recording"),
]
