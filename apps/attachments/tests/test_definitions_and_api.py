"""The attachment-type catalogue and the GraphQL surface."""

import io

import pytest
from PIL import Image

from apps.attachments import services
from apps.attachments.models import AttachmentDefinition
from apps.core.exceptions import NotFoundError, ValidationError

pytestmark = pytest.mark.django_db

PAGE = {"searchDto": {"pageableParam": {"pageNumber": 1, "itemsPerPage": 20}}}
PDF = b"%PDF-1.4\n%%EOF\n"


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_definitions_constrain_uploads(user):
    definition = services.create_attachment_definition(
        name_en="Passport photo", kind="image", allowed_extensions=["JPG", ".png"], max_size_mb=1
    )
    assert definition.code == "passport-photo" and definition.kind == "Image"
    assert definition.allowed_extensions == ["jpg", "png"]
    with pytest.raises(ValidationError):
        services.create_attachment_definition(name_en="passport photo", kind="Image")  # same name
    with pytest.raises(ValidationError):
        services.create_attachment_definition(name_en="Odd", kind="Video")
    with pytest.raises(ValidationError):
        services.create_attachment_definition(name_en="Odd", kind="Image", allowed_extensions=["pdf"])
    second = services.create_attachment_definition(name_en="Passport photo!", kind="Image")
    assert second.code == "passport-photo-2"

    photo = services.store(content=png_bytes(), file_name="me.png", user=user, definition_uid=definition.uid)
    assert photo.definition == definition and photo.file_type == "Image"
    with pytest.raises(ValidationError) as excinfo:
        services.store(content=png_bytes(), file_name="me.gif", user=user, definition_uid=definition.uid)
    assert excinfo.value.code == 9181
    assert services.max_bytes("Image", definition) == 1024 * 1024

    services.update_attachment_definition(uid=definition.uid, is_active=False, name_en="Old photo")
    with pytest.raises(ValidationError):
        services.store(content=png_bytes(), file_name="me.png", user=user, definition_uid=definition.uid)
    with pytest.raises(ValidationError):  # in use: cannot be deleted
        services.delete_attachment_definition(uid=definition.uid)
    services.delete_attachment_definition(uid=second.uid)
    assert not AttachmentDefinition.objects.filter(uid=second.uid).exists()
    with pytest.raises(NotFoundError):
        services.update_attachment_definition(uid=second.uid, name_en="x")


def test_the_catalogue_api(gql, admin, user):
    saved = gql(
        """
        mutation S($input: AttachmentDefinitionInput!) {
          saveAttachmentDefinition(input: $input) { code data { uid code kind allowedExtensions maxSizeMb name { en } } }
        }""",
        {
            "input": {
                "name": {"en": "Contract", "sw": "Mkataba"},
                "kind": "Document",
                "allowedExtensions": ["pdf"],
                "maxSizeMb": 5,
            }
        },
        user=admin,
    )
    assert (
        saved["code"] == 9001
        and saved["data"]["code"] == "contract"
        and saved["data"]["allowedExtensions"] == ["pdf"]
    )
    uid = saved["data"]["uid"]
    renamed = gql(
        "mutation S($input: AttachmentDefinitionInput!) { saveAttachmentDefinition(input: $input) { code data { name { en sw } } } }",
        {"input": {"uid": uid, "name": {"en": "Signed contract"}}},
        user=admin,
    )
    assert renamed["data"]["name"] == {"en": "Signed contract", "sw": "Mkataba"}
    listed = gql(
        "query { getAttachmentDefinitions(filtering: {isActive: true}) { dataList { code } } }", user=admin
    )
    assert listed["dataList"] == [{"code": "contract"}]
    assert gql("query { getActiveAttachmentDefinitions { dataList { code } } }", user=user)["dataList"] == [
        {"code": "contract"}
    ]
    query = "query { getAllowedFileExtensions { code dataList { kind extension } } }"
    assert gql(query, user=user)["code"] == 9000  # settings screen: permission-gated
    extensions = gql(query, user=admin)["dataList"]
    assert {row["kind"] for row in extensions} == {"Document", "Image"}
    stats = gql(
        "query { getAttachmentStats { data { definitionsTotal filesTotal storageBytes } } }", user=admin
    )["data"]
    assert stats == {"definitionsTotal": 1, "filesTotal": 0, "storageBytes": 0}
    assert (
        gql(
            "mutation D($uid: UUID!) { deleteAttachmentDefinition(uid: $uid) { code } }",
            {"uid": uid},
            user=user,
        )["code"]
        == 9000
    )
    assert (
        gql(
            "mutation D($uid: UUID!) { deleteAttachmentDefinition(uid: $uid) { code } }",
            {"uid": uid},
            user=admin,
        )["code"]
        == 9001
    )


def test_the_file_queries(gql, admin, user, make_user, grant):
    other = make_user()
    mine = services.store(
        content=PDF,
        file_name="mine.pdf",
        file_type="Document",
        user=user,
        entity_type="example.Announcement",
        entity_uid="00000000-0000-7000-8000-000000000001",
    )
    services.store(content=PDF, file_name="theirs.pdf", file_type="Document", user=other)

    page = gql(
        "query M($searchDto: SearchDataDto!) { getMyAttachmentsPageable(searchDto: $searchDto) { content { name downloadUrl uploadedByUid } page { totalElements } } }",
        PAGE,
        user=user,
    )
    assert page["page"]["totalElements"] == 1 and page["content"][0]["name"] == "mine.pdf"
    assert page["content"][0]["downloadUrl"].endswith(f"/files/{mine.uid}/")

    everything = gql(
        "query A($searchDto: SearchDataDto!, $filtering: AttachmentFilterInput) { getAttachmentsPageable(searchDto: $searchDto, filtering: $filtering) { code content { name } page { totalElements } } }",
        {**PAGE, "filtering": {"fileType": "Document", "search": "their"}},
        user=admin,
    )
    assert everything["page"]["totalElements"] == 1 and everything["content"][0]["name"] == "theirs.pdf"
    assert (
        gql(
            "query A($searchDto: SearchDataDto!) { getAttachmentsPageable(searchDto: $searchDto) { code } }",
            PAGE,
            user=user,
        )["code"]
        == 9000
    )

    linked = gql(
        'query E($uid: UUID!) { getEntityAttachments(entityType: "example.Announcement", entityUid: $uid) { dataList { name } } }',
        {"uid": "00000000-0000-7000-8000-000000000001"},
        user=other,
    )
    assert linked["dataList"] == []  # not theirs to see
    grant(other, "can_view_all_attachments")
    linked = gql(
        'query E($uid: UUID!) { getEntityAttachments(entityType: "example.Announcement", entityUid: $uid) { dataList { name } } }',
        {"uid": "00000000-0000-7000-8000-000000000001"},
        user=other,
    )
    assert linked["dataList"] == [{"name": "mine.pdf"}]

    assert (
        gql(
            "mutation D($uid: UUID!) { deleteAttachment(uid: $uid) { code } }",
            {"uid": str(mine.uid)},
            user=other,
        )["code"]
        == 9003
    )
    assert (
        gql(
            "mutation D($uid: UUID!) { deleteAttachment(uid: $uid) { code } }",
            {"uid": str(mine.uid)},
            user=user,
        )["code"]
        == 9001
    )
