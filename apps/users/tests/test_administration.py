"""Administrators managing accounts, and the users GraphQL surface."""

import pytest

from apps.core.exceptions import NotFoundError, ValidationError
from apps.users import selectors, services, signals
from apps.users.models import User

pytestmark = pytest.mark.django_db

PAGE = {"searchDto": {"pageableParam": {"pageNumber": 1, "itemsPerPage": 50, "sortBy": "email"}}}


def test_gates_sign_the_person_out_everywhere(user, admin):
    heard = []
    signals.account_gated.connect(lambda sender, **kwargs: heard.append(kwargs["reason"]), weak=False)

    services.set_user_active(uid=user.uid, active=False, actor=admin)
    services.set_account_status(uid=user.uid, status="SUSPENDED", actor=admin)
    services.set_account_status(uid=user.uid, status="BLOCKED", actor=admin)
    services.set_account_status(uid=user.uid, status="ACTIVE", actor=admin)  # reinstating gates nothing
    services.soft_delete_user(uid=user.uid, actor=admin)
    assert heard == ["deactivated", "suspended", "blocked", "deleted"]

    user.refresh_from_db()
    assert user.deleted and not user.is_active
    services.restore_user(uid=user.uid)
    user.refresh_from_db()
    assert not user.deleted and user.is_active and user.can_authenticate
    with pytest.raises(ValidationError):
        services.restore_user(uid=user.uid)
    services.set_user_active(uid=user.uid, active=True, actor=admin)
    with pytest.raises(ValidationError):
        services.set_account_status(uid=user.uid, status="ON_FIRE", actor=admin)


def test_operators_cannot_gate_themselves(admin):
    for action in (
        lambda: services.set_user_active(uid=admin.uid, active=False, actor=admin),
        lambda: services.set_account_status(uid=admin.uid, status="SUSPENDED", actor=admin),
        lambda: services.soft_delete_user(uid=admin.uid, actor=admin),
        lambda: services.update_user(uid=admin.uid, actor=admin, account_type="External"),
    ):
        with pytest.raises(ValidationError) as excinfo:
            action()
        assert excinfo.value.code == 9102


def test_an_administrator_corrects_details(user, admin):
    updated = services.update_user(uid=user.uid, actor=admin, first_name="Corrected", account_type="Internal")
    assert updated.first_name == "Corrected" and updated.account_type == "Internal"
    with pytest.raises(ValidationError):
        services.update_user(uid=user.uid, actor=admin, account_type="Alien")
    with pytest.raises(NotFoundError):
        services.update_user(uid="00000000-0000-7000-8000-000000000000", actor=admin)


def test_the_listing_filters(make_user, make_role):
    from apps.rbac import services as rbac_services

    clerk = make_user(email="clerk@example.org", account_type="Internal", first_name="Clara")
    make_user(email="guest@example.org", account_type="External", is_verified=False)
    role = make_role("Clerk", [])
    rbac_services.grant_role(clerk, role)

    class Filter:
        uid = None
        search = ""
        account_type = None
        auth_provider = None
        account_status = None
        gender = None
        role_uid = None
        is_active = None
        is_verified = None
        is_staff = None
        mfa_enabled = None
        invitation_pending = None
        deleted = None

    filtering = Filter()
    filtering.search = "clara"
    assert [u.email for u in selectors.users_queryset(selectors.build_user_filters(filtering))] == [
        clerk.email
    ]
    filtering = Filter()
    filtering.role_uid = role.uid
    assert selectors.users_queryset(selectors.build_user_filters(filtering)).count() == 1
    filtering = Filter()
    filtering.is_verified = False
    assert selectors.users_queryset(selectors.build_user_filters(filtering)).count() == 1
    assert selectors.build_user_filters(None) is not None
    assert selectors.get_user_by_email("CLERK@example.org") == clerk
    assert selectors.get_user_by_email("nobody@example.org") is None


