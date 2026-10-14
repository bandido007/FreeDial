"""Shared fixtures for every test in the project.

=====================  ======================================================
Fixture                What it gives you
=====================  ======================================================
``make_user``          a factory for accounts (verified and active by default)
``user``               one ordinary signed-up person
``admin``              a platform operator holding the system role
``make_role``          a factory for roles holding the permissions you name
``grant``              give a user a permission (through a role made for it)
``gql``                run a GraphQL operation as a user, get the envelope back
``api``                a REST client that is signed in with a real token
``oauth_app``          the first-party OAuth2 application
``mailbox``            the e-mails sent during the test
=====================  ======================================================

The cache and the per-app registries are reset around every test, so tests do
not leak rate-limit counters or cached permissions into one another.
"""

import json
from urllib.parse import urlencode

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import Client, RequestFactory

PASSWORD = "Str0ng!Passw0rd"


def pytest_ignore_collect(collection_path, config):
    """Skip the tests of an optional module that is switched off."""
    from django.conf import settings

    parts = collection_path.parts
    if "apps" in parts:
        position = parts.index("apps")
        if len(parts) > position + 1:
            label = f"apps.{parts[position + 1]}"
            known = (settings.APPS_DIR / parts[position + 1] / "apps.py").is_file()
            if known and label not in settings.INSTALLED_APPS:
                return True
    return None


@pytest.fixture(autouse=True)
def _isolation(settings, tmp_path):
    """A clean cache and a throwaway media folder for every test."""
    cache.clear()
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    yield
    cache.clear()


@pytest.fixture
def catalogue(db):
    """The permission catalogue written to the database (as `manage.py seed` does)."""
    from apps.rbac import services

    services.sync_catalogue()
    return services


@pytest.fixture
def make_user(db):
    """``make_user(email=..., **fields)`` → a saved user whose password is PASSWORD."""
    counter = {"n": 0}

    def factory(email=None, password=PASSWORD, **fields):
        counter["n"] += 1
        number = counter["n"]
        fields.setdefault("first_name", "Test")
        fields.setdefault("last_name", f"User{chr(64 + number)}" if number <= 26 else "User")
        fields.setdefault("is_verified", True)
        fields.setdefault("phone", f"+2557550000{number:02d}")
        user = get_user_model().objects.create_user(
            email=email or f"user{number}@example.org", password=password, **fields
        )
        return user

    return factory


@pytest.fixture
def user(make_user):
    return make_user(email="person@example.org")


@pytest.fixture
def admin(make_user, catalogue):
    """A platform operator: System account type + the system role."""
    from apps.rbac import selectors, services
    from apps.rbac.permissions import SYSTEM_ROLE_NAME

    operator = make_user(email="admin@example.org", account_type="System", is_staff=True)
    services.grant_role(operator, selectors.get_role_by_name(SYSTEM_ROLE_NAME))
    return operator


@pytest.fixture
def make_role(catalogue):
    """``make_role("Clerk", ["can_view_users"])`` → a role holding those permissions."""
    from apps.rbac.models import Permission, Role, RolePermission

    def factory(name, codes=()):
        role = Role.objects.create(name=name)
        permissions = list(Permission.objects.filter(code__in=codes))
        missing = set(codes) - {permission.code for permission in permissions}
        assert not missing, f"Unknown permission code(s): {sorted(missing)}"
        RolePermission.objects.bulk_create(
            [RolePermission(role=role, permission=permission) for permission in permissions]
        )
        catalogue.cache.invalidate_all()
        return role

    return factory


@pytest.fixture
def grant(make_role):
    """``grant(user, "can_view_users", ...)`` → the user now holds those permissions."""
    from apps.rbac import services

    counter = {"n": 0}

    def give(user, *codes):
        counter["n"] += 1
        role = make_role(f"test-role-{counter['n']}", codes)
        services.grant_role(user, role)
        return role

    return give


@pytest.fixture
def gql(db):
    """Run a GraphQL operation and return the data of its first field.

        envelope = gql("query { meQuery { status code } }", user=user)
        assert envelope["code"] == 9001

    ``raw=True`` returns the whole execution result instead.
    """
    from django.conf import settings
    from django.utils.module_loading import import_string

    from config.schema import schema

    # The same resolver middleware the view applies (restricted sessions, …).
    middleware = [import_string(path)() for path in settings.GRAPHENE.get("MIDDLEWARE", [])]

    def run(query, variables=None, *, user=None, headers=None, raw=False):
        request = RequestFactory().post("/graphql", **(headers or {}))
        if user is None:
            from django.contrib.auth.models import AnonymousUser

            user = AnonymousUser()
        request.user = user
        request.request_id = "test-request"
        result = schema.execute(query, variables=variables, context_value=request, middleware=middleware)
        if raw:
            return result
        assert result.errors is None, result.errors
        return next(iter(result.data.values()))

    return run


@pytest.fixture
def oauth_app(db, settings):
    """The first-party OAuth2 application the frontend signs in against."""
    from oauth2_provider.models import get_application_model

    Application = get_application_model()
    application, _ = Application.objects.get_or_create(
        name=settings.OAUTH_APP_NAME,
        defaults={
            "client_type": Application.CLIENT_PUBLIC,
            "authorization_grant_type": Application.GRANT_PASSWORD,
        },
    )
    return application


@pytest.fixture
def sign_in(oauth_app):
    """``sign_in(email)`` → the decoded response of a password sign-in."""

    def post(email, password=PASSWORD, **extra):
        # Form-encoded, as an OAuth2 client sends it (the test client's default
        # is multipart, which the token endpoint also accepts but no SPA uses).
        response = Client().post(
            "/oauth2/access_token",
            data=urlencode(
                {
                    "grant_type": "password",
                    "username": email,
                    "password": password,
                    "client_id": oauth_app.client_id,
                    **extra,
                }
            ),
            content_type="application/x-www-form-urlencoded",
        )
        return response.status_code, json.loads(response.content.decode())

    return post


@pytest.fixture
def api(sign_in):
    """``api(user)`` → a test client that sends the user's bearer token."""

    def client_for(user, password=PASSWORD):
        status, body = sign_in(user.email, password)
        assert status == 200, body
        client = Client(HTTP_AUTHORIZATION=f"Bearer {body['access_token']}")
        client.access_token = body["access_token"]
        client.refresh_token = body["refresh_token"]
        return client

    return client_for


@pytest.fixture
def mailbox():
    """The e-mails sent during the test (``mailbox[0].subject``, …)."""
    mail.outbox.clear()
    return mail.outbox
