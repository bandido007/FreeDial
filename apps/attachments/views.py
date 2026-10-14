"""REST endpoints for files.

==============================  ==============================================
``POST /files/upload/``         store a file (multipart ``file``, or JSON with
                                ``base64_string``)
``POST /files/download/``       a stored file as base64 (preview in the page)
``GET  /files/<uid>/``          a stored file as a stream (download)
``POST /files/public-image/``   a public file as base64, without signing in
``DELETE /files/<uid>/``        remove a file
==============================  ==============================================

Authentication and permission come from the project defaults (OAuth2 bearer
token, first-party scope, unrestricted session). **Every read also asks the
access policy of the file's purpose** — knowing a file's address is not enough
to open it.

All endpoints speak one JSON contract (``{ success, message, ... }``). Errors
are mapped to friendly, user-safe messages.
"""

import logging

from django.http import FileResponse, HttpResponseRedirect
from oauth2_provider.contrib.rest_framework import TokenHasReadWriteScope
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import SAFE_METHODS, AllowAny
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView

from apps.attachments import selectors, services
from apps.attachments.serializers import FileReferenceSerializer, UploadFileSerializer
from apps.core.exceptions import ApplicationError, NotFoundError, ThrottledError
from apps.core.ratelimit import throttle_scope
from apps.core.utils import get_client_ip

logger = logging.getLogger(__name__)

SERVER_ERROR = "Something went wrong on our side. Please try again."


def _first_error_message(serializer) -> str:
    """Pull one human-readable message out of serializer errors."""
    for field, errors in serializer.errors.items():
        message = errors[0] if isinstance(errors, list) else errors
        if field in ("base64_string", "file_name", "file"):
            return "Please choose a file to upload."
        if field in ("file_path", "uid"):
            return "No file was specified."
        return str(message)
    return "Invalid request."


def _refused(message: str, http_status=status.HTTP_400_BAD_REQUEST, **extra) -> Response:
    return Response({"success": False, "message": message, **extra}, status=http_status)


def _describe(attachment, request) -> dict:
    return {
        "file_id": str(attachment.uid),
        "uid": str(attachment.uid),
        "file_path": attachment.path,
        "url": request.build_absolute_uri(f"/files/{attachment.uid}/"),
        "name": attachment.name,
        "file_name": attachment.name,
        "mime": attachment.mime_type,
        "size": attachment.size,
        "purpose": attachment.purpose,
        "is_public": attachment.is_public,
    }


def _find(data):
    if data.get("uid"):
        return selectors.get_attachment(data["uid"])
    return selectors.get_attachment_by_path(data["file_path"])


