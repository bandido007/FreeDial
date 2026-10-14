"""Restricted sessions: signed in, but not yet allowed to use the API.

An account that must choose a new password, or must set up two-factor
authentication, *is* signed in — it has to be, to do either. Until it complies
the API answers only the operations it needs for that, and refuses everything
else with the code that tells the client which screen to show (8015 / 8016).

Two enforcement points, wired from settings:

* :class:`RestrictedSessionMiddleware` — a graphene middleware
  (``GRAPHENE["MIDDLEWARE"]``) for GraphQL;
* :class:`UnrestrictedSession` — a REST framework permission
  (``REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]``) for the REST endpoints.
"""

from rest_framework.permissions import BasePermission

from apps.authentication.gates import restriction
from apps.core.decorators import failure_envelope
from apps.core.exceptions import PermissionDeniedError
from apps.core.hooks import report_api_error

# Root fields a restricted session may still call: find out what is required,
# comply with it, read what the sign-in screens need, and sign out.
ALWAYS_ALLOWED = frozenset(
    {
        # who am I / what must I do
        "meQuery",
        "getRegistrationStatus",
        "getSignInProviders",
        # comply: password
        "changeMyPassword",
        # comply: two-factor
        "beginMfaEnrollment",
        "confirmMfaEnrollment",
        # consent may be pending too
        "acceptConsents",
        # leave
        "revokeAllMySessions",
        "revokeMySession",
        "getMySessions",
        # public operations stay public
        "register",
        "validateRegistrationOtp",
        "resendRegistrationOtp",
        "requestPasswordReset",
        "setPassword",
    }
)


class RestrictedSessionMiddleware:
    """Refuse a restricted session every root field that is not allow-listed."""

    def resolve(self, next_resolver, root, info, **kwargs):
        # Only the operation's top-level fields are gated; nested fields belong
        # to an operation that was already allowed or refused.
        if info.path.prev is not None:
            return next_resolver(root, info, **kwargs)
        if info.field_name in ALWAYS_ALLOWED or info.field_name.startswith("__"):
            return next_resolver(root, info, **kwargs)

        request = info.context
        required = restriction(getattr(request, "user", None), request)
        if required is None:
            return next_resolver(root, info, **kwargs)

        code, message = required
        report_api_error(PermissionDeniedError(message, code=code), operation=info.field_name, handled=True)
        return failure_envelope(info, code, message)


class UnrestrictedSession(BasePermission):
    """REST counterpart of :class:`RestrictedSessionMiddleware`."""

    def has_permission(self, request, view):
        required = restriction(getattr(request, "user", None), request)
        if required is None:
            return True
        self.message = required[1]
        self.code = str(required[0])
        return False
