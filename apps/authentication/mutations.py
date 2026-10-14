"""GraphQL mutations for the authentication domain.

Surface:
  * Passwords   — requestPasswordReset / setPassword (public), changeMyPassword
  * Two-factor  — begin / confirm enrolment, regenerate recovery codes, disable
  * Sessions    — revokeMySession, revokeAllMySessions
  * Oversight   — resetUserMfa, sendUserPasswordReset, requireUserPasswordChange,
                  revokeUserSessions
  * Directories — LDAP configuration CRUD, pullLdapUsers
"""

import logging

import graphene

from apps.authentication import (
    directory_services,
    gates,
    mfa_services,
    password_services,
    session_services,
)
from apps.authentication.inputs import ChangePasswordInput, LdapConfigInput, SetPasswordInput
from apps.authentication.permissions import (
    MANAGE_LDAP_CONFIG,
    PULL_LDAP_USERS,
    RESET_USER_MFA,
    RESET_USER_PASSWORD,
    REVOKE_USER_SESSIONS,
)
from apps.authentication.types import (
    LdapConfigType,
    MfaRecoveryType,
    MfaSetupType,
    RevokedSessionsType,
)
from apps.core.decorators import authenticated, has_permission, log_errors_and_warnings
from apps.core.enums import AccountTypeChoices
from apps.core.graphql.context import current_user, username
from apps.core.graphql.response import Response
from apps.core.ratelimit import throttle, throttle_scope
from apps.core.utils import Utils, get_client_ip
from apps.users import selectors as user_selectors
from apps.users.permissions import EDIT_USER
from apps.users.types import UserType

logger = logging.getLogger(__name__)

ADMIN_TYPES = [AccountTypeChoices.System.value]
PASSWORDS_DO_NOT_MATCH = 9012


def _origin(info) -> tuple[str | None, str]:
    request = getattr(info, "context", None)
    if request is None:
        return None, ""
    return get_client_ip(request), request.META.get("HTTP_USER_AGENT", "")


def _visible_user(info, uid):
    """The account an administrator is acting on — within what they may see."""
    from apps.core.exceptions import PermissionDeniedError

    user = user_selectors.get_user(uid)
    if not user_selectors.is_visible_to(current_user(info), user):
        raise PermissionDeniedError("You are not allowed to manage this account.")
    return user


