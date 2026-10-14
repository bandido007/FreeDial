"""GraphQL mutations for the users domain.

Mutations are deliberately thin: validate the envelope of arguments, delegate to
the services for the actual work, and wrap the outcome. Business rules and
invariants live in the services, not here.

Surface:
  * Registration — register / validateRegistrationOtp / resendRegistrationOtp
  * Self-service — updateMyProfile, e-mail change, phone verification,
                   acceptConsents, account deletion
  * Administration — createUser, updateUser, invitations, activate/deactivate,
                     account status, delete/restore, assignRolesToUser
"""

import logging

import graphene

from apps.core.decorators import authenticated, has_permission, log_errors_and_warnings
from apps.core.enums import AccountStatusEnum, AccountTypeChoices
from apps.core.graphql.context import current_user, username
from apps.core.graphql.response import Response
from apps.core.hooks import forget_permissions
from apps.core.ratelimit import throttle, throttle_scope
from apps.core.utils import Utils, get_client_ip
from apps.users import consent_services, otp_services, registration_services, services
from apps.users.inputs import (
    AssignRolesInput,
    CreateUserInput,
    RegisterInput,
    UpdateProfileInput,
    UpdateUserInput,
    ValidateOtpInput,
)
from apps.users.permissions import ASSIGN_USER_ROLES, CREATE_USER, DELETE_USER, EDIT_USER
from apps.users.types import UserType

logger = logging.getLogger(__name__)

# Platform operators (System origin) may always manage accounts and roles, so a
# mistake in the role editor can never lock everyone out.
ADMIN_TYPES = [AccountTypeChoices.System.value]


def request_meta(info) -> tuple[str | None, str]:
    """(client IP, user-agent) from the GraphQL request — for throttling / audit."""
    request = getattr(info, "context", None)
    if request is None:
        return None, ""
    return get_client_ip(request), request.META.get("HTTP_USER_AGENT", "")


