"""Enumerations for the authentication domain."""

import graphene

from apps.core.enums import BaseEnum


class AuthEventChoices(BaseEnum):
    """What the authentication audit records."""

    # Tokens
    TokenGenerated = "token_generated"
    TokenRefreshed = "token_refreshed"
    TokenRevoked = "token_revoked"
    FailedLogin = "failed_login"
    AccountLocked = "account_locked"
    # Accounts
    Registered = "registered"
    Invited = "invited"
    # Passwords
    PasswordChanged = "password_changed"
    PasswordResetRequested = "password_reset_requested"
    PasswordResetCompleted = "password_reset_completed"
    # Two-factor authentication
    MfaEnabled = "mfa_enabled"
    MfaDisabled = "mfa_disabled"
    MfaFailed = "mfa_failed"
    MfaReset = "mfa_reset"
    MfaRecoveryCodeUsed = "mfa_recovery_code_used"
    # Sessions
    SessionRevoked = "session_revoked"
    SessionsRevoked = "sessions_revoked"
    # Identity
    EmailChanged = "email_changed"
    # Single sign-on: the user approved an external system's sign-in request.
    ConsentGranted = "consent_granted"


AuthEventEnum = graphene.Enum.from_enum(AuthEventChoices)

STATUS_SUCCESS = "success"
STATUS_FAILURE = "failure"


class TokenPurposeChoices(BaseEnum):
    """What a single-use emailed link is for."""

    RESET = "reset"  # forgot-password
    INVITE = "invite"  # activate an account an administrator created


class MfaMethodChoices(BaseEnum):
    TOTP = "TOTP"  # authenticator app (RFC 6238)
