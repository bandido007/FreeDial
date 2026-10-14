"""OAuth2 tokens: minting them outside the password grant, and revoking them.

A *session* is a live access token. Signing out — one device or all of them —
means deleting tokens.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from oauth2_provider.models import (
    get_access_token_model,
    get_application_model,
    get_grant_model,
    get_id_token_model,
    get_refresh_token_model,
)
from oauthlib.common import generate_token

from apps.authentication.scopes import is_first_party
from apps.core.exceptions import ApplicationError

logger = logging.getLogger(__name__)

FIRST_PARTY_SCOPE = "read write"


def default_application():
    """The OAuth2 client first-party tokens are bound to (created by ``manage.py seed``)."""
    Application = get_application_model()
    name = getattr(settings, "OAUTH_APP_NAME", "")
    application = Application.objects.filter(name=name).first()
    if application is None:
        application = next(
            (candidate for candidate in Application.objects.all() if is_first_party(candidate)), None
        )
    if application is None:
        raise ApplicationError("No OAuth2 application is configured. Run `python manage.py seed`.", code=9004)
    return application


def issue_oauth2_tokens(user, application=None) -> dict:
    """Mint an access + refresh token pair for ``user``.

    Mirrors what the password grant does internally, so an external sign-in
    returns the same token shape as ``/oauth2/access_token``.
    """
    application = application or default_application()
    AccessToken = get_access_token_model()
    RefreshToken = get_refresh_token_model()

    expires_in = settings.OAUTH2_PROVIDER["ACCESS_TOKEN_EXPIRE_SECONDS"]
    access = AccessToken.objects.create(
        user=user,
        application=application,
        token=generate_token(),
        expires=timezone.now() + timedelta(seconds=expires_in),
        scope=FIRST_PARTY_SCOPE,
    )
    refresh = RefreshToken.objects.create(
        user=user, application=application, token=generate_token(), access_token=access
    )
    return {
        "access_token": access.token,
        "refresh_token": refresh.token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "scope": FIRST_PARTY_SCOPE,
    }


def revoke_access_token(access_token) -> None:
    """Delete a just-issued access token (and its refresh) after a refused sign-in."""
    if access_token is None:
        return
    try:
        get_refresh_token_model().objects.filter(access_token=access_token).delete()
        access_token.delete()
    except Exception:  # noqa: BLE001 — best-effort cleanup
        logger.warning("Failed to revoke a token after a refused sign-in", exc_info=True)


def bearer_token(request) -> str:
    """The raw access token the request carries, or ``""``."""
    header = getattr(request, "META", {}).get("HTTP_AUTHORIZATION", "") if request else ""
    prefix, _, token = header.partition(" ")
    return token.strip() if prefix.lower() == "bearer" else ""


def revoke_all_sessions(*, user, keep_token: str | None = None) -> int:
    """Delete all of the user's OAuth artefacts (optionally keep one access token).

    Covers refresh + access tokens **and** pending authorization grants and
    ID tokens, so both first-party sessions and in-flight single sign-on
    requests die together. Called on "sign out everywhere" *and* whenever an
    account gate closes — a held account must not keep a single usable
    credential.
    """
    access_qs = get_access_token_model().objects.filter(user=user)
    if keep_token:
        access_qs = access_qs.exclude(token=keep_token)
    count = access_qs.count()
    # Every refresh token of the user goes — not only those still linked to an
    # access token. With refresh-token rotation a rotated token keeps
    # ``access_token=NULL``; leaving it behind would let a signed-out device
    # mint itself a new session.
    refresh_qs = get_refresh_token_model().objects.filter(user=user)
    if keep_token:
        refresh_qs = refresh_qs.exclude(access_token__token=keep_token)
    refresh_qs.delete()
    access_qs.delete()
    get_grant_model().objects.filter(user=user).delete()
    if not keep_token:
        get_id_token_model().objects.filter(user=user).delete()
    logger.info("[revoke_all_sessions] user %s: revoked %s session(s)", user.pk, count)
    return count


def purge_expired_tokens() -> int:
    """Remove access tokens that expired (and the refresh tokens past their life)."""
    now = timezone.now()
    refresh_life = timedelta(seconds=settings.OAUTH2_PROVIDER.get("REFRESH_TOKEN_EXPIRE_SECONDS") or 0)
    removed = 0
    if refresh_life:
        stale_refresh = get_refresh_token_model().objects.filter(created__lt=now - refresh_life)
        removed += stale_refresh.count()
        stale_refresh.delete()
    stale_access = get_access_token_model().objects.filter(expires__lt=now, refresh_token__isnull=True)
    removed += stale_access.count()
    stale_access.delete()
    get_grant_model().objects.filter(expires__lt=now).delete()
    return removed
