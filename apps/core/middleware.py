"""Request middleware: request ID, acting user, security headers."""

import re

from django.conf import settings

from apps.core.context import (
    open_after_request_queue,
    reset_current_user,
    reset_request_id,
    run_after_request_queue,
    set_current_user_from,
    set_request_id,
)
from apps.core.utils import generate_uuid7, strip_mount_prefix

REQUEST_ID_HEADER = "X-Request-ID"
# An inbound ID is echoed into logs and response headers, so it is accepted
# only when it cannot carry anything but an identifier.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{7,63}$")


class RequestIdMiddleware:
    """Give every request an ID and hand it back in the response.

    A well-formed ``X-Request-ID`` from the caller (the SPA, a gateway, another
    service) is kept, so one ID follows a user action across systems. Anything
    else is replaced with a fresh UUIDv7.

    As the outermost of the project's middleware it also runs the work that
    was postponed with :func:`apps.core.context.after_request` — after the
    view has returned, so after every transaction the request opened.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _VALID_REQUEST_ID.fullmatch(incoming) else str(generate_uuid7())
        request.request_id = request_id
        token = set_request_id(request_id)
        queue = open_after_request_queue()
        try:
            response = self.get_response(request)
        finally:
            run_after_request_queue(queue)
            reset_request_id(token)
        response[REQUEST_ID_HEADER] = request_id
        return response


class CurrentUserMiddleware:
    """Expose ``request.user`` to the ORM layer for the lifetime of the request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = set_current_user_from(request)  # project: lazy, safe under ASGI
        try:
            return self.get_response(request)
        finally:
            # Never leak the user into the next request served by this worker.
            reset_current_user(token)


class SecurityHeadersMiddleware:
    """Add ``Content-Security-Policy`` and ``Permissions-Policy``.

    The API serves JSON, so it gets the strictest policy (nothing may load,
    nothing may frame it). The HTML surfaces — Django admin and, in
    development, GraphiQL — get a policy that lets their own assets run.
    A header already set by a view is left alone.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.api_policy = getattr(settings, "CONTENT_SECURITY_POLICY", "")
        self.html_policy = getattr(settings, "CONTENT_SECURITY_POLICY_HTML", "")
        self.html_prefixes = tuple(getattr(settings, "CONTENT_SECURITY_POLICY_HTML_PREFIXES", ()))
        self.permissions_policy = getattr(settings, "PERMISSIONS_POLICY", "")

    def __call__(self, request):
        response = self.get_response(request)
        policy = self.html_policy if self._is_html_surface(request, response) else self.api_policy
        if policy and "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = policy
        if self.permissions_policy and "Permissions-Policy" not in response:
            response["Permissions-Policy"] = self.permissions_policy
        return response

    def _is_html_surface(self, request, response) -> bool:
        if "text/html" not in response.get("Content-Type", ""):
            return False
        return strip_mount_prefix(request.path_info).startswith(self.html_prefixes)
