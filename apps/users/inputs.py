"""GraphQL input types (input DTOs) for the users domain."""

import graphene

from apps.core.enums import AccountStatusEnum, AccountTypeEnum, AuthProviderEnum, GenderEnum


# ── User management ─────────────────────────────────────────────────────────
class UserFilterInput(graphene.InputObjectType):
    """Filters for the user-management listing (all optional, ANDed together).

    ``search`` is a free-text match across email + names + phone; the remaining
    clauses narrow by exact classification. ``deleted=true`` surfaces
    soft-deleted accounts (otherwise hidden) for authorised callers.
    """

    uid = graphene.UUID()
    search = graphene.String(description="Free text across email, names and phone")
    account_type = AccountTypeEnum()
    auth_provider = AuthProviderEnum()
    account_status = AccountStatusEnum()
    gender = GenderEnum()
    role_uid = graphene.UUID(description="Users holding this role")
    is_active = graphene.Boolean()
    is_verified = graphene.Boolean()
    is_staff = graphene.Boolean()
    mfa_enabled = graphene.Boolean()
    invitation_pending = graphene.Boolean(description="Only accounts still waiting to be activated")
    deleted = graphene.Boolean(description="Include only soft-deleted accounts")


class UpdateProfileInput(graphene.InputObjectType):
    """Self-service profile edit (PATCH: omit a field to keep it)."""

    first_name = graphene.String()
    middle_name = graphene.String()
    last_name = graphene.String()
    phone = graphene.String()
    gender = GenderEnum()
    photo = graphene.String()
    date_of_birth = graphene.Date()


class CreateUserInput(graphene.InputObjectType):
    """An administrator creates an account; its owner is invited to activate it."""

    email = graphene.String(required=True)
    first_name = graphene.String(required=True)
    last_name = graphene.String(required=True)
    middle_name = graphene.String()
    phone = graphene.String()
    gender = GenderEnum()
    date_of_birth = graphene.Date()
    account_type = AccountTypeEnum(description="Defaults to Internal")
    role_uids = graphene.List(graphene.UUID, description="Roles the account starts with")
    send_invitation = graphene.Boolean(default_value=True)


class UpdateUserInput(graphene.InputObjectType):
    """An administrator corrects a person's details (PATCH: omit to keep)."""

    uid = graphene.UUID(required=True)
    first_name = graphene.String()
    middle_name = graphene.String()
    last_name = graphene.String()
    phone = graphene.String()
    gender = GenderEnum()
    photo = graphene.String()
    date_of_birth = graphene.Date()
    account_type = AccountTypeEnum()


class AssignRolesInput(graphene.InputObjectType):
    """Set the roles a person holds (replace-set)."""

    user_uid = graphene.UUID(required=True)
    role_uids = graphene.List(graphene.UUID, required=True)


# ── Registration ───────────────────────────────────────────────────────────
class RegisterInput(graphene.InputObjectType):
    email = graphene.String(required=True)
    password = graphene.String(required=True)
    confirm_password = graphene.String(required=True)
    first_name = graphene.String(required=True)
    last_name = graphene.String(required=True)
    phone = graphene.String(required=True)
    middle_name = graphene.String()
    date_of_birth = graphene.Date()
    # Legal consent — must be true; recorded as an auditable UserConsent row.
    accepted_terms = graphene.Boolean(
        required=True, description="Acceptance of the Terms of Service and Privacy Policy"
    )


# ── Verification codes ──────────────────────────────────────────────────────
class ValidateOtpInput(graphene.InputObjectType):
    email = graphene.String(required=True)
    otp = graphene.String(required=True, description="The 6-digit code")
