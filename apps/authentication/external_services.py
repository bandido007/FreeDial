"""External sign-in: this backend as an OpenID Connect *client*.

"Continue with Google / Microsoft / Keycloak / another system." The frontend
signs the person in with the provider and posts the resulting **ID token**
here. It is verified, the account behind it is found or created, and this
backend mints its own OAuth2 tokens — so the rest of the system treats an
external sign-in exactly like a password sign-in.

Providers are configured in ``OIDC_PROVIDERS``::

    {
      "google":   {"label": "Google", "issuer": "https://accounts.google.com",
                   "client_id": "…apps.googleusercontent.com"},
      "keycloak": {"label": "Staff sign-in",
                   "issuer": "https://sso.example.org/realms/main",
                   "client_id": "portal", "account_type": "Internal"}
    }

Optional keys per provider: ``jwks_uri`` (skips discovery), ``account_type``
(what a newly created account is stamped as; default ``External``),
``allowed_domains`` (only these e-mail domains may sign in), ``auto_provision``
(overrides ``OIDC_AUTO_PROVISION``).

Verification
------------
The token's signature is checked against the provider's published keys, and
its issuer, audience (our client ID), expiry and — when the client sent one —
nonce are all enforced. Only asymmetric algorithms are accepted: a token
"signed" with ``none`` or with a shared secret is refused.

Matching an account
-------------------
1. by the provider's **subject** (``sub``) — the link made on a previous sign-in;
2. else by e-mail address, **only if the provider says the address is
   verified** — otherwise anyone could claim another person's account by
   registering their address at a provider that does not check it;
3. else a new account is created, when provisioning is allowed.
"""

import logging
from dataclasses import dataclass, field

import jwt
import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from apps.authentication.gates import ACCOUNT_DEACTIVATED_MESSAGE
from apps.authentication.models import ExternalIdentity
from apps.core.enums import AccountTypeChoices, AuthProviderChoices
from apps.core.exceptions import ApplicationError, PermissionDeniedError, ValidationError

logger = logging.getLogger(__name__)

NOT_AVAILABLE = 8017
NO_LINKED_ACCOUNT = 8018
INVALID_TOKEN = 8001

ALLOWED_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]
DISCOVERY_CACHE_SECONDS = 24 * 3600
HTTP_TIMEOUT_SECONDS = 5
CLOCK_SKEW_SECONDS = 60

_jwk_clients: dict[str, jwt.PyJWKClient] = {}


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    issuer: str
    client_id: str
    jwks_uri: str = ""
    account_type: str = AccountTypeChoices.External.value
    allowed_domains: tuple = field(default_factory=tuple)
    auto_provision: bool = True

    @property
    def issuers(self) -> list[str]:
        """Spellings of the issuer this provider is known to put in a token."""
        bare = self.issuer.rstrip("/")
        accepted = {bare, bare + "/"}
        if "accounts.google.com" in bare:
            accepted |= {"accounts.google.com", "https://accounts.google.com"}
        return sorted(accepted)

    @property
    def auth_provider(self) -> str:
        if "accounts.google.com" in self.issuer:
            return AuthProviderChoices.GOOGLE.value
        return AuthProviderChoices.OIDC.value


def providers() -> dict[str, Provider]:
    """The configured providers that are complete enough to use."""
    configured = getattr(settings, "OIDC_PROVIDERS", None) or {}
    default_provision = bool(getattr(settings, "OIDC_AUTO_PROVISION", True))
    found = {}
    for key, options in configured.items():
        issuer = (options.get("issuer") or "").strip()
        client_id = (options.get("client_id") or "").strip()
        if not issuer or not client_id:
            continue
        account_type = options.get("account_type") or AccountTypeChoices.External.value
        if account_type not in AccountTypeChoices.values():
            account_type = AccountTypeChoices.External.value
        found[key.lower()] = Provider(
            key=key.lower(),
            label=options.get("label") or key.title(),
            issuer=issuer,
            client_id=client_id,
            jwks_uri=(options.get("jwks_uri") or "").strip(),
            account_type=account_type,
            allowed_domains=tuple(d.lower().lstrip("@") for d in options.get("allowed_domains") or []),
            auto_provision=bool(options.get("auto_provision", default_provision)),
        )
    return found


def get_provider(key: str) -> Provider:
    provider = providers().get((key or "").lower())
    if provider is None:
        raise ApplicationError("This way of signing in is not available.", code=NOT_AVAILABLE)
    return provider


# ── Verifying the ID token ───────────────────────────────────────────────────
def _jwks_uri(provider: Provider) -> str:
    if provider.jwks_uri:
        return provider.jwks_uri
    cache_key = f"oidc-jwks-uri:{provider.key}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    url = provider.issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        response = requests.get(url, timeout=HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()
        uri = response.json()["jwks_uri"]
    except Exception as exc:  # noqa: BLE001
        logger.error("[external] discovery failed for %s: %s", provider.key, exc)
        raise ApplicationError(
            "The sign-in provider could not be reached. Please try again shortly.", code=NOT_AVAILABLE
        ) from exc
    cache.set(cache_key, uri, timeout=DISCOVERY_CACHE_SECONDS)
    return uri


