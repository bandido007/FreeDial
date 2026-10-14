"""REST endpoints for signing in and out.

===============================  ==========================================
``POST /oauth2/access_token``    password and refresh grants (first-party)
``POST /oauth2/revoke_token``    sign out
``POST /oauth2/external/<key>``  exchange an external provider's ID token
``GET  /oauth2/providers``       the external providers to offer (public)
===============================  ==========================================

The token views subclass django-oauth-toolkit's to:
  * wrap the raw OAuth2 payload in the project's envelope
    (``success`` / ``code`` / ``message`` + tokens + user);
  * map OAuth2 error strings onto friendly, translatable status codes;
  * apply the account gates and the second factor;
  * record every attempt in the authentication audit.
"""

import json
import logging
from json import JSONDecodeError

from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET
from oauth2_provider.models import AccessToken, RefreshToken
from oauth2_provider.views import RevokeTokenView, TokenView

from apps.authentication import events, external_services, gates, mfa_services, tokens
from apps.authentication.enums import STATUS_FAILURE, STATUS_SUCCESS, AuthEventChoices
from apps.authentication.scopes import is_first_party
from apps.authentication.serializers import build_requirements, build_user_payload
from apps.core.enums import AuthProviderChoices
from apps.core.exceptions import ApplicationError
from apps.core.ratelimit import throttle_scope

logger = logging.getLogger(__name__)

LOGIN_OK = 8000
LOCKED_OUT = 8003

# OAuth2 "error" -> (business code, message) for failed token requests.
OAUTH_ERROR_MAP = {
    "invalid_grant": (8002, _("Invalid username or password. Please check and try again.")),
    "invalid_client": (8004, _("Invalid client credentials. Contact the system administrator.")),
    "invalid_token": (8005, _("The provided token has expired. Please log in again.")),
    "unsupported_grant_type": (8006, _("Unsupported grant type.")),
}


def _refused(code: int, message: str, **extra) -> JsonResponse:
    return JsonResponse({"success": False, "code": code, "message": message, **extra}, status=400)


def _signed_in(user, request, token_payload: dict) -> JsonResponse:
    return JsonResponse(
        {
            "success": True,
            "code": LOGIN_OK,
            "message": _("Login successful"),
            "access_token": token_payload["access_token"],
            "refresh_token": token_payload.get("refresh_token"),
            "token_type": token_payload.get("token_type", "Bearer"),
            "expires_in": token_payload.get("expires_in"),
            "scope": token_payload.get("scope", ""),
            "user": build_user_payload(user),
            "requirements": build_requirements(user, request),
        },
        status=200,
    )