# ── Passwords ──────────────────────────────────────────────────────────────
class RequestPasswordReset(graphene.Mutation):
    """Public: e-mail a password-reset link to a local account.

    Always succeeds with a generic acknowledgement — it never reveals whether
    the e-mail is registered.
    """

    class Arguments:
        email = graphene.String(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @log_errors_and_warnings
    def mutate(cls, root, info, email):
        ip, user_agent = _origin(info)
        throttle_scope("password-reset", ip)
        throttle("password-reset-email", (email or "").lower(), limit=3, window=900)
        outcome = password_services.request_password_reset(email=email, ip=ip, user_agent=user_agent)
        return Response.build_response(code=9001, info=info, custom_message=outcome["message"])


class SetPassword(graphene.Mutation):
    """Public: set a password using a valid token (reset link or invitation)."""

    class Arguments:
        input = SetPasswordInput(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @log_errors_and_warnings
    def mutate(cls, root, info, input):
        if not Utils.empty_and_null_checker(input, ["token", "new_password", "confirm_password"]):
            return Response.build_response(code=9005, info=info)
        # confirm_password is a presentation-only echo, matched at the boundary.
        if input.new_password != input.confirm_password:
            return Response.build_response(code=PASSWORDS_DO_NOT_MATCH, info=info)

        ip, user_agent = _origin(info)
        throttle_scope("password-reset", ip)
        user = password_services.set_password_with_token(
            token=input.token, new_password=input.new_password, ip=ip, user_agent=user_agent
        )
        return Response.build_response(
            code=9001,
            info=info,
            data=user,
            custom_message="Your password has been set. You can now sign in.",
        )


class ChangeMyPassword(graphene.Mutation):
    """Change my own password. Every other device is signed out."""

    class Arguments:
        input = ChangePasswordInput(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @authenticated()
    @log_errors_and_warnings
    def mutate(cls, root, info, input):
        if input.new_password != input.confirm_password:
            return Response.build_response(code=PASSWORDS_DO_NOT_MATCH, info=info)
        user = current_user(info)
        throttle("change-password", user.pk, limit=10, window=900)
        user = password_services.change_own_password(
            user=user,
            old_password=input.old_password,
            new_password=input.new_password,
            request=info.context,
        )
        gates.forget_restriction(info.context)
        return Response.build_response(
            code=9001, info=info, data=user, custom_message="Your password has been changed."
        )


# ── Two-factor authentication ──────────────────────────────────────────────
class BeginMfaEnrollment(graphene.Mutation):
    """Start setting up two-factor: returns the secret and the QR-code URI."""

    Output = Response.get_response_type(MfaSetupType)

    @classmethod
    @authenticated()
    @log_errors_and_warnings
    def mutate(cls, root, info):
        setup = mfa_services.begin_enrollment(user=current_user(info))
        return Response.build_response(code=9001, info=info, data=setup)


class ConfirmMfaEnrollment(graphene.Mutation):
    """Enter the first code: two-factor is switched on and the recovery codes
    are returned — once."""

    class Arguments:
        otp = graphene.String(required=True)

    Output = Response.get_response_type(MfaRecoveryType)

    @classmethod
    @authenticated()
    @log_errors_and_warnings
    def mutate(cls, root, info, otp):
        user = current_user(info)
        throttle("mfa-confirm", user.pk, limit=10, window=600)
        codes = mfa_services.confirm_enrollment(user=user, code=otp, request=info.context)
        gates.forget_restriction(info.context)
        return Response.build_response(
            code=9001,
            info=info,
            data={"recovery_codes": codes},
            custom_message="Two-factor authentication is on. Save your recovery codes now.",
        )


class RegenerateMfaRecoveryCodes(graphene.Mutation):
    class Arguments:
        otp = graphene.String(required=True)

    Output = Response.get_response_type(MfaRecoveryType)

    @classmethod
    @authenticated()
    @log_errors_and_warnings
    def mutate(cls, root, info, otp):
        user = current_user(info)
        throttle("mfa-confirm", user.pk, limit=10, window=600)
        codes = mfa_services.regenerate_recovery_codes(user=user, code=otp, request=info.context)
        return Response.build_response(code=9001, info=info, data={"recovery_codes": codes})


class DisableMfa(graphene.Mutation):
    class Arguments:
        otp = graphene.String(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @authenticated()
    @log_errors_and_warnings
    def mutate(cls, root, info, otp):
        user = current_user(info)
        throttle("mfa-confirm", user.pk, limit=10, window=600)
        user = mfa_services.disable(user=user, code=otp, request=info.context)
        return Response.build_response(code=9001, info=info, data=user)


# ── Sessions ───────────────────────────────────────────────────────────────
class RevokeMySession(graphene.Mutation):
    """Sign one of my devices out."""

    class Arguments:
        session_id = graphene.String(required=True)

    Output = Response.get_response_type(RevokedSessionsType)

    @classmethod
    @authenticated()
    @log_errors_and_warnings
    def mutate(cls, root, info, session_id):
        own = session_services.revoke_session(
            user=current_user(info), session_id=session_id, request=info.context
        )
        return Response.build_response(code=9001, info=info, data={"revoked": 1, "signed_out": own})


class RevokeAllMySessions(graphene.Mutation):
    """Sign me out everywhere (by default, everywhere except here)."""

    class Arguments:
        include_current = graphene.Boolean(default_value=False)

    Output = Response.get_response_type(RevokedSessionsType)

    @classmethod
    @authenticated()
    @log_errors_and_warnings
    def mutate(cls, root, info, include_current=False):
        count = session_services.revoke_all_my_sessions(
            user=current_user(info), request=info.context, keep_current=not include_current
        )
        return Response.build_response(
            code=9001,
            info=info,
            data={"revoked": count, "signed_out": bool(include_current)},
            custom_message=f"{count} session(s) signed out.",
        )


# ── Oversight ──────────────────────────────────────────────────────────────
class ResetUserMfa(graphene.Mutation):
    """Turn two-factor off for someone who is locked out (lost device)."""

    class Arguments:
        uid = graphene.UUID(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @has_permission(permissions=[RESET_USER_MFA, EDIT_USER], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, uid):
        logger.info("[ResetUserMfa] %s by %s", uid, username(info))
        user = mfa_services.admin_reset(
            user=_visible_user(info, uid), actor=current_user(info), request=info.context
        )
        return Response.build_response(code=9001, info=info, data=user)


class SendUserPasswordReset(graphene.Mutation):
    """E-mail someone a link to choose a new password."""

    class Arguments:
        uid = graphene.UUID(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @has_permission(permissions=[RESET_USER_PASSWORD], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, uid):
        throttle("admin-password-reset", str(uid), limit=5, window=3600)
        user = _visible_user(info, uid)
        sent = password_services.admin_send_password_reset(
            user=user, actor=current_user(info), request=info.context
        )
        message = (
            f"A reset link was sent to {user.email}."
            if sent
            else "The reset link could not be delivered. Check the mail settings and try again."
        )
        return Response.build_response(
            code=9001 if sent else 9004, info=info, data=user, custom_message=message
        )


class RequireUserPasswordChange(graphene.Mutation):
    """Make someone choose a new password at their next sign-in."""

    class Arguments:
        uid = graphene.UUID(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @has_permission(permissions=[RESET_USER_PASSWORD], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, uid):
        user = password_services.require_password_change(
            user=_visible_user(info, uid), actor=current_user(info)
        )
        return Response.build_response(code=9001, info=info, data=user)


class RevokeUserSessions(graphene.Mutation):
    """Sign another person out everywhere."""

    class Arguments:
        uid = graphene.UUID(required=True)

    Output = Response.get_response_type(RevokedSessionsType)

    @classmethod
    @has_permission(permissions=[REVOKE_USER_SESSIONS], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, uid):
        actor = current_user(info)
        user = _visible_user(info, uid)
        count = session_services.revoke_sessions_of(user=user, actor=actor, request=info.context)
        return Response.build_response(
            code=9001,
            info=info,
            data={"revoked": count, "signed_out": user.pk == actor.pk},
            custom_message=f"{count} session(s) signed out.",
        )


# ── Directories ────────────────────────────────────────────────────────────
class CreateLdapConfig(graphene.Mutation):
    class Arguments:
        input = LdapConfigInput(required=True)

    Output = Response.get_response_type(LdapConfigType)

    @classmethod
    @has_permission(permissions=[MANAGE_LDAP_CONFIG], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, input):
        if not Utils.empty_and_null_checker(input, ["ldap_url", "bind_dn"]):
            return Response.build_response(code=9005, info=info)
        config = directory_services.create_config(
            name=input.name or "",
            ldap_url=input.ldap_url,
            bind_dn=input.bind_dn,
            bind_password=input.bind_password or "",
            search_base=input.search_base or "",
            email_attribute=input.email_attribute or "mail",
            email_domain=input.email_domain or "",
            default_role_uid=input.default_role_uid,
            is_active=input.is_active if input.is_active is not None else True,
        )
        return Response.build_response(code=9001, info=info, data=config)


class UpdateLdapConfig(graphene.Mutation):
    class Arguments:
        input = LdapConfigInput(required=True)

    Output = Response.get_response_type(LdapConfigType)

    @classmethod
    @has_permission(permissions=[MANAGE_LDAP_CONFIG], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, input):
        if not Utils.empty_and_null_checker(input, ["uid"]):
            return Response.build_response(code=9005, info=info)
        config = directory_services.update_config(
            uid=input.uid,
            name=input.name,
            ldap_url=input.ldap_url,
            bind_dn=input.bind_dn,
            bind_password=input.bind_password,
            search_base=input.search_base,
            email_attribute=input.email_attribute,
            email_domain=input.email_domain,
            default_role_uid=input.default_role_uid,
            clear_default_role=bool(input.clear_default_role),
            is_active=input.is_active,
        )
        return Response.build_response(code=9001, info=info, data=config)


class DeleteLdapConfig(graphene.Mutation):
    class Arguments:
        uid = graphene.UUID(required=True)

    Output = Response.get_response_type(LdapConfigType)

    @classmethod
    @has_permission(permissions=[MANAGE_LDAP_CONFIG], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, uid):
        config = directory_services.delete_config(uid=uid)
        return Response.build_response(code=9001, info=info, data=config)


class PullLdapUsers(graphene.Mutation):
    """Create an account for every person in a directory who has none."""

    class Arguments:
        config_uid = graphene.UUID(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @has_permission(permissions=[PULL_LDAP_USERS], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, config_uid):
        created = directory_services.pull_users(config_uid=config_uid, actor=current_user(info))
        message = (
            f"{len(created)} account(s) created. Review and activate them."
            if created
            else "No new people were found in the directory."
        )
        return Response.build_response(code=9001, info=info, data_list=created, custom_message=message)


class Mutation(graphene.ObjectType):
    # Passwords
    request_password_reset = RequestPasswordReset.Field(description="Public.")
    set_password = SetPassword.Field(description="Public.")
    change_my_password = ChangeMyPassword.Field(description="Authenticated.")
    # Two-factor
    begin_mfa_enrollment = BeginMfaEnrollment.Field(description="Authenticated.")
    confirm_mfa_enrollment = ConfirmMfaEnrollment.Field(description="Authenticated.")
    regenerate_mfa_recovery_codes = RegenerateMfaRecoveryCodes.Field(description="Authenticated.")
    disable_mfa = DisableMfa.Field(description="Authenticated.")
    # Sessions
    revoke_my_session = RevokeMySession.Field(description="Authenticated.")
    revoke_all_my_sessions = RevokeAllMySessions.Field(description="Authenticated.")
    # Oversight
    reset_user_mfa = ResetUserMfa.Field(description="permissions=['can_reset_user_mfa','can_edit_user']")
    send_user_password_reset = SendUserPasswordReset.Field(
        description="permissions=['can_reset_user_password']"
    )
    require_user_password_change = RequireUserPasswordChange.Field(
        description="permissions=['can_reset_user_password']"
    )
    revoke_user_sessions = RevokeUserSessions.Field(description="permissions=['can_revoke_user_sessions']")
    # Directories
    create_ldap_config = CreateLdapConfig.Field(description="permissions=['can_manage_ldap_config']")
    update_ldap_config = UpdateLdapConfig.Field(description="permissions=['can_manage_ldap_config']")
    delete_ldap_config = DeleteLdapConfig.Field(description="permissions=['can_manage_ldap_config']")
    pull_ldap_users = PullLdapUsers.Field(description="permissions=['can_pull_ldap_users']")