# ── GraphQL ──────────────────────────────────────────────────────────────────
LIST = """
query Users($searchDto: SearchDataDto!, $filtering: UserFilterInput) {
  getAllUsersPageable(searchDto: $searchDto, filtering: $filtering) {
    status code content { uid email fullName accountType roles { name } invitationPending hasPassword }
    page { totalElements }
  }
}
"""


def test_listing_and_detail_need_the_permission(gql, user, make_user, grant):
    make_user()
    assert gql(LIST, PAGE, user=user)["code"] == 9000
    grant(user, "can_view_users")
    envelope = gql(LIST, PAGE, user=user)
    assert envelope["page"]["totalElements"] == 2
    assert envelope["content"][0]["roles"] == [{"name": "test-role-1"}] or envelope["content"][1]["roles"]

    detail = gql(
        "query U($uid: UUID!) { getUser(uid: $uid) { code data { email pendingConsents missingProfileFields } } }",
        {"uid": str(user.uid)},
        user=user,
    )
    assert detail["data"]["email"] == user.email
    assert detail["data"]["pendingConsents"] == ["TERMS", "PRIVACY"]


def test_deleted_accounts_are_hidden_unless_asked_for(gql, admin, user):
    services.soft_delete_user(uid=user.uid, actor=admin)
    assert gql(LIST, PAGE, user=admin)["page"]["totalElements"] == 1
    deleted = gql(LIST, {**PAGE, "filtering": {"deleted": True}}, user=admin)
    assert [row["email"] for row in deleted["content"]] == [user.email]


def test_the_export_is_a_file(gql, admin):
    envelope = gql(
        "query { exportUsers(format: CSV) { code data { fileName mimeType sizeBytes contentBase64 } } }",
        user=admin,
    )
    assert envelope["data"]["mimeType"] == "text/csv" and envelope["data"]["sizeBytes"] > 0
    assert envelope["data"]["fileName"].startswith("users-")


def test_registration_status_is_public(gql, settings):
    settings.ALLOW_SELF_REGISTRATION = False
    envelope = gql("query { getRegistrationStatus { data { selfRegistrationOpen termsVersion } } }")
    assert envelope["data"]["selfRegistrationOpen"] is False


def test_the_public_registration_journey(gql, mailbox):
    import re

    registered = gql(
        """
        mutation R($input: RegisterInput!) {
          register(input: $input) { code message data { email isVerified } }
        }""",
        {
            "input": {
                "email": "new@example.org",
                "password": "Str0ng!Passw0rd",
                "confirmPassword": "Str0ng!Passw0rd",
                "firstName": "New",
                "lastName": "Person",
                "phone": "0755123456",
                "acceptedTerms": True,
            }
        },
    )
    assert registered["code"] == 9001 and "Account created" in registered["message"]
    code = re.search(r"\b(\d{6})\b", mailbox[0].body).group(1)
    verified = gql(
        "mutation V($input: ValidateOtpInput!) { validateRegistrationOtp(input: $input) { code data { isVerified } } }",
        {"input": {"email": "new@example.org", "otp": code}},
    )
    assert verified["data"]["isVerified"] is True
    resent = gql('mutation { resendRegistrationOtp(email: "new@example.org") { code message } }')
    assert resent["message"] == "Your account is already verified."


def test_mismatched_passwords_and_missing_fields_are_caught_at_the_edge(gql):
    envelope = gql(
        "mutation R($input: RegisterInput!) { register(input: $input) { code } }",
        {
            "input": {
                "email": "new@example.org",
                "password": "Str0ng!Passw0rd",
                "confirmPassword": "different",
                "firstName": "New",
                "lastName": "Person",
                "phone": "0755123456",
                "acceptedTerms": True,
            }
        },
    )
    assert envelope["code"] == 9012