class CustomTokenView(TokenView):
    """``POST /oauth2/access_token`` — issue or refresh tokens (first-party only).

    This endpoint serves the project's own frontend: the ``password`` and
    ``refresh_token`` grants and nothing else. Any other client landing here
    would receive the *enveloped* response — the full user payload, roles
    included — so both the grant type and the authenticated client are checked.
    """

    ALLOWED_GRANTS = frozenset({"password", "refresh_token"})

    def post(self, request, *args, **kwargs):
        email = request.POST.get("username", "")
        grant_type = request.POST.get("grant_type", "")

        # Refuse foreign grants before anything is minted (RFC error shape,
        # matching what an OAuth client library expects from a token endpoint).
        if grant_type not in self.ALLOWED_GRANTS:
            events.record_failure(
                email=email,
                request=request,
                error_message="Grant type not served by the first-party token endpoint",
                metadata={"grant_type": grant_type},
            )
            return JsonResponse(
                {
                    "error": "unsupported_grant_type",
                    "error_description": "This endpoint serves password and refresh_token grants only.",
                },
                status=400,
            )

        if grant_type == "password":
            try:
                throttle_scope("login", events.request_origin(request)[0])
            except ApplicationError as exc:
                events.record_failure(
                    email=email,
                    request=request,
                    error_message="Too many sign-in attempts from this address",
                    metadata={"grant_type": grant_type},
                )
                return _refused(exc.code, exc.message)

        response = super().post(request, *args, **kwargs)

        try:
            payload = json.loads(response.content.decode())
        except JSONDecodeError:
            return response

        if "access_token" in payload:
            return self._on_success(request, payload, grant_type)
        return self._on_failure(request, payload, email, grant_type)

    def _on_success(self, request, payload, grant_type):
        access_token = (
            AccessToken.objects.filter(token=payload["access_token"])
            .select_related("user", "application")
            .first()
        )
        user = access_token.user if access_token else None

        # Anything that is not the first-party application is refused here, and
        # what was minted for it is revoked.
        if access_token is not None and not is_first_party(access_token.application):
            tokens.revoke_access_token(access_token)
            events.record_failure(
                user=user,
                request=request,
                error_message="A non first-party client used the first-party token endpoint",
                metadata={"grant_type": grant_type, "client": access_token.application.name},
            )
            return _refused(8004, _("Invalid client credentials. Contact the system administrator."))

        if user is None:
            events.record_failure(request=request, error_message="Access token has no associated user")
            return _refused(8001, _("Authentication failed. Please try again."))

        # Post-issue gates. The grant already minted a token above, so any
        # refusal here must also revoke it — otherwise a blocked account would
        # walk away with a usable bearer token.
        gate = gates.blocked_reason(user)
        if gate is not None:
            code, message = gate
            tokens.revoke_access_token(access_token)
            events.record_failure(user=user, request=request, error_message=f"Sign-in blocked (code {code})")
            return _refused(code, message)

        # Two-factor: a password sign-in for an enrolled account must carry a
        # valid code (posted as `otp`). Refresh grants skip this — the person
        # already cleared it when the refresh token was first issued.
        needs_code = grant_type == "password" and user.mfa_enabled
        if needs_code and not mfa_services.code_ok(user, request.POST.get("otp", "")):
            tokens.revoke_access_token(access_token)
            supplied = bool(request.POST.get("otp", "").strip())
            events.record_authentication_event(
                event_type=AuthEventChoices.MfaFailed if supplied else AuthEventChoices.FailedLogin,
                status=STATUS_FAILURE,
                user=user,
                request=request,
                error_message="Two-factor code invalid" if supplied else "Two-factor code required",
            )
            return _refused(
                gates.MFA_CODE_REQUIRED,
                _("That code was incorrect or expired. Please try again.")
                if supplied
                else _("Enter your two-factor authentication code to continue."),
            )

        if grant_type == "refresh_token":
            events.record_authentication_event(
                event_type=AuthEventChoices.TokenRefreshed,
                user=user,
                request=request,
                metadata={"grant_type": grant_type, "scope": payload.get("scope", "")},
            )
        else:
            events.record_login(
                user,
                request,
                provider=user.auth_provider,
                grant_type=grant_type,
                metadata={"scope": payload.get("scope", "")},
            )
        return _signed_in(user, request, payload)

    def _on_failure(self, request, payload, email, grant_type):
        error = payload.get("error", "")
        code, message = OAUTH_ERROR_MAP.get(error, (8001, _("Authentication failed. Please try again.")))

        # A deactivated account never reaches the post-issue gate: Django's
        # ModelBackend refuses ``is_active=False`` before anything is minted,
        # so it would surface as "invalid username or password". Recognise it
        # here — only when the password is right, so the wording never
        # confirms an address to someone guessing.
        if error == "invalid_grant" and grant_type == "password":
            deactivated = self._deactivated_reason(email, request.POST.get("password", ""))
            if deactivated is not None:
                code, message = deactivated

        event = AuthEventChoices.FailedLogin
        if self._locked_out(request):
            code = LOCKED_OUT
            message = _(
                "Your account has been locked due to too many failed login attempts. Please try again later."
            )
            event = AuthEventChoices.AccountLocked

        events.record_authentication_event(
            event_type=event,
            status=STATUS_FAILURE,
            email=email,
            request=request,
            error_message=payload.get("error_description") or error,
            metadata={"grant_type": grant_type, "oauth_error": error},
        )
        return _refused(code, message)

    @staticmethod
    def _locked_out(request) -> bool:
        try:
            from axes.handlers.proxy import AxesProxyHandler

            return bool(AxesProxyHandler.is_locked(request))
        except Exception:  # noqa: BLE001 — axes disabled / not installed
            return False

    @staticmethod
    def _deactivated_reason(email: str, password: str) -> tuple[int, str] | None:
        """``(8010, message)`` when ``email`` names a deactivated local account
        whose password matches; ``None`` otherwise."""
        if not email or not password:
            return None
        user = get_user_model().all_objects.filter(email__iexact=email.strip()).first()
        if user is None or user.auth_provider != AuthProviderChoices.DB.value:
            return None
        if user.is_active and not user.deleted:
            return None
        if not user.check_password(password):
            return None
        return gates.DEACTIVATED, _(gates.ACCOUNT_DEACTIVATED_MESSAGE)


class CustomRevokeTokenView(RevokeTokenView):
    """``POST /oauth2/revoke_token`` — revoke an access or refresh token (sign out)."""

    def post(self, request, *args, **kwargs):
        token = request.POST.get("token", "")
        user, token_type = self._resolve_token_owner(token)

        response = super().post(request, *args, **kwargs)
        succeeded = response.status_code == 200

        events.record_authentication_event(
            event_type=AuthEventChoices.TokenRevoked,
            status=STATUS_SUCCESS if succeeded else STATUS_FAILURE,
            user=user,
            request=request,
            error_message="" if succeeded else "Token revocation failed",
            metadata={"token_type": token_type, "status_code": response.status_code},
        )
        return response

    @staticmethod
    def _resolve_token_owner(token: str):
        access = AccessToken.objects.filter(token=token).select_related("user").first()
        if access:
            return access.user, "access_token"
        refresh = RefreshToken.objects.filter(token=token).select_related("user").first()
        if refresh:
            return refresh.user, "refresh_token"
        return None, "unknown"


