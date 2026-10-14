"""Sign-in sessions = live OAuth2 access tokens, described and revoked one at a time.

django-oauth-toolkit stores no device information on a token. So a session is
*described* by pairing the token with the closest
:class:`~apps.authentication.models.UserLoginHistory` row at or before its
creation — the sign-in that started it (a rotated refresh keeps the same
origin). That is enough for a person to recognise "Chrome on Windows from
197.x" and sign that one device out without touching the rest.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone
from oauth2_provider.models import get_access_token_model, get_refresh_token_model

from apps.authentication import events, tokens
from apps.authentication.enums import AuthEventChoices
from apps.authentication.models import UserLoginHistory
from apps.core.exceptions import NotFoundError

logger = logging.getLogger(__name__)

SESSION_GONE = 9126


@dataclass
class SessionInfo:
    """One live session as the Security page lists it."""

    id: str
    created: datetime
    expires: datetime
    scope: str
    ip_address: str
    browser: str
    os: str
    device: str
    provider: str
    is_current: bool

    @property
    def label(self) -> str:
        """`Chrome 128 · Windows 10` — or 'Unknown device' when nothing was recorded."""
        parts = [part for part in (self.browser, self.os) if part]
        return " · ".join(parts) if parts else "Unknown device"


def list_sessions(*, user, request=None) -> list[SessionInfo]:
    """The user's live (non-expired) access tokens, newest first, each paired
    with the sign-in that started it and flagged when it is the caller's own."""
    live = list(
        get_access_token_model().objects.filter(user=user, expires__gt=timezone.now()).order_by("-created")
    )
    if not live:
        return []
    # One query for every sign-in up to the newest token; matched in Python.
    logins = list(
        UserLoginHistory.objects.filter(user=user, logged_in_at__lte=live[0].created).order_by(
            "-logged_in_at"
        )[:50]
    )
    current = tokens.bearer_token(request)
    sessions: list[SessionInfo] = []
    for token in live:
        origin = next((entry for entry in logins if entry.logged_in_at <= token.created), None)
        sessions.append(
            SessionInfo(
                id=str(token.pk),
                created=token.created,
                expires=token.expires,
                scope=token.scope or "",
                ip_address=(origin.ip_address if origin and origin.ip_address else ""),
                browser=(origin.browser if origin else ""),
                os=(origin.os if origin else ""),
                device=(origin.device if origin else ""),
                provider=(origin.provider if origin else ""),
                is_current=bool(current) and token.token == current,
            )
        )
    return sessions


def revoke_session(*, user, session_id: str, request=None) -> bool:
    """Sign one session out: delete that access token and its refresh token(s).

    Returns ``True`` when the revoked token was the caller's own — the client
    then ends its local session too. Only the owner's tokens are addressable;
    a foreign or unknown id is a plain "not found" (no cross-user probing).
    """
    AccessToken = get_access_token_model()
    try:
        token = AccessToken.objects.get(pk=int(session_id), user=user)
    except (AccessToken.DoesNotExist, ValueError, TypeError):
        raise NotFoundError("That session no longer exists.", code=SESSION_GONE) from None
    was_current = token.token == tokens.bearer_token(request)
    get_refresh_token_model().objects.filter(access_token=token).delete()
    token.delete()
    events.record_authentication_event(
        event_type=AuthEventChoices.SessionRevoked,
        user=user,
        request=request,
        metadata={"session": str(session_id), "own_session": was_current},
    )
    return was_current


def revoke_all_my_sessions(*, user, request=None, keep_current: bool = True) -> int:
    """Sign out everywhere (by default keeping the session making the request)."""
    keep = tokens.bearer_token(request) if keep_current else None
    count = tokens.revoke_all_sessions(user=user, keep_token=keep or None)
    events.record_authentication_event(
        event_type=AuthEventChoices.SessionsRevoked,
        user=user,
        request=request,
        metadata={"revoked": count, "kept_current": bool(keep)},
    )
    return count


def revoke_sessions_of(*, user, actor, request=None) -> int:
    """An administrator signs another person out everywhere."""
    count = tokens.revoke_all_sessions(user=user)
    events.record_authentication_event(
        event_type=AuthEventChoices.SessionsRevoked,
        user=user,
        request=request,
        metadata={"revoked": count, "by": str(getattr(actor, "uid", ""))},
    )
    return count
