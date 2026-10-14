"""Security invariants of the API surface, enforced by CI.

These hold whether or not the optional single sign-on module is installed. If a
change removes a permission class, re-adds the stock OAuth2 backend or loosens
the DRF defaults, one of these fails with a message saying what to restore.
"""

from django.conf import settings
from django.urls import URLPattern, URLResolver
from oauth2_provider.contrib.rest_framework import TokenHasReadWriteScope
from rest_framework.views import APIView

# DRF views that are *deliberately* public / not first-party-gated. Anything
# else mounted anywhere must carry TokenHasReadWriteScope (or a subclass).
PUBLIC_DRF_VIEWS = {"PublicImageAPIView"}


def drf_view_classes(patterns, found=None):
    found = found if found is not None else {}
    for entry in patterns:
        if isinstance(entry, URLResolver):
            drf_view_classes(entry.url_patterns, found)
        elif isinstance(entry, URLPattern):
            cls = getattr(entry.callback, "cls", None)  # DRF's as_view marker
            if cls is not None and issubclass(cls, APIView):
                found[cls.__name__] = cls
    return found


def test_every_drf_view_requires_a_first_party_scope():
    from config.urls import urlpatterns

    offenders = []
    for name, cls in drf_view_classes(urlpatterns).items():
        if name in PUBLIC_DRF_VIEWS:
            continue
        if not any(issubclass(p, TokenHasReadWriteScope) for p in cls.permission_classes):
            offenders.append(name)
    assert not offenders, (
        f"DRF view(s) {offenders} would accept identity-only tokens. Add "
        "TokenHasReadWriteScope to permission_classes (or rely on the DRF "
        "defaults), or — only for a deliberately public endpoint — add the "
        "class name to PUBLIC_DRF_VIEWS in this test."
    )


def test_auth_backend_chain_is_scope_aware():
    backends = settings.AUTHENTICATION_BACKENDS
    assert "oauth2_provider.backends.OAuth2Backend" not in backends, (
        "The stock OAuth2 backend authenticates identity-only tokens against "
        "the whole GraphQL API — keep FirstPartyOAuth2Backend instead."
    )
    assert "apps.authentication.backends.FirstPartyOAuth2Backend" in backends


def test_drf_and_oauth_defaults_are_pinned():
    permission_classes = settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]
    assert "oauth2_provider.contrib.rest_framework.TokenHasReadWriteScope" in permission_classes, (
        "New DRF endpoints must be first-party-only by default."
    )
    assert "apps.authentication.middleware.UnrestrictedSession" in permission_classes, (
        "A restricted session (must change password / set up 2FA) must be refused by REST too."
    )
    assert sorted(settings.OAUTH2_PROVIDER["DEFAULT_SCOPES"]) == ["read", "write"]
    assert settings.OAUTH2_PROVIDER["PKCE_REQUIRED"] is True