@method_decorator(csrf_exempt, name="dispatch")
class ExternalSignInView(View):
    """``POST /oauth2/external/<provider>`` — exchange an ID token for our tokens.

    The client signs in with the provider, then posts the resulting
    ``id_token`` (``credential`` is accepted too). It is verified, the account
    is found or created, and this backend mints its own OAuth2 tokens.

    The same **account gates** as the password grant apply, and an account with
    two-factor authentication is asked for its second factor: the first
    exchange answers ``8014`` with an ``mfa_ticket``; the client then posts
    ``mfa_ticket`` + ``otp`` (no ``id_token`` — a provider's credential is
    single-use) to receive the tokens.
    """

    provider_key: str | None = None  # set by the URL for the fixed /oauth2/google route

    def post(self, request, provider: str = "", *args, **kwargs):
        key = (self.provider_key or provider or "").lower()
        ticket = request.POST.get("mfa_ticket", "")
        otp = request.POST.get("otp", "")
        ip_address, _agent = events.request_origin(request)

        try:
            throttle_scope("external-sign-in", ip_address)
            if ticket:
                user, created, key = self._finish_mfa_challenge(request, ticket, otp)
            else:
                user, created = self._exchange(request, key)
                if user.mfa_enabled and not mfa_services.code_ok(user, otp):
                    return self._mfa_challenge(request, user, key)
            token_payload = tokens.issue_oauth2_tokens(user)
        except _MfaRetry as retry:
            return retry.response
        except ApplicationError as exc:
            events.record_failure(request=request, error_message=exc.message, metadata={"provider": key})
            return _refused(exc.code, exc.message)

        events.record_login(
            user, request, provider=key, grant_type="external", metadata={"new_account": created}
        )
        return _signed_in(user, request, token_payload)

    def _exchange(self, request, key):
        """Verify the credential, find or create the account and apply the gates."""
        provider = external_services.get_provider(key)
        id_token = request.POST.get("id_token") or request.POST.get("credential", "")
        claims = external_services.verify_id_token(provider, id_token, nonce=request.POST.get("nonce", ""))
        user, created = external_services.provision_user(provider, claims)
        gate = gates.blocked_reason(user)
        if gate is not None:
            code, message = gate
            raise ApplicationError(message, code=code)
        return user, created

    def _mfa_challenge(self, request, user, key):
        """Park the verified identity behind a ticket and ask for the second factor."""
        ticket = mfa_services.issue_ticket(user, provider=key)
        events.record_failure(
            user=user,
            request=request,
            error_message="Two-factor code required",
            metadata={"provider": key},
        )
        return _refused(
            gates.MFA_CODE_REQUIRED,
            _("Enter your two-factor authentication code to continue."),
            mfa_ticket=ticket,
        )

    def _finish_mfa_challenge(self, request, ticket, otp):
        """Second leg: the ticket names the account, the code proves the factor."""
        user, key = mfa_services.resolve_ticket(ticket)
        if user is None:
            raise ApplicationError(_("Your sign-in has expired. Please sign in again."), code=8005)
        gate = gates.blocked_reason(user)
        if gate is not None:
            mfa_services.consume_ticket(ticket)
            raise ApplicationError(gate[1], code=gate[0])
        if not mfa_services.code_ok(user, otp):
            still_open = mfa_services.record_ticket_attempt(ticket)
            events.record_authentication_event(
                event_type=AuthEventChoices.MfaFailed,
                status=STATUS_FAILURE,
                user=user,
                request=request,
                error_message="Two-factor code invalid",
                metadata={"provider": key},
            )
            if not still_open:
                raise ApplicationError(_("Too many incorrect codes. Please sign in again."), code=8005)
            raise _MfaRetry(
                _refused(
                    gates.MFA_CODE_REQUIRED,
                    _("That code was incorrect or expired. Please try again."),
                    mfa_ticket=ticket,
                )
            )
        mfa_services.consume_ticket(ticket)
        return user, False, key


class _MfaRetry(Exception):
    """Carries the "wrong code, try again" response out of the challenge leg."""

    def __init__(self, response):
        super().__init__("mfa retry")
        self.response = response


@require_GET
def sign_in_providers(request):
    """``GET /oauth2/providers`` — what the sign-in page should offer. Public:
    a client ID and an issuer are not secrets, the page needs them to start
    the provider's own sign-in."""
    offered = [
        {
            "key": provider.key,
            "label": provider.label,
            "issuer": provider.issuer,
            "client_id": provider.client_id,
        }
        for provider in external_services.providers().values()
    ]
    return JsonResponse({"success": True, "code": 9001, "providers": offered})
