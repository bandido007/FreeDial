"""The GraphQL surface of authentication."""

import pytest

from apps.authentication import totp

pytestmark = pytest.mark.django_db

PAGE = {"searchDto": {"pageableParam": {"pageNumber": 1, "itemsPerPage": 20}}}


def test_me_query(gql, user, grant):
    grant(user, "can_view_users")
    me = gql(
        "query { meQuery { code data { attributes { email hasPassword } accountType authorities "
        "totalAuthorities roles { name } mfaRequired passwordExpiresAt } } }",
        user=user,
    )
    assert me["data"]["attributes"] == {"email": user.email, "hasPassword": True}
    assert me["data"]["authorities"] == ["can_view_users"] and me["data"]["totalAuthorities"] == 1
    assert me["data"]["roles"] == [{"name": "test-role-1"}]
    assert gql("query { meQuery { code } }")["code"] == 9000


def test_password_mutations(gql, user, mailbox):
    changed = gql(
        "mutation C($input: ChangePasswordInput!) { changeMyPassword(input: $input) { code message } }",
        {
            "input": {
                "oldPassword": "Str0ng!Passw0rd",
                "newPassword": "Fr3sh!Passw0rd",
                "confirmPassword": "nope",
            }
        },
        user=user,
    )
    assert changed["code"] == 9012
    changed = gql(
        "mutation C($input: ChangePasswordInput!) { changeMyPassword(input: $input) { code message } }",
        {
            "input": {
                "oldPassword": "Str0ng!Passw0rd",
                "newPassword": "Fr3sh!Passw0rd",
                "confirmPassword": "Fr3sh!Passw0rd",
            }
        },
        user=user,
    )
    assert changed["code"] == 9001

    asked = gql('mutation { requestPasswordReset(email: "person@example.org") { code message } }')
    assert asked["code"] == 9001 and "If an account exists" in asked["message"]
    import re

    raw = re.search(r"/set-password/(\S+)", mailbox[-1].body).group(1).rstrip(".")
    done = gql(
        "mutation S($input: SetPasswordInput!) { setPassword(input: $input) { code message } }",
        {"input": {"token": raw, "newPassword": "Ag4in!Passw0rd", "confirmPassword": "Ag4in!Passw0rd"}},
    )
    assert done["code"] == 9001
    mismatch = gql(
        "mutation S($input: SetPasswordInput!) { setPassword(input: $input) { code } }",
        {"input": {"token": raw, "newPassword": "a", "confirmPassword": "b"}},
    )
    assert mismatch["code"] == 9012


def test_two_factor_and_session_mutations(gql, user, sign_in):
    setup = gql("mutation { beginMfaEnrollment { code data { secret otpauthUri } } }", user=user)
    secret = setup["data"]["secret"]
    confirmed = gql(
        "mutation C($otp: String!) { confirmMfaEnrollment(otp: $otp) { code data { recoveryCodes } } }",
        {"otp": totp._hotp(secret, totp.current_step())},
        user=user,
    )
    assert len(confirmed["data"]["recoveryCodes"]) == 10
    regenerated = gql(
        "mutation R($otp: String!) { regenerateMfaRecoveryCodes(otp: $otp) { code data { recoveryCodes } } }",
        {"otp": totp._hotp(secret, totp.current_step() + 1)},
        user=user,
    )
    assert regenerated["code"] == 9001
    me = gql("query { meQuery { data { attributes { mfaEnabled mfaRecoveryCodesRemaining } } } }", user=user)
    assert me["data"]["attributes"] == {"mfaEnabled": True, "mfaRecoveryCodesRemaining": 10}
    off = gql(
        "mutation D($otp: String!) { disableMfa(otp: $otp) { code data { mfaEnabled } } }",
        {"otp": regenerated["data"]["recoveryCodes"][0]},
        user=user,
    )
    assert off["data"]["mfaEnabled"] is False

    sign_in(user.email)
    sessions = gql("query { getMySessions { dataList { id label isCurrent provider } } }", user=user)
    assert len(sessions["dataList"]) == 1
    revoked = gql(
        "mutation R($id: String!) { revokeMySession(sessionId: $id) { code data { revoked signedOut } } }",
        {"id": sessions["dataList"][0]["id"]},
        user=user,
    )
    assert revoked["data"] == {"revoked": 1, "signedOut": False}
    everywhere = gql(
        "mutation { revokeAllMySessions(includeCurrent: true) { code data { revoked } } }", user=user
    )
    assert everywhere["code"] == 9001


