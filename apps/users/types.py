"""GraphQL output types (response DTOs) for the users domain."""

import graphene
from graphene_django import DjangoObjectType

from apps.core.enums import AccountStatusEnum, AccountTypeEnum, AuthProviderEnum
from apps.rbac.types import RoleType
from apps.users import consent_services
from apps.users.enums import ConsentDocumentEnum
from apps.users.models import User, UserConsent


class UserType(DjangoObjectType):
    full_name = graphene.String()
    account_type = AccountTypeEnum()
    auth_provider = AuthProviderEnum()
    account_status = AccountStatusEnum()
    roles = graphene.List(RoleType, description="Roles currently granted to the user")
    is_profile_complete = graphene.Boolean(description="True when every required profile field is filled.")
    missing_profile_fields = graphene.List(
        graphene.String, description="Required profile fields still blank (empty = complete)."
    )
    pending_consents = graphene.List(
        graphene.String, description="Consent documents whose current version is not yet accepted."
    )
    mfa_recovery_codes_remaining = graphene.Int(
        description="How many unused two-factor recovery codes are left."
    )
    has_password = graphene.Boolean(
        description="False for external and directory accounts — they have no local password."
    )
    invitation_pending = graphene.Boolean(
        description="Created by an administrator and not yet activated by its owner."
    )

    class Meta:
        model = User
        fields = (
            "uid",
            "email",
            "pending_email",
            "first_name",
            "middle_name",
            "last_name",
            "phone",
            "gender",
            "photo",
            "date_of_birth",
            "account_type",
            "auth_provider",
            "account_status",
            "is_active",
            "is_staff",
            "is_verified",
            "phone_verified",
            "mfa_enabled",
            "must_change_password",
            "password_changed_at",
            "invited_at",
            "last_login",
            "deleted",
            "date_joined",
            "updated_at",
        )

    def resolve_full_name(self, info) -> str:
        return self.get_full_name()

    def resolve_gender(self, info):
        return self.gender or None

    def resolve_has_password(self, info) -> bool:
        return self.has_usable_password()

    def resolve_invitation_pending(self, info) -> bool:
        return self.invitation_pending

    def resolve_is_profile_complete(self, info) -> bool:
        return self.is_profile_complete

    def resolve_missing_profile_fields(self, info) -> list[str]:
        return self.missing_profile_fields()

    def resolve_pending_consents(self, info) -> list[str]:
        return consent_services.pending_consents(self)

    def resolve_mfa_recovery_codes_remaining(self, info) -> int:
        # Recovery codes belong to apps.authentication, which sits above this
        # app; they are reached through the reverse relation it adds to the
        # user, so nothing here imports upward.
        codes = getattr(self, "mfa_recovery_codes", None)
        if codes is None:
            return 0
        return codes.filter(used_at__isnull=True).count()

    def resolve_roles(self, info):
        # user_roles is prefetched (and its default manager hides soft-deleted
        # grants) on the management listing, so this stays a single query.
        return [grant.role for grant in self.user_roles.all() if not grant.is_expired]


class UserConsentType(DjangoObjectType):
    document = ConsentDocumentEnum()

    class Meta:
        model = UserConsent
        fields = ("uid", "document", "version", "accepted_at", "ip_address", "user_agent")


class MyDataExportType(graphene.ObjectType):
    """A portable snapshot of the caller's personal data."""

    data = graphene.JSONString()
    sections = graphene.List(graphene.String, description="The parts the snapshot contains.")


class RegistrationStatusType(graphene.ObjectType):
    """What the sign-in page needs to know before anyone is signed in."""

    self_registration_open = graphene.Boolean()
    terms_version = graphene.String()
    privacy_version = graphene.String()