# ── Registration ───────────────────────────────────────────────────────────
class Register(graphene.Mutation):
    """Public self-registration. Creates an External account."""

    class Arguments:
        input = RegisterInput(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @log_errors_and_warnings
    def mutate(cls, root, info, input):
        if not Utils.empty_and_null_checker(input, ["email", "password", "confirm_password"]):
            return Response.build_response(code=9005, info=info)

        # `confirm_password` is a presentation-only echo: matched here at the
        # input boundary (not persisted, never reaches the domain service).
        if input.password != input.confirm_password:
            return Response.build_response(code=9012, info=info)

        ip, user_agent = request_meta(info)
        # Anti-abuse: cap registrations per IP and per email before doing any work.
        throttle_scope("register", ip)
        throttle("register-email", (input.email or "").lower(), limit=3, window=300)

        user = registration_services.register_local_user(
            email=input.email,
            password=input.password,
            first_name=input.first_name or "",
            middle_name=input.middle_name or "",
            last_name=input.last_name or "",
            phone=input.phone or "",
            date_of_birth=input.date_of_birth,
            accepted_terms=bool(input.accepted_terms),
            ip=ip,
            user_agent=user_agent,
        )
        logger.info("[Register] user %s", user.pk)
        # Send the verification code over email + SMS and reflect the delivery
        # outcome in the message. Account creation succeeds regardless.
        outcome = otp_services.issue_registration_otp(user)
        return Response.build_response(
            code=9001,
            info=info,
            data=user,
            custom_message=f"Account created. {outcome['message']}",
        )


class ValidateRegistrationOtp(graphene.Mutation):
    """Validate the registration code and mark the account verified (public)."""

    class Arguments:
        input = ValidateOtpInput(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @log_errors_and_warnings
    def mutate(cls, root, info, input):
        if not Utils.empty_and_null_checker(input, ["email", "otp"]):
            return Response.build_response(code=9005, info=info)
        ip, _ = request_meta(info)
        throttle_scope("otp", ip)
        user = otp_services.validate_registration_otp(email=input.email, code=input.otp)
        return Response.build_response(
            code=9001, info=info, data=user, custom_message="Your account has been verified."
        )


class ResendRegistrationOtp(graphene.Mutation):
    """Re-send the registration code to the account's email + phone (public)."""

    class Arguments:
        email = graphene.String(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @log_errors_and_warnings
    def mutate(cls, root, info, email):
        ip, _ = request_meta(info)
        throttle_scope("otp", ip)
        outcome = otp_services.resend_registration_otp(email)
        return Response.build_response(code=9001, info=info, custom_message=outcome["message"])


# ── Self-service ───────────────────────────────────────────────────────────
class UpdateMyProfile(graphene.Mutation):
    """Edit my own basic details (any signed-in user)."""

    class Arguments:
        input = UpdateProfileInput(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @authenticated()
    @log_errors_and_warnings
    def mutate(cls, root, info, input):
        user = services.update_own_profile(
            user=current_user(info),
            first_name=input.first_name,
            middle_name=input.middle_name,
            last_name=input.last_name,
            phone=input.phone,
            gender=input.gender,
            photo=input.photo,
            date_of_birth=input.date_of_birth,
        )
        return Response.build_response(code=9001, info=info, data=user)


class RequestEmailChange(graphene.Mutation):
    """Start changing my e-mail address: a code is sent to the NEW address."""

    class Arguments:
        new_email = graphene.String(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @authenticated()
    @log_errors_and_warnings
    def mutate(cls, root, info, new_email):
        user = current_user(info)
        otp_services.enforce_resend_limits(user.email)
        outcome = services.request_email_change(user=user, new_email=new_email)
        return Response.build_response(code=9001, info=info, data=user, custom_message=outcome["message"])


class ConfirmEmailChange(graphene.Mutation):
    """Confirm the pending e-mail address with its code."""

    class Arguments:
        otp = graphene.String(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @authenticated()
    @log_errors_and_warnings
    def mutate(cls, root, info, otp):
        user = services.confirm_email_change(user=current_user(info), code=otp)
        return Response.build_response(
            code=9001,
            info=info,
            data=user,
            custom_message="Your email address has been changed. Please sign in again.",
        )


class RequestPhoneVerification(graphene.Mutation):
    """Send a code to my phone to prove it is mine."""

    Output = Response.get_response_type(UserType)

    @classmethod
    @authenticated()
    @log_errors_and_warnings
    def mutate(cls, root, info):
        user = current_user(info)
        otp_services.enforce_resend_limits(user.email)
        outcome = services.request_phone_verification(user=user)
        return Response.build_response(code=9001, info=info, data=user, custom_message=outcome["message"])


class ConfirmPhoneVerification(graphene.Mutation):
    class Arguments:
        otp = graphene.String(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @authenticated()
    @log_errors_and_warnings
    def mutate(cls, root, info, otp):
        user = services.confirm_phone_verification(user=current_user(info), code=otp)
        return Response.build_response(
            code=9001, info=info, data=user, custom_message="Your phone number has been verified."
        )


class AcceptConsents(graphene.Mutation):
    """Accept the current Terms of Service and Privacy notice."""

    Output = Response.get_response_type(UserType)

    @classmethod
    @authenticated()
    @log_errors_and_warnings
    def mutate(cls, root, info):
        user = current_user(info)
        ip, user_agent = request_meta(info)
        consent_services.accept_current_consents(user=user, ip=ip, user_agent=user_agent)
        return Response.build_response(code=9001, info=info, data=user)


class RequestAccountDeletion(graphene.Mutation):
    """Step one of deleting my account: send me the confirmation code."""

    Output = Response.get_response_type(UserType)

    @classmethod
    @authenticated()
    @log_errors_and_warnings
    def mutate(cls, root, info):
        user = current_user(info)
        otp_services.enforce_resend_limits(user.email)
        outcome = services.request_account_deletion(user=user)
        return Response.build_response(code=9001, info=info, custom_message=outcome["message"])


class DeleteMyAccount(graphene.Mutation):
    """Delete my own account (confirmed by the mailed code, or my password)."""

    class Arguments:
        password = graphene.String()
        otp = graphene.String(description="The confirmation code from requestAccountDeletion")

    Output = Response.get_response_type(UserType)

    @classmethod
    @authenticated()
    @log_errors_and_warnings
    def mutate(cls, root, info, password="", otp=""):
        services.delete_my_account(user=current_user(info), password=password or "", code=otp or "")
        return Response.build_response(code=9001, info=info, custom_message="Your account has been deleted.")


# ── Administration ─────────────────────────────────────────────────────────
class CreateUser(graphene.Mutation):
    """Create an account for someone and invite them to set their password."""

    class Arguments:
        input = CreateUserInput(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @has_permission(permissions=[CREATE_USER], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, input):
        if not Utils.empty_and_null_checker(input, ["email", "first_name", "last_name"]):
            return Response.build_response(code=9005, info=info)
        actor = current_user(info)
        # Granting roles is a separate, weightier capability than creating an
        # account: without it the account is created with no roles.
        may_assign = ASSIGN_USER_ROLES in _codes(info) or actor.account_type in ADMIN_TYPES
        user, sent = registration_services.create_user(
            email=input.email,
            first_name=input.first_name,
            last_name=input.last_name,
            middle_name=input.middle_name or "",
            phone=input.phone or "",
            gender=input.gender or "",
            date_of_birth=input.date_of_birth,
            account_type=input.account_type or AccountTypeChoices.Internal.value,
            role_uids=(input.role_uids or []) if may_assign else [],
            actor=actor,
            send_invitation=input.send_invitation is not False,
        )
        if input.send_invitation is False:
            message = "Account created. No invitation was sent."
        elif sent:
            message = f"Account created. An invitation was sent to {user.email}."
        else:
            message = (
                "Account created, but the invitation could not be delivered. "
                "Check the mail settings, then send the invitation again."
            )
        if input.role_uids and not may_assign:
            message += " Roles were not assigned: you are not allowed to assign roles."
        logger.info("[CreateUser] user %s by %s", user.pk, username(info))
        return Response.build_response(code=9001, info=info, data=user, custom_message=message)


def _codes(info) -> set[str]:
    from apps.core.hooks import resolve_permissions

    return resolve_permissions(current_user(info), info.context)


class UpdateUser(graphene.Mutation):
    """Correct a person's details."""

    class Arguments:
        input = UpdateUserInput(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @has_permission(permissions=[EDIT_USER], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, input):
        user = services.update_user(
            uid=input.uid,
            actor=current_user(info),
            first_name=input.first_name,
            middle_name=input.middle_name,
            last_name=input.last_name,
            phone=input.phone,
            gender=input.gender,
            photo=input.photo,
            date_of_birth=input.date_of_birth,
            account_type=input.account_type,
        )
        return Response.build_response(code=9001, info=info, data=user)


class ResendUserInvitation(graphene.Mutation):
    """Send the invitation again to an account that was never activated."""

    class Arguments:
        uid = graphene.UUID(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @has_permission(permissions=[CREATE_USER], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, uid):
        throttle("resend-invitation", str(uid), limit=5, window=3600)
        user, sent = registration_services.resend_invitation(uid=uid, actor=current_user(info))
        message = (
            f"A new invitation was sent to {user.email}."
            if sent
            else "The invitation could not be delivered. Check the mail settings and try again."
        )
        return Response.build_response(
            code=9001 if sent else 9004, info=info, data=user, custom_message=message
        )


class CancelUserInvitation(graphene.Mutation):
    """Withdraw an invitation nobody accepted; the account is removed."""

    class Arguments:
        uid = graphene.UUID(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @has_permission(permissions=[DELETE_USER], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, uid):
        registration_services.cancel_invitation(uid=uid, actor=current_user(info))
        return Response.build_response(code=9001, info=info, custom_message="The invitation was withdrawn.")


class DeactivateUser(graphene.Mutation):
    class Arguments:
        uid = graphene.UUID(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @has_permission(permissions=[EDIT_USER], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, uid):
        logger.info("[DeactivateUser] %s by %s", uid, username(info))
        user = services.set_user_active(uid=uid, active=False, actor=current_user(info))
        return Response.build_response(code=9001, info=info, data=user)


class ActivateUser(graphene.Mutation):
    class Arguments:
        uid = graphene.UUID(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @has_permission(permissions=[EDIT_USER], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, uid):
        logger.info("[ActivateUser] %s by %s", uid, username(info))
        user = services.set_user_active(uid=uid, active=True, actor=current_user(info))
        return Response.build_response(code=9001, info=info, data=user)


class SetUserAccountStatus(graphene.Mutation):
    """Suspend, block or reinstate an account."""

    class Arguments:
        uid = graphene.UUID(required=True)
        status = AccountStatusEnum(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @has_permission(permissions=[EDIT_USER], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, uid, status):
        logger.info("[SetUserAccountStatus] %s -> %s by %s", uid, status, username(info))
        user = services.set_account_status(uid=uid, status=status, actor=current_user(info))
        return Response.build_response(code=9001, info=info, data=user)


class DeleteUser(graphene.Mutation):
    class Arguments:
        uid = graphene.UUID(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @has_permission(permissions=[DELETE_USER], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, uid):
        logger.info("[DeleteUser] %s by %s", uid, username(info))
        user = services.soft_delete_user(uid=uid, actor=current_user(info))
        return Response.build_response(code=9001, info=info, data=user)


class RestoreUser(graphene.Mutation):
    class Arguments:
        uid = graphene.UUID(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @has_permission(permissions=[DELETE_USER], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, uid):
        logger.info("[RestoreUser] %s by %s", uid, username(info))
        user = services.restore_user(uid=uid)
        return Response.build_response(code=9001, info=info, data=user)


class AssignRolesToUser(graphene.Mutation):
    """Set the roles a person holds (replace-set)."""

    class Arguments:
        input = AssignRolesInput(required=True)

    Output = Response.get_response_type(UserType)

    @classmethod
    @has_permission(permissions=[ASSIGN_USER_ROLES], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, input):
        logger.info("[AssignRolesToUser] %s by %s", input.user_uid, username(info))
        user = services.set_user_roles(
            user_uid=input.user_uid, role_uids=input.role_uids or [], actor=current_user(info)
        )
        forget_permissions(info.context)
        return Response.build_response(code=9001, info=info, data=user)


class Mutation(graphene.ObjectType):
    # Registration (public)
    register = Register.Field(description="Public. Create my own account.")
    validate_registration_otp = ValidateRegistrationOtp.Field(description="Public.")
    resend_registration_otp = ResendRegistrationOtp.Field(description="Public.")
    # Self-service
    update_my_profile = UpdateMyProfile.Field(description="Authenticated.")
    request_email_change = RequestEmailChange.Field(description="Authenticated.")
    confirm_email_change = ConfirmEmailChange.Field(description="Authenticated.")
    request_phone_verification = RequestPhoneVerification.Field(description="Authenticated.")
    confirm_phone_verification = ConfirmPhoneVerification.Field(description="Authenticated.")
    accept_consents = AcceptConsents.Field(description="Authenticated.")
    request_account_deletion = RequestAccountDeletion.Field(description="Authenticated.")
    delete_my_account = DeleteMyAccount.Field(description="Authenticated.")
    # Administration
    create_user = CreateUser.Field(description="permissions=['can_create_user']")
    update_user = UpdateUser.Field(description="permissions=['can_edit_user']")
    resend_user_invitation = ResendUserInvitation.Field(description="permissions=['can_create_user']")
    cancel_user_invitation = CancelUserInvitation.Field(description="permissions=['can_delete_user']")
    deactivate_user = DeactivateUser.Field(description="permissions=['can_edit_user']")
    activate_user = ActivateUser.Field(description="permissions=['can_edit_user']")
    set_user_account_status = SetUserAccountStatus.Field(description="permissions=['can_edit_user']")
    delete_user = DeleteUser.Field(description="permissions=['can_delete_user']")
    restore_user = RestoreUser.Field(description="permissions=['can_delete_user']")
    assign_roles_to_user = AssignRolesToUser.Field(description="permissions=['can_assign_user_roles']")
