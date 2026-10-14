"""The GraphQL surface of roles and permissions."""

import pytest

from apps.rbac.models import Permission, Role

pytestmark = pytest.mark.django_db

PAGE = {"searchDto": {"pageableParam": {"pageNumber": 1, "itemsPerPage": 50, "sortBy": "name"}}}

LIST = """
query Roles($searchDto: SearchDataDto!, $filtering: RoleFilterInput) {
  getAllRolesPageable(searchDto: $searchDto, filtering: $filtering) {
    status code
    content { uid name isSystem permissionCount userCount permissions { code accessLevel } }
    page { totalElements numberOfPages }
  }
}
"""
CREATE = """
mutation Create($input: RoleInput!) {
  createRole(input: $input) { status code message data { uid name permissionCount } }
}
"""
ASSIGN = """
mutation Assign($input: RoleInput!) {
  assignPermissionsToRole(input: $input) { status code data { permissions { code } } }
}
"""


def test_listing_needs_the_permission(gql, user, grant):
    assert gql(LIST, PAGE, user=user)["code"] == 9000
    assert gql(LIST, PAGE)["code"] == 9000
    grant(user, "can_view_roles")
    envelope = gql(LIST, PAGE, user=user)
    assert envelope["status"] is True
    assert envelope["page"]["totalElements"] >= 1


def test_listing_filters_and_searches(gql, admin):
    envelope = gql(LIST, {**PAGE, "filtering": {"isSystem": True}}, user=admin)
    assert [role["name"] for role in envelope["content"]] == ["System Administrator"]
    assert envelope["content"][0]["userCount"] == 1

    envelope = gql(LIST, {**PAGE, "filtering": {"search": "auditor"}}, user=admin)
    assert [role["name"] for role in envelope["content"]] == ["Security Auditor"]


def test_permission_groups_carry_the_copy_for_the_role_editor(gql, admin):
    envelope = gql(
        "query { getAllPermissionGroups { code dataList { name description "
        "permissions { code name description accessLevel } } } }",
        user=admin,
    )
    groups = {group["name"]: group for group in envelope["dataList"]}
    view = next(p for p in groups["User Management"]["permissions"] if p["code"] == "can_view_users")
    assert view["name"] == "View users"
    assert view["accessLevel"] == "VIEW"
    assert "Read-only" in view["description"]


def test_create_update_delete_a_role(gql, admin):
    wanted = [
        str(uid) for uid in Permission.objects.filter(code="can_view_users").values_list("uid", flat=True)
    ]
    created = gql(CREATE, {"input": {"name": "Clerk", "permissionUids": wanted}}, user=admin)
    assert created["code"] == 9001 and created["data"]["permissionCount"] == 1
    uid = created["data"]["uid"]

    assert gql(CREATE, {"input": {"name": "Clerk"}}, user=admin)["code"] == 9006
    assert gql(CREATE, {"input": {"description": "nameless"}}, user=admin)["code"] == 9005

    updated = gql(
        "mutation U($input: RoleInput!) { updateRole(input: $input) { code data { name } } }",
        {"input": {"uid": uid, "name": "Senior clerk"}},
        user=admin,
    )
    assert updated["data"]["name"] == "Senior clerk"

    one = gql("query G($uid: UUID!) { getRole(uid: $uid) { code data { name } } }", {"uid": uid}, user=admin)
    assert one["data"]["name"] == "Senior clerk"

    deleted = gql("mutation D($uid: UUID!) { deleteRole(uid: $uid) { code } }", {"uid": uid}, user=admin)
    assert deleted["code"] == 9001
    assert not Role.objects.filter(uid=uid).exists()


def test_assigning_permissions_replaces_the_set(gql, admin, make_role):
    role = make_role("Reader", ["can_view_users"])
    wanted = [
        str(uid) for uid in Permission.objects.filter(code="can_view_roles").values_list("uid", flat=True)
    ]
    envelope = gql(ASSIGN, {"input": {"uid": str(role.uid), "permissionUids": wanted}}, user=admin)
    assert [p["code"] for p in envelope["data"]["permissions"]] == ["can_view_roles"]
    assert gql(ASSIGN, {"input": {"permissionUids": wanted}}, user=admin)["code"] == 9005


def test_role_management_needs_the_permission(gql, user):
    assert gql(CREATE, {"input": {"name": "Clerk"}}, user=user)["code"] == 9000