def test_oversight_queries_and_mutations(gql, user, admin, sign_in, mailbox):
    sign_in(user.email)
    sign_in(user.email, "wrong")
    history = gql(
        "query H($searchDto: SearchDataDto!, $filtering: LoginHistoryFilterInput) { getLoginHistoryPageable("
        "searchDto: $searchDto, filtering: $filtering) { code content { userEmail label isNewDevice } page { totalElements } } }",
        {**PAGE, "filtering": {"userUid": str(user.uid)}},
        user=admin,
    )
    assert history["page"]["totalElements"] == 1 and history["content"][0]["userEmail"] == user.email
    events = gql(
        "query E($searchDto: SearchDataDto!, $filtering: AuthenticationEventFilterInput) { getAuthenticationEventsPageable("
        "searchDto: $searchDto, filtering: $filtering) { code content { eventType status email } page { totalElements } } }",
        {**PAGE, "filtering": {"status": "failure", "search": "person"}},
        user=admin,
    )
    assert events["page"]["totalElements"] == 1 and events["content"][0]["eventType"] == "FailedLogin"
    assert (
        gql(
            "query H($searchDto: SearchDataDto!) { getLoginHistoryPageable(searchDto: $searchDto) { code } }",
            PAGE,
            user=user,
        )["code"]
        == 9000
    )

    mine = gql(
        "query M($searchDto: SearchDataDto!) { getMyLoginHistoryPageable(searchDto: $searchDto) { page { totalElements } } }",
        PAGE,
        user=user,
    )
    assert mine["page"]["totalElements"] == 1
    mine = gql(
        "query M($searchDto: SearchDataDto!) { getMySecurityEventsPageable(searchDto: $searchDto) { page { totalElements } } }",
        PAGE,
        user=user,
    )
    assert mine["page"]["totalElements"] >= 1

    for mutation, expected in (
        ("sendUserPasswordReset", 9001),
        ("requireUserPasswordChange", 9001),
        ("revokeUserSessions", 9001),
        ("resetUserMfa", 9001),
    ):
        envelope = gql(
            f"mutation M($uid: UUID!) {{ {mutation}(uid: $uid) {{ code }} }}",
            {"uid": str(user.uid)},
            user=admin,
        )
        assert envelope["code"] == expected, mutation
    assert (
        gql(
            "mutation M($uid: UUID!) { resetUserMfa(uid: $uid) { code } }", {"uid": str(user.uid)}, user=user
        )["code"]
        == 9000
    )


def test_directory_mutations(gql, admin, make_role, settings):
    settings.LDAP_SIMULATE_DIRECTORY = True
    role = make_role("Staff", [])
    created = gql(
        """
        mutation C($input: LdapConfigInput!) {
          createLdapConfig(input: $input) { code data { uid emailDomain hasBindPassword defaultRole { name } } }
        }""",
        {
            "input": {
                "ldapUrl": "ldap://dir.example.org",
                "bindDn": "cn=svc",
                "bindPassword": "s3cret",
                "searchBase": "dc=example,dc=org",
                "emailDomain": "example.org",
                "defaultRoleUid": str(role.uid),
            }
        },
        user=admin,
    )
    assert created["data"]["hasBindPassword"] and created["data"]["defaultRole"] == {"name": "Staff"}
    uid = created["data"]["uid"]
    listed = gql(
        "query { getLdapConfigs(filtering: {isActive: true}) { dataList { emailDomain } } }", user=admin
    )
    assert listed["dataList"] == [{"emailDomain": "example.org"}]
    pulled = gql(
        "mutation P($uid: UUID!) { pullLdapUsers(configUid: $uid) { code message dataList { email } } }",
        {"uid": uid},
        user=admin,
    )
    assert len(pulled["dataList"]) == 6
    updated = gql(
        "mutation U($input: LdapConfigInput!) { updateLdapConfig(input: $input) { code data { isActive } } }",
        {"input": {"uid": uid, "isActive": False}},
        user=admin,
    )
    assert updated["data"]["isActive"] is False
    assert (
        gql("mutation D($uid: UUID!) { deleteLdapConfig(uid: $uid) { code } }", {"uid": uid}, user=admin)[
            "code"
        ]
        == 9015
    )
