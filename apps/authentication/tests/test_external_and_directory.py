"""External sign-in (OpenID Connect) and directory (LDAP) accounts."""

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import Client

from apps.authentication import directory_services, external_services, mfa_services, totp
from apps.authentication import ldap as directory
from apps.authentication.models import AuthenticationAudit, ExternalIdentity, LdapConfig
from apps.core.exceptions import ApplicationError, PermissionDeniedError, ValidationError
from apps.users.models import User

pytestmark = pytest.mark.django_db

ISSUER = "https://accounts.google.com"
CLIENT_ID = "starter.apps.googleusercontent.com"


@pytest.fixture
def provider_keys(settings, monkeypatch):
    """A signing key, and a provider whose published keys we control."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings.OIDC_PROVIDERS = {
        "google": {"label": "Google", "issuer": ISSUER, "client_id": CLIENT_ID},
        "corp": {
            "label": "Staff",
            "issuer": "https://sso.example.org/realms/main",
            "client_id": "portal",
            "account_type": "Internal",
            "allowed_domains": ["example.org"],
            "auto_provision": False,
        },
    }
    monkeypatch.setattr(external_services, "_signing_key", lambda provider, token: private.public_key())
    return private


def id_token(private, **claims):
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "google-subject-1",
        "email": "asha@example.org",
        "email_verified": True,
        "given_name": "Asha",
        "family_name": "Mkumbo",
        "iat": now,
        "exp": now + 600,
        **claims,
    }
    return jwt.encode(payload, private, algorithm="RS256")


def test_providers_are_read_from_settings(provider_keys, settings):
    providers = external_services.providers()
    assert set(providers) == {"google", "corp"}
    assert (
        providers["google"].auth_provider == "GOOGLE" and "accounts.google.com" in providers["google"].issuers
    )
    assert providers["corp"].account_type == "Internal" and not providers["corp"].auto_provision
    settings.OIDC_PROVIDERS = {"broken": {"issuer": ""}}
    assert external_services.providers() == {}
    with pytest.raises(ApplicationError) as excinfo:
        external_services.get_provider("broken")
    assert excinfo.value.code == 8017


def test_a_valid_token_creates_the_account_and_links_the_identity(provider_keys, oauth_app):
    response = Client().post("/oauth2/external/google", data={"id_token": id_token(provider_keys)})
    assert response.status_code == 200, response.content
    body = json.loads(response.content)
    assert body["user"]["email"] == "asha@example.org" and body["user"]["auth_provider"] == "GOOGLE"
    person = User.objects.get(email="asha@example.org")
    assert person.is_verified and person.first_name == "Asha" and not person.has_usable_password()
    assert ExternalIdentity.objects.get(user=person).subject == "google-subject-1"
    assert AuthenticationAudit.objects.filter(user=person, event_type="token_generated").exists()

    again = Client().post(
        "/oauth2/google", data={"credential": id_token(provider_keys, email="renamed@example.org")}
    )
    assert again.status_code == 200 and User.objects.filter(email="asha@example.org").exists()
    assert ExternalIdentity.objects.count() == 1  # matched on the subject, not the address


def test_an_existing_account_is_linked_only_on_a_verified_address(provider_keys, make_user):
    person = make_user(email="asha@example.org")
    provider = external_services.get_provider("google")
    unverified = jwt.decode(
        id_token(provider_keys, email_verified=False), options={"verify_signature": False}
    )
    with pytest.raises(PermissionDeniedError):
        external_services.provision_user(provider, unverified)
    verified = jwt.decode(id_token(provider_keys), options={"verify_signature": False})
    linked, created = external_services.provision_user(provider, verified)
    assert linked == person and not created


@pytest.mark.parametrize(
    "claims,code",
    [
        ({"aud": "someone-else"}, 8001),
        ({"iss": "https://evil.example"}, 8001),
        ({"exp": int(time.time()) - 1000}, 8001),
        ({"email": ""}, 8001),
        ({"nonce": "other"}, 8001),
    ],
)
def test_bad_tokens_are_refused(provider_keys, claims, code):
    provider = external_services.get_provider("google")
    with pytest.raises(ValidationError) as excinfo:
        external_services.verify_id_token(provider, id_token(provider_keys, **claims), nonce="expected")
    assert excinfo.value.code == code
    with pytest.raises(ValidationError):
        external_services.verify_id_token(provider, "")


def test_an_unsigned_token_is_refused(provider_keys):
    provider = external_services.get_provider("google")
    forged = jwt.encode(
        {"iss": ISSUER, "aud": CLIENT_ID, "sub": "x", "email": "x@example.org"}, "", algorithm="none"
    )
    with pytest.raises(ValidationError):
        external_services.verify_id_token(provider, forged)


def test_a_provider_that_does_not_provision_needs_an_existing_account(provider_keys, make_user):
    provider = external_services.get_provider("corp")
    claims = {"sub": "corp-1", "email": "new@example.org", "email_verified": True}
    with pytest.raises(PermissionDeniedError) as excinfo:
        external_services.provision_user(provider, claims)
    assert excinfo.value.code == 8018
    with pytest.raises(PermissionDeniedError):
        external_services.verify_id_token(
            provider,
            jwt.encode(
                {
                    "iss": provider.issuer,
                    "aud": "portal",
                    "sub": "x",
                    "email": "x@other.org",
                    "iat": int(time.time()),
                    "exp": int(time.time()) + 60,
                },
                provider_keys,
                algorithm="RS256",
            ),
        )


def test_a_deactivated_account_cannot_sign_in_externally(provider_keys, make_user, oauth_app):
    make_user(email="asha@example.org", is_active=False)
    response = Client().post("/oauth2/external/google", data={"id_token": id_token(provider_keys)})
    assert response.status_code == 400 and json.loads(response.content)["code"] == 8010


def test_two_factor_on_an_external_sign_in_uses_a_ticket(provider_keys, make_user, oauth_app):
    person = make_user(email="asha@example.org")
    secret = mfa_services.begin_enrollment(user=person)["secret"]
    mfa_services.confirm_enrollment(user=person, code=totp._hotp(secret, totp.current_step()))

    first = json.loads(
        Client().post("/oauth2/external/google", data={"id_token": id_token(provider_keys)}).content
    )
    assert first["code"] == 8014 and first["mfa_ticket"]

    wrong = json.loads(
        Client()
        .post("/oauth2/external/google", data={"mfa_ticket": first["mfa_ticket"], "otp": "000000"})
        .content
    )
    assert wrong["code"] == 8014 and wrong["mfa_ticket"] == first["mfa_ticket"]

    code = totp._hotp(secret, totp.current_step() + 1)
    done = Client().post("/oauth2/external/google", data={"mfa_ticket": first["mfa_ticket"], "otp": code})
    assert done.status_code == 200 and json.loads(done.content)["access_token"]
    assert (
        json.loads(
            Client().post("/oauth2/external/google", data={"mfa_ticket": "stale", "otp": code}).content
        )["code"]
        == 8005
    )


def test_the_sign_in_page_learns_the_providers(provider_keys, gql):
    listed = Client().get("/oauth2/providers").json()["providers"]
    assert {row["key"] for row in listed} == {"google", "corp"}
    envelope = gql("query { getSignInProviders { dataList { key label clientId } } }")
    assert {row["key"] for row in envelope["dataList"]} == {"google", "corp"}


def test_names_from_claims():
    assert external_services.names_from_claims({"given_name": "A", "family_name": "B"}) == ("A", "B")
    assert external_services.names_from_claims({"name": "Asha Mkumbo Jr"}) == ("Asha", "Mkumbo Jr")
    assert external_services.names_from_claims({"email": "asha@example.org"}) == ("asha", "")


# ── Directories ──────────────────────────────────────────────────────────────
def test_directory_configurations(admin, make_role):
    role = make_role("Staff", ["can_view_users"])
    config = directory_services.create_config(
        ldap_url="ldaps://dir.example.org",
        bind_dn="cn=svc",
        bind_password="s3cret",
        search_base="dc=example,dc=org",
        email_domain="@Example.ORG",
        default_role_uid=role.uid,
    )
    assert config.email_domain == "example.org" and config.get_bind_password() == "s3cret"
    assert config.bind_password_encrypted != "s3cret"
    assert directory.config_for_email("Asha@example.org") == config
    assert directory.config_for_email("asha@other.org") is None

    with pytest.raises(ValidationError):
        directory_services.create_config(ldap_url="http://x", bind_dn="cn=svc")
    with pytest.raises(ValidationError):
        directory_services.create_config(ldap_url="ldap://x", bind_dn="cn=svc", email_domain="example.org")
    directory_services.update_config(
        uid=config.uid, name="Main", clear_default_role=True, bind_password="n3w"
    )
    config.refresh_from_db()
    assert config.default_role is None and config.get_bind_password() == "n3w"


def test_pulling_users_from_a_simulated_directory(admin, make_role, settings):
    settings.LDAP_SIMULATE_DIRECTORY = True
    role = make_role("Staff", ["can_view_users"])
    config = directory_services.create_config(
        ldap_url="ldap://dir.example.org",
        bind_dn="cn=svc",
        search_base="dc=example,dc=org",
        email_domain="example.org",
        default_role_uid=role.uid,
    )
    created = directory_services.pull_users(config_uid=config.uid, actor=admin)
    assert len(created) == len(directory.SIMULATED_DIRECTORY_NAMES)
    person = User.all_objects.get(email="amina.hassan@example.org")
    assert not person.is_active and person.is_verified and person.auth_provider == "LDAP"
    assert person.account_type == "Internal" and person.first_name == "Amina"
    assert person.user_roles.filter(role=role, source="directory").exists()
    assert directory_services.pull_users(config_uid=config.uid, actor=admin) == []  # idempotent

    with pytest.raises(ValidationError) as excinfo:
        directory_services.delete_config(uid=config.uid)
    assert excinfo.value.code == 9015
    directory_services.update_config(uid=config.uid, is_active=False)
    with pytest.raises(ValidationError):
        directory_services.pull_users(config_uid=config.uid, actor=admin)


def test_without_python_ldap_and_no_simulation_the_pull_fails_clearly(admin, settings):
    settings.LDAP_SIMULATE_DIRECTORY = False
    config = directory_services.create_config(ldap_url="ldap://dir", bind_dn="cn=svc", search_base="dc=x")
    if directory.LDAP_AVAILABLE:
        pytest.skip("python-ldap is installed here")
    with pytest.raises(ApplicationError) as excinfo:
        directory_services.pull_users(config_uid=config.uid, actor=admin)
    assert excinfo.value.code == 9125
    assert directory.ConfiguredLdapBackend().authenticate(None, username="a@b", password="x") is None
    assert directory.escape_filter_chars("a*b(c)") == r"a\2ab\28c\29"


def test_a_directory_sign_in_provisions_the_account(make_role, settings):
    settings.LDAP_DEFAULT_ROLE = "Staff"
    role = make_role("Staff", [])
    person = directory_services.ensure_directory_account(email="new@example.org")
    assert person.auth_provider == "LDAP" and person.is_verified and not person.has_usable_password()
    assert person.user_roles.filter(role=role).exists()
    directory_services.on_directory_login(person)
    assert LdapConfig.objects.count() == 0