def test_self_service_mutations(gql, user, mailbox):
    updated = gql(
        "mutation P($input: UpdateProfileInput!) { updateMyProfile(input: $input) { code data { firstName } } }",
        {"input": {"firstName": "Renamed"}},
        user=user,
    )
    assert updated["data"]["firstName"] == "Renamed"
    assert (
        gql("mutation { acceptConsents { code data { pendingConsents } } }", user=user)["data"][
            "pendingConsents"
        ]
        == []
    )
    asked = gql("mutation { requestPhoneVerification { code message } }", user=user)
    assert asked["code"] == 9001
    # One code at a time per person: the next request must wait for the cool-down.
    assert gql("mutation { requestAccountDeletion { code } }", user=user)["code"] == 9018
    from django.core.cache import cache

    cache.clear()
    exported = gql("query { exportMyData { code data { sections } } }", user=user)
    assert "profile" in exported["data"]["sections"]
    started = gql("mutation { requestAccountDeletion { code message } }", user=user)
    assert started["code"] == 9001
    refused = gql('mutation { deleteMyAccount(password: "wrong") { code } }', user=user)
    assert refused["code"] == 9011


def test_administration_mutations(gql, admin, user, make_role, mailbox):
    role = make_role("Clerk", ["can_view_users"])
    created = gql(
        """
        mutation C($input: CreateUserInput!) {
          createUser(input: $input) { code message data { uid email invitationPending roles { name } } }
        }""",
        {
            "input": {
                "email": "new@example.org",
                "firstName": "New",
                "lastName": "Person",
                "roleUids": [str(role.uid)],
            }
        },
        user=admin,
    )
    assert created["data"]["invitationPending"] is True and created["data"]["roles"] == [{"name": "Clerk"}]
    assert "invitation was sent" in created["message"]
    new_uid = created["data"]["uid"]

    resent = gql(
        "mutation R($uid: UUID!) { resendUserInvitation(uid: $uid) { code message } }",
        {"uid": new_uid},
        user=admin,
    )
    assert resent["code"] == 9001
    cancelled = gql(
        "mutation X($uid: UUID!) { cancelUserInvitation(uid: $uid) { code message } }",
        {"uid": new_uid},
        user=admin,
    )
    assert cancelled["code"] == 9001 and not User.all_objects.filter(uid=new_uid).exists()

    for mutation in ("deactivateUser", "activateUser", "deleteUser", "restoreUser"):
        envelope = gql(
            f"mutation M($uid: UUID!) {{ {mutation}(uid: $uid) {{ code }} }}",
            {"uid": str(user.uid)},
            user=admin,
        )
        assert envelope["code"] == 9001, mutation

    status = gql(
        "mutation S($uid: UUID!) { setUserAccountStatus(uid: $uid, status: SUSPENDED) { code data { accountStatus } } }",
        {"uid": str(user.uid)},
        user=admin,
    )
    assert status["data"]["accountStatus"] == "SUSPENDED"

    assigned = gql(
        "mutation A($input: AssignRolesInput!) { assignRolesToUser(input: $input) { code data { roles { name } } } }",
        {"input": {"userUid": str(user.uid), "roleUids": [str(role.uid)]}},
        user=admin,
    )
    assert assigned["data"]["roles"] == [{"name": "Clerk"}]

    edited = gql(
        "mutation E($input: UpdateUserInput!) { updateUser(input: $input) { code data { firstName } } }",
        {"input": {"uid": str(user.uid), "firstName": "Edited"}},
        user=admin,
    )
    assert edited["data"]["firstName"] == "Edited"


def test_creating_users_without_the_role_permission_assigns_no_roles(gql, user, grant, make_role, mailbox):
    grant(user, "can_create_user")
    role = make_role("Clerk", [])
    created = gql(
        "mutation C($input: CreateUserInput!) { createUser(input: $input) { code message data { roles { name } } } }",
        {
            "input": {
                "email": "new@example.org",
                "firstName": "New",
                "lastName": "Person",
                "roleUids": [str(role.uid)],
            }
        },
        user=user,
    )
    assert created["data"]["roles"] == [] and "not allowed to assign roles" in created["message"]
