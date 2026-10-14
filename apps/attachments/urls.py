"""File routes (mounted under /files/ by config.urls)."""

from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from apps.attachments.views import (
    DownloadFileAPIView,
    FileAPIView,
    PublicImageAPIView,
    UploadFileAPIView,
)

app_name = "attachments"

urlpatterns = [
    path("upload/", csrf_exempt(UploadFileAPIView.as_view()), name="upload"),
    path("download/", csrf_exempt(DownloadFileAPIView.as_view()), name="download"),
    path("public-image/", csrf_exempt(PublicImageAPIView.as_view()), name="public-image"),
    path("<uuid:uid>/", csrf_exempt(FileAPIView.as_view()), name="file"),
]
