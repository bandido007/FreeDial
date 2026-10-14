"""The GraphQL surface of the audit module."""

import base64

import pytest

from apps.audit.models import RequestLog
from apps.core.context import acting_as

pytestmark = pytest.mark.django_db

PAGE = {"searchDto": {"pageableParam": {"pageNumber": 1, "itemsPerPage": 20}}}


@pytest.fixture
def some_trail(admin, user, make_role):
    with acting_as(admin):
        role = make_role("Readers", [])
    with acting_as(user):
        role.description = "changed"
        role.save()
    return role


def test_the_trail_queries(gql, admin, user, some_trail):
    page = gql(
        "query L($searchDto: SearchDataDto!, $filtering: AuditLogFilterInput) { getAuditLogsPageable(searchDto: $searchDto, filtering: $filtering)"
        " { code content { action entity actorEmail changes objectUid } page { totalElements } } }",
        {**PAGE, "filtering": {"entity": "rbac.Role", "action": "Updated"}},
        user=admin,
    )
    assert page["page"]["totalElements"] == 1 and page["content"][0]["actorEmail"] == user.email
    by_actor = gql(
        "query L($searchDto: SearchDataDto!, $filtering: AuditLogFilterInput) { getAuditLogsPageable(searchDto: $searchDto, filtering: $filtering) { page { totalElements } } }",
        {**PAGE, "filtering": {"actorUid": str(admin.uid), "search": "Readers"}},
        user=admin,
    )
    assert by_actor["page"]["totalElements"] >= 1

    history = gql(
        'query H($uid: UUID!) { getRecordHistory(entity: "rbac.Role", objectUid: $uid) { code dataList { action } } }',
        {"uid": str(some_trail.uid)},
        user=admin,
    )
    assert [row["action"] for row in history["dataList"]] == ["Created", "Updated"]
    assert "rbac.Role" in gql("query { getAuditedEntities }", user=admin)
    assert (
        gql(
            "query L($searchDto: SearchDataDto!) { getAuditLogsPageable(searchDto: $searchDto) { code } }",
            PAGE,
            user=user,
        )["code"]
        == 9000
    )

    mine = gql(
        "query M($searchDto: SearchDataDto!) { getMyActivityPageable(searchDto: $searchDto) { content { action entity } page { totalElements } } }",
        PAGE,
        user=user,
    )
    assert mine["page"]["totalElements"] == 1 and mine["content"][0]["action"] == "Updated"


@pytest.mark.parametrize(
    "fmt,mime",
    [
        ("CSV", "text/csv"),
        ("EXCEL", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("PDF", "application/pdf"),
        ("WORD", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ],
)
def test_the_trail_exports(gql, admin, some_trail, fmt, mime):
    exported = gql(
        "query E($format: TableExportFormatChoices!) { exportAuditLogs(format: $format) { code data { fileName mimeType sizeBytes contentBase64 } } }",
        {"format": fmt},
        user=admin,
    )
    assert exported["code"] == 9001 and exported["data"]["mimeType"] == mime
    content = base64.b64decode(exported["data"]["contentBase64"])
    assert len(content) == exported["data"]["sizeBytes"] > 0
    if fmt == "CSV":
        assert "rbac.Role" in content.decode()


def test_exports_are_throttled(gql, admin, some_trail):
    for _ in range(6):
        assert gql("query { exportAuditLogs(format: CSV) { code } }", user=admin)["code"] == 9001
    assert gql("query { exportAuditLogs(format: CSV) { code } }", user=admin)["code"] == 9018


def test_the_error_queue(gql, admin, user, make_user):
    error = RequestLog.objects.create(
        kind="Query",
        method="POST",
        path="/graphql",
        level="ERROR",
        status_code=200,
        error_type="KeyError",
        operations="getThing",
        user=user,
        user_email=user.email,
    )
    RequestLog.objects.create(kind="Query", method="POST", path="/graphql", level="WARNING", status_code=200)
    listed = gql(
        "query E($searchDto: SearchDataDto!, $filtering: EndpointLogFilterInput) { getEndpointLogsPageable(searchDto: $searchDto, filtering: $filtering) { content { uid errorType userEmail isResolved } page { totalElements } } }",
        {
            **PAGE,
            "filtering": {
                "isResolved": False,
                "errorType": "KeyError",
                "userUid": str(user.uid),
                "operation": "getThing",
                "kind": "Query",
            },
        },
        user=admin,
    )
    assert listed["page"]["totalElements"] == 1 and listed["content"][0]["userEmail"] == user.email
    stats = gql("query { getAuditStats { data { unresolvedErrors errorsToday } } }", user=admin)
    assert stats["data"] == {"unresolvedErrors": 1, "errorsToday": 1}

    resolved = gql(
        'mutation R($uid: UUID!) { resolveEndpointError(uid: $uid, note: "done") { code data { isResolved resolutionNote resolvedByName } } }',
        {"uid": str(error.uid)},
        user=admin,
    )
    assert resolved["data"]["isResolved"] and resolved["data"]["resolvedByName"] == admin.get_full_name()
    reopened = gql(
        "mutation R($uid: UUID!) { reopenEndpointError(uid: $uid) { code data { isResolved } } }",
        {"uid": str(error.uid)},
        user=admin,
    )
    assert reopened["data"]["isResolved"] is False
    similar = gql(
        "mutation R($uid: UUID!) { resolveSimilarEndpointErrors(uid: $uid) { code data { resolved } } }",
        {"uid": str(error.uid)},
        user=admin,
    )
    assert similar["data"]["resolved"] == 1

    recipients = gql(
        "mutation S($uids: [UUID!]!) { setErrorReportRecipients(userUids: $uids) { code dataList { userEmail } } }",
        {"uids": [str(admin.uid)]},
        user=admin,
    )
    assert recipients["dataList"] == [{"userEmail": admin.email}]
    assert gql("query { getErrorReportRecipients { dataList { userEmail } } }", user=admin)["dataList"] == [
        {"userEmail": admin.email}
    ]
    sent = gql("mutation { sendErrorReportNow { code data { errors recipients sent } } }", user=admin)
    assert sent["data"] == {"errors": 1, "recipients": 1, "sent": True}  # the last 24 hours
    assert (
        gql(
            "mutation R($uid: UUID!) { reopenEndpointError(uid: $uid) { code } }",
            {"uid": str(error.uid)},
            user=user,
        )["code"]
        == 9000
    )
