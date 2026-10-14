"""GraphQL output types for the authentication domain."""

import graphene
from graphene.types.generic import GenericScalar
from graphene_django import DjangoObjectType

from apps.authentication.enums import AuthEventEnum
from apps.authentication.models import AuthenticationAudit, LdapConfig, UserLoginHistory
from apps.core.enums import AccountTypeEnum, AuthProviderEnum
from apps.rbac.types import RoleType
from apps.users.types import UserType


class LoggedInUserType(graphene.ObjectType):
    """Payload for ``meQuery`` — identity, classification and access context."""

    attributes = graphene.Field(UserType)
    account_type = AccountTypeEnum()
    auth_provider = AuthProviderEnum()
    authorities = graphene.List(graphene.String, description="Flattened permission codes")
    total_authorities = graphene.Int()
    roles = graphene.List(RoleType)
    mfa_required = graphene.Boolean(description="Policy obliges this account to use two-factor.")
    mfa_enrolment_required = graphene.Boolean(
        description="The account must set up two-factor before it may use the API."
    )
    password_change_required = graphene.Boolean(
        description="The account must choose a new password before it may use the API."
    )
    password_expires_at = graphene.DateTime(description="Null when passwords do not expire.")
    restriction_code = graphene.Int(description="8015 / 8016 while the session is restricted.")

    def resolve_total_authorities(self, info) -> int:
        return len(self.get("authorities") or [])


class MfaSetupType(graphene.ObjectType):
    """Enrolment payload — the shared secret and the otpauth URI to render as a QR."""

    secret = graphene.String()
    otpauth_uri = graphene.String()


class MfaRecoveryType(graphene.ObjectType):
    """One-time recovery codes, returned once at enrolment / regeneration."""

    recovery_codes = graphene.List(graphene.String)


class SessionType(graphene.ObjectType):
    """One live access token (a signed-in session), described by the sign-in
    that started it so a person can tell their devices apart."""

    id = graphene.String()
    created = graphene.DateTime()
    expires = graphene.DateTime()
    scope = graphene.String()
    ip_address = graphene.String(description="Where the session was signed in from.")
    browser = graphene.String()
    os = graphene.String()
    device = graphene.String()
    provider = graphene.String(description="How the person signed in.")
    label = graphene.String(description="`Chrome 128 · Windows 10`, or 'Unknown device'.")
    is_current = graphene.Boolean(description="True for the session making this request.")

    def resolve_id(self, info) -> str:
        return str(self.id)


class RevokedSessionsType(graphene.ObjectType):
    revoked = graphene.Int()
    signed_out = graphene.Boolean(description="True when the caller's own session ended too.")


class LoginHistoryType(DjangoObjectType):
    user_uid = graphene.UUID()
    user_name = graphene.String()
    user_email = graphene.String()
    label = graphene.String()

    class Meta:
        model = UserLoginHistory
        fields = (
            "id",
            "logged_in_at",
            "ip_address",
            "user_agent",
            "browser",
            "os",
            "device",
            "provider",
            "is_new_device",
        )

    def resolve_user_uid(self, info):
        return self.user.uid if self.user_id else None

    def resolve_user_name(self, info):
        return self.user.get_full_name() if self.user_id else ""

    def resolve_user_email(self, info):
        return self.user.email if self.user_id else ""

    def resolve_label(self, info):
        return self.label


class AuthenticationEventType(DjangoObjectType):
    event_type = AuthEventEnum()
    user_uid = graphene.UUID()
    user_name = graphene.String()
    metadata = GenericScalar()

    class Meta:
        model = AuthenticationAudit
        fields = (
            "uid",
            "email",
            "event_type",
            "status",
            "ip_address",
            "user_agent",
            "error_message",
            "metadata",
            "request_id",
            "created_at",
        )

    def resolve_user_uid(self, info):
        return self.user.uid if self.user_id else None

    def resolve_user_name(self, info):
        return self.user.get_full_name() if self.user_id else ""


class LdapConfigType(DjangoObjectType):
    """Directory configuration — deliberately omits the encrypted bind password."""

    has_bind_password = graphene.Boolean()
    default_role = graphene.Field(RoleType)

    class Meta:
        model = LdapConfig
        fields = (
            "uid",
            "name",
            "ldap_url",
            "bind_dn",
            "search_base",
            "email_attribute",
            "email_domain",
            "is_active",
            "active",
            "created_at",
        )

    def resolve_has_bind_password(self, info):
        return bool(self.bind_password_encrypted)


class SignInProviderType(graphene.ObjectType):
    """An external provider the sign-in page can offer."""

    key = graphene.String()
    label = graphene.String()
    issuer = graphene.String()
    client_id = graphene.String()