def _signing_key(provider: Provider, token: str):
    uri = _jwks_uri(provider)
    client = _jwk_clients.get(uri)
    if client is None:
        client = _jwk_clients[uri] = jwt.PyJWKClient(uri, cache_keys=True, timeout=HTTP_TIMEOUT_SECONDS)
    return client.get_signing_key_from_jwt(token).key


def verify_id_token(provider: Provider, token: str, *, nonce: str = "") -> dict:
    """Verify an ID token from ``provider`` and return its claims (raises on invalid)."""
    if not token:
        raise ValidationError("The sign-in token is missing.", code=9005)
    try:
        key = _signing_key(provider, token)
        claims = jwt.decode(
            token,
            key,
            algorithms=ALLOWED_ALGORITHMS,
            audience=provider.client_id,
            issuer=provider.issuers,
            leeway=CLOCK_SKEW_SECONDS,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except ApplicationError:
        raise
    except jwt.PyJWKClientError as exc:
        logger.error("[external] could not fetch signing keys for %s: %s", provider.key, exc)
        raise ApplicationError(
            "The sign-in provider could not be reached. Please try again shortly.", code=NOT_AVAILABLE
        ) from exc
    except jwt.PyJWTError as exc:
        logger.warning("[external] rejected a %s token: %s", provider.key, exc)
        raise ValidationError(
            "That sign-in could not be verified. Please try again.", code=INVALID_TOKEN
        ) from exc

    if nonce and claims.get("nonce") != nonce:
        raise ValidationError("That sign-in could not be verified. Please try again.", code=INVALID_TOKEN)
    if not claims.get("email"):
        raise ValidationError("The sign-in provider did not share an e-mail address.", code=INVALID_TOKEN)
    if provider.allowed_domains:
        domain = claims["email"].rsplit("@", 1)[-1].lower()
        if domain not in provider.allowed_domains:
            raise PermissionDeniedError(
                "Accounts from that e-mail domain cannot sign in here.", code=NOT_AVAILABLE
            )
    return claims


# ── Finding or creating the account ──────────────────────────────────────────
def names_from_claims(claims: dict) -> tuple[str, str]:
    """Best-effort (first_name, last_name) from an ID token's claims.

    Prefers the structured ``given_name`` / ``family_name``; falls back to
    splitting the display ``name``; finally to the e-mail local part — so an
    account never ends up nameless.
    """
    given = (claims.get("given_name") or "").strip()
    family = (claims.get("family_name") or "").strip()
    if given or family:
        return given, family
    full = (claims.get("name") or "").strip()
    if full:
        parts = full.split()
        return parts[0], " ".join(parts[1:])
    local = (claims.get("email") or "").split("@", 1)[0]
    return local, ""


def _email_verified(claims: dict) -> bool:
    value = claims.get("email_verified")
    return value is True or str(value).lower() == "true"


@transaction.atomic
def provision_user(provider: Provider, claims: dict) -> tuple[object, bool]:
    """Find or create the account behind a verified external identity.

    Returns ``(user, created)``.
    """
    User = get_user_model()
    subject = str(claims["sub"])
    email = claims["email"].strip().lower()
    first_name, last_name = names_from_claims(claims)

    identity = (
        ExternalIdentity.objects.filter(provider=provider.key, subject=subject).select_related("user").first()
    )
    user = identity.user if identity is not None else None
    created = False

    if user is None:
        existing = User.all_objects.filter(email__iexact=email).first()
        if existing is not None:
            if not _email_verified(claims):
                # The provider does not vouch for the address, so this sign-in
                # proves nothing about who owns the existing account.
                raise PermissionDeniedError(
                    "Your e-mail address is not verified with the sign-in provider.", code=INVALID_TOKEN
                )
            user = existing
        else:
            if not provider.auto_provision:
                raise PermissionDeniedError(
                    "No account is linked to this sign-in. Ask an administrator for an account.",
                    code=NO_LINKED_ACCOUNT,
                )
            if not _email_verified(claims):
                raise PermissionDeniedError(
                    "Your e-mail address is not verified with the sign-in provider.", code=INVALID_TOKEN
                )
            user = User(
                email=email,
                first_name=first_name,
                last_name=last_name,
                photo=claims.get("picture", "") or "",
                account_type=provider.account_type,
                auth_provider=provider.auth_provider,
                is_verified=True,  # the provider verified the address
            )
            user.set_unusable_password()
            user.save()
            created = True
            logger.info("[provision_user] created user %s via %s", user.pk, provider.key)

    if getattr(user, "deleted", False) or not user.is_active:
        raise PermissionDeniedError(ACCOUNT_DEACTIVATED_MESSAGE, code=8010)

    if identity is None:
        identity = ExternalIdentity.objects.create(
            user=user, provider=provider.key, subject=subject, email=email
        )
    identity.last_login_at = timezone.now()
    identity.email = email
    identity.save(update_fields=["last_login_at", "email"])

    if not created:
        _backfill(user, claims, first_name, last_name)
    return user, created


def _backfill(user, claims: dict, first_name: str, last_name: str) -> None:
    """Fill in a name or photo the account is missing, so returning sign-ins heal."""
    changed = []
    if not user.first_name and first_name:
        user.first_name = first_name
        changed.append("first_name")
    if not user.last_name and last_name:
        user.last_name = last_name
        changed.append("last_name")
    if not user.photo and claims.get("picture"):
        user.photo = claims["picture"]
        changed.append("photo")
    if changed:
        user.save(update_fields=changed)