class UploadFileAPIView(APIView):
    """POST /files/upload/ — store a file, return its ID and media path.

    Multipart: a ``file`` field (streamed; the way to send anything large).
    JSON: ``{ "file_type": "Image", "base64_string": "<b64>", "file_name": "me.png" }``.
    Both accept ``purpose``, ``definition_uid``, ``entity_type``, ``entity_uid``.
    """

    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        serializer = UploadFileSerializer(data=request.data)
        if not serializer.is_valid():
            return _refused(_first_error_message(serializer))
        data = serializer.validated_data
        uploaded = data.get("file")
        try:
            if uploaded is not None:
                # Refuse an oversized upload before reading it into memory.
                ceiling = services.max_bytes("Document")
                if uploaded.size > ceiling:
                    return _refused(
                        f"That file is too large. Please keep it under {ceiling // services.MEGABYTE} MB.",
                        code=services.TOO_LARGE,
                    )
                content = uploaded.read()
                file_name = data.get("file_name") or uploaded.name
            else:
                content = services.decode_base64(data["base64_string"])
                file_name = data["file_name"]
            attachment = services.store(
                content=content,
                file_name=file_name,
                file_type=data.get("file_type") or "Image",
                user=request.user,
                purpose=data.get("purpose") or "general",
                definition_uid=data.get("definition_uid"),
                entity_type=data.get("entity_type") or "",
                entity_uid=data.get("entity_uid"),
                request=request,
            )
        except ApplicationError as exc:
            return _refused(exc.message, code=exc.code)
        except Exception:  # noqa: BLE001
            logger.exception("[UploadFileAPIView] unexpected error")
            return _refused(SERVER_ERROR, status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(
            {
                "success": True,
                "message": "Your file was uploaded successfully.",
                **_describe(attachment, request),
            },
            status=status.HTTP_201_CREATED,
        )


class DownloadFileAPIView(APIView):
    """POST /files/download/ — return a stored file as base64 for preview.

    Body: ``{ "uid": "<file id>" }`` or ``{ "file_path": "/media/…" }``.
    """

    def post(self, request):
        serializer = FileReferenceSerializer(data=request.data)
        if not serializer.is_valid():
            return _refused(_first_error_message(serializer))
        try:
            attachment = services.readable(_find(serializer.validated_data), request.user)
            file_name, mime, encoded = services.read_as_base64(attachment)
        except NotFoundError as exc:
            return _refused(exc.message, status.HTTP_404_NOT_FOUND)
        except ApplicationError as exc:
            return _refused(exc.message, code=exc.code)
        except Exception:  # noqa: BLE001
            logger.exception("[DownloadFileAPIView] unexpected error")
            return _refused(SERVER_ERROR, status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(
            {
                "success": True,
                "message": "File retrieved successfully.",
                **_describe(attachment, request),
                "base64_string": encoded,
            },
            status=status.HTTP_200_OK,
        )


class FirstPartyOrOpenRead(TokenHasReadWriteScope):
    """Reads are open — the file's own access policy decides, so a public file
    is served to anyone (an ``<img src>`` carries no token) and a private one
    answers 404 — while every other method needs the project's default
    first-party session."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return all(
            permission().has_permission(request, view)
            for permission in api_settings.DEFAULT_PERMISSION_CLASSES
        )


class FileAPIView(APIView):
    """GET /files/<uid>/ — stream a stored file · DELETE — remove it."""

    permission_classes = [FirstPartyOrOpenRead]

    def get(self, request, uid):
        try:
            attachment = services.readable(selectors.get_attachment(uid), request.user)
            direct = services.signed_url(attachment)
            if direct:
                return HttpResponseRedirect(direct)
            handle = services.open_attachment(attachment)
        except NotFoundError as exc:
            return _refused(exc.message, status.HTTP_404_NOT_FOUND)
        except Exception:  # noqa: BLE001
            logger.exception("[FileAPIView] unexpected error")
            return _refused(SERVER_ERROR, status.HTTP_500_INTERNAL_SERVER_ERROR)

        inline = attachment.mime_type.startswith("image/") or attachment.mime_type == "application/pdf"
        response = FileResponse(
            handle,
            as_attachment=not inline,
            filename=attachment.name,
            content_type=attachment.mime_type or "application/octet-stream",
        )
        # The browser must never guess a type other than the one declared.
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, max-age=0, no-store"
        return response

    def delete(self, request, uid):
        try:
            services.delete_attachment(uid=uid, user=request.user, request=request)
        except NotFoundError as exc:
            return _refused(exc.message, status.HTTP_404_NOT_FOUND)
        except ApplicationError as exc:
            return _refused(exc.message, code=exc.code)
        return Response({"success": True, "message": "The file was removed."}, status=status.HTTP_200_OK)


class PublicImageAPIView(APIView):
    """POST /files/public-image/ — a **public** file as base64, no sign-in.

    Serves only files that were published as public (a logo, a banner). A
    private file answers exactly like a missing one.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = FileReferenceSerializer(data=request.data)
        if not serializer.is_valid():
            return _refused(_first_error_message(serializer))
        try:
            throttle_scope("public-file", get_client_ip(request))
            attachment = _find(serializer.validated_data)
            if not attachment.is_public:
                raise NotFoundError("We couldn't find that file. It may have been removed.")
            file_name, mime, encoded = services.read_as_base64(attachment)
        except NotFoundError as exc:
            return _refused(exc.message, status.HTTP_404_NOT_FOUND)
        except ThrottledError as exc:
            return _refused(exc.message, status.HTTP_429_TOO_MANY_REQUESTS, code=exc.code)
        except ApplicationError as exc:
            return _refused(exc.message, code=exc.code)
        except Exception:  # noqa: BLE001
            logger.exception("[PublicImageAPIView] unexpected error")
            return _refused(SERVER_ERROR, status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(
            {
                "success": True,
                "message": "File retrieved successfully.",
                "file_name": file_name,
                "file_path": attachment.path,
                "mime": mime,
                "base64_string": encoded,
            },
            status=status.HTTP_200_OK,
        )
