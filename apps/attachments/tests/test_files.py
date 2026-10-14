"""Uploading, opening and removing files — and who may do each."""

import base64
import io
import json

import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.attachments import policies, scanner, selectors, services, sniffing
from apps.attachments.models import Attachment
from apps.core.exceptions import NotFoundError, PermissionDeniedError, ValidationError

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"


def png_bytes(size=(64, 64), fmt="PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buffer, format=fmt)
    return buffer.getvalue()


# ── Validation pipeline ──────────────────────────────────────────────────────
def test_images_are_re_encoded_and_documents_kept_verbatim(user):
    photo = services.store(content=png_bytes(), file_name="me.PNG", file_type="Image", user=user)
    assert photo.file_type == "Image" and photo.mime_type == "image/png" and photo.extension == "png"
    assert photo.storage_key.startswith("documents/") and photo.path.startswith("/media/documents/")
    assert photo.created_by == user and photo.checksum and photo.scan_status == "SKIPPED"
    assert default_storage.exists(photo.storage_key)

    paper = services.store(content=PDF, file_name="paper.pdf", file_type="Document", user=user)
    assert paper.mime_type == "application/pdf" and paper.size == len(PDF)
    with default_storage.open(paper.storage_key, "rb") as handle:
        assert handle.read() == PDF


def test_oversized_empty_and_wrong_type_files_are_refused(user, settings):
    settings.ATTACHMENT_MAX_IMAGE_MB = 1
    with pytest.raises(ValidationError) as excinfo:
        services.store(content=b"x" * (1024 * 1024 + 1), file_name="big.png", user=user)
    assert excinfo.value.code == 9180
    with pytest.raises(ValidationError) as excinfo:
        services.store(content=b"", file_name="empty.png", user=user)
    assert excinfo.value.code == 9005
    with pytest.raises(ValidationError) as excinfo:
        services.store(content=PDF, file_name="script.exe", file_type="Document", user=user)
    assert excinfo.value.code == 9181
    with pytest.raises(ValidationError) as excinfo:
        services.store(
            content=b"MZ\x90\x00 not a pdf", file_name="paper.pdf", file_type="Document", user=user
        )
    assert excinfo.value.code == 9182
    with pytest.raises(ValidationError) as excinfo:
        services.store(content=b"garbage", file_name="me.png", file_type="Image", user=user)
    assert excinfo.value.code == 9182


def test_content_sniffing():
    assert sniffing.matches(PDF, "pdf") and not sniffing.matches(b"hello", "pdf")
    assert sniffing.matches(b"a,b\n1,2\n", "csv") and not sniffing.matches(b"\x00\x01\x02", "txt")
    assert sniffing.matches(b"RIFF....WEBPVP8 ", "webp")
    assert not sniffing.matches(b"PK\x03\x04", "docx")  # a zip is not a Word file without its parts
    assert sniffing.matches(b"PK\x03\x04" + b"word/document.xml", "docx")
    assert not sniffing.matches(b"", "pdf") and not sniffing.matches(PDF, "xyz")


def test_base64_uploads(user):
    encoded = "data:image/png;base64," + base64.b64encode(png_bytes()).decode()
    photo = services.save_base64_attachment(base64_string=encoded, file_name="me.png", user=user)
    assert photo.mime_type == "image/png"
    with pytest.raises(ValidationError):
        services.decode_base64("not base64 at all!!")
    with pytest.raises(ValidationError):
        services.decode_base64("")


def test_the_virus_scanner(settings, monkeypatch):
    settings.CLAMAV_ADDRESS = ""
    assert scanner.scan(b"x") == "SKIPPED"
    settings.CLAMAV_ADDRESS = "127.0.0.1:1"  # nothing listens here
    settings.CLAMAV_FAIL_CLOSED = True
    with pytest.raises(ValidationError) as excinfo:
        scanner.scan(b"x")
    assert excinfo.value.code == 9186
    settings.CLAMAV_FAIL_CLOSED = False
    assert scanner.scan(b"x") == "UNSCANNED"

    monkeypatch.setattr(scanner, "_instream", lambda address, data: "stream: Eicar-Test-Signature FOUND")
    settings.CLAMAV_FAIL_CLOSED = True
    with pytest.raises(ValidationError) as excinfo:
        scanner.scan(b"x")
    assert excinfo.value.code == 9183
    monkeypatch.setattr(scanner, "_instream", lambda address, data: "stream: OK")
    assert scanner.scan(b"x") == "CLEAN"


# ── Access policies ──────────────────────────────────────────────────────────
def test_a_file_is_private_to_its_uploader_by_default(user, make_user, grant):
    other = make_user()
    paper = services.store(content=PDF, file_name="paper.pdf", file_type="Document", user=user)
    assert policies.can_read(user, paper)
    assert not policies.can_read(other, paper)
    assert not policies.can_read(None, paper)
    with pytest.raises(NotFoundError):  # a refusal looks like a missing file
        services.readable(paper, other)
    grant(other, "can_view_all_attachments")
    assert policies.can_read(other, paper)


def test_profile_photos_and_public_files(user, make_user, grant):
    other = make_user()
    photo = services.store(content=png_bytes(), file_name="me.png", user=user, purpose="profile-photo")
    assert photo.storage_key.startswith("profiles/")
    assert policies.can_read(other, photo) and not policies.can_read(None, photo)

    with pytest.raises(PermissionDeniedError) as excinfo:
        services.store(content=png_bytes(), file_name="logo.png", user=user, purpose="public")
    assert excinfo.value.code == 9184
    grant(user, "can_publish_public_files")
    logo = services.store(content=png_bytes(), file_name="logo.png", user=user, purpose="public")
    assert logo.is_public and logo.storage_key.startswith("public/") and policies.can_read(None, logo)


def test_a_business_app_registers_its_own_rule(user, make_user):
    other = make_user()
    policies.register("invoice-document", lambda person, attachment: person == other, bucket="invoices")
    try:
        assert policies.is_known("invoice-document") and policies.bucket_for("invoice-document") == "invoices"
        paper = services.store(
            content=PDF, file_name="inv.pdf", file_type="Document", user=user, purpose="invoice-document"
        )
        assert paper.storage_key.startswith("invoices/")
        assert policies.can_read(other, paper) and not policies.can_read(user, paper)

        policies.register("broken", lambda person, attachment: 1 / 0)
        paper.purpose = "broken"
        assert not policies.can_read(other, paper)  # a broken rule denies
    finally:
        policies._policies.pop("invoice-document", None)
        policies._policies.pop("broken", None)
    with pytest.raises(ValidationError):
        services.store(
            content=PDF, file_name="x.pdf", file_type="Document", user=user, purpose="unknown-purpose"
        )


def test_paths_are_resolved_safely(user):
    paper = services.store(content=PDF, file_name="paper.pdf", file_type="Document", user=user)
    assert selectors.get_attachment_by_path(paper.path) == paper
    assert selectors.get_attachment_by_path(paper.storage_key) == paper
    for bad in ("", "  ", "/media/../../etc/passwd", "documents\\x.pdf"):
        with pytest.raises(ValidationError):
            selectors.storage_key_from_path(bad)
    with pytest.raises(NotFoundError):
        selectors.get_attachment_by_path("/media/documents/2020/01/nothing.pdf")


def test_deleting_a_file(user, make_user, grant):
    other = make_user()
    paper = services.store(content=PDF, file_name="paper.pdf", file_type="Document", user=user)
    with pytest.raises(NotFoundError):
        services.delete_attachment(uid=paper.uid, user=other)
    grant(other, "can_delete_attachments")
    services.delete_attachment(uid=paper.uid, user=other)
    assert not Attachment.objects.filter(uid=paper.uid).exists()
    assert Attachment.all_objects.filter(uid=paper.uid, deleted=True).exists()
    assert not default_storage.exists(paper.storage_key)
    with pytest.raises(NotFoundError):
        services.delete_attachment(uid=paper.uid, user=user)


# ── REST endpoints ───────────────────────────────────────────────────────────
def test_uploading_and_downloading_over_http(api, user, make_user):
    client = api(user)
    uploaded = client.post(
        "/files/upload/",
        data={
            "file": SimpleUploadedFile("paper.pdf", PDF, content_type="application/pdf"),
            "file_type": "Document",
        },
    )
    assert uploaded.status_code == 201, uploaded.content
    body = uploaded.json()
    assert (
        body["success"]
        and body["mime"] == "application/pdf"
        and body["url"].endswith(f"/files/{body['uid']}/")
    )

    downloaded = client.post("/files/download/", data={"uid": body["uid"]})
    assert downloaded.status_code == 200 and base64.b64decode(downloaded.json()["base64_string"]) == PDF
    by_path = client.post("/files/download/", data={"file_path": body["file_path"]})
    assert by_path.status_code == 200

    streamed = client.get(f"/files/{body['uid']}/")
    assert streamed.status_code == 200 and streamed["Content-Type"] == "application/pdf"
    assert streamed["X-Content-Type-Options"] == "nosniff" and b"".join(streamed.streaming_content) == PDF
    assert "inline" in streamed["Content-Disposition"]

    stranger = api(make_user())
    assert stranger.post("/files/download/", data={"uid": body["uid"]}).status_code == 404
    assert stranger.post("/files/download/", data={"file_path": body["file_path"]}).status_code == 404
    assert stranger.get(f"/files/{body['uid']}/").status_code == 404
    assert stranger.delete(f"/files/{body['uid']}/").status_code == 404
    from django.test import Client

    assert Client().post("/files/download/", data={"uid": body["uid"]}).status_code == 401

    removed = client.delete(f"/files/{body['uid']}/")
    assert removed.status_code == 200 and client.get(f"/files/{body['uid']}/").status_code == 404


def test_upload_refusals_over_http(api, user, settings):
    client = api(user)
    assert client.post("/files/upload/", data={}).status_code == 400
    refused = client.post(
        "/files/upload/",
        data=json.dumps(
            {"file_type": "Image", "base64_string": base64.b64encode(b"junk").decode(), "file_name": "x.png"}
        ),
        content_type="application/json",
    )
    assert refused.status_code == 400 and refused.json()["code"] == 9182
    settings.ATTACHMENT_MAX_DOCUMENT_MB = 1
    huge = client.post(
        "/files/upload/",
        data={
            "file": SimpleUploadedFile("big.pdf", b"%PDF" + b"x" * (1024 * 1024 + 10)),
            "file_type": "Document",
        },
    )
    assert huge.status_code == 400 and huge.json()["code"] == 9180
    logo = json.dumps(
        {"base64_string": base64.b64encode(png_bytes()).decode(), "file_name": "a.png", "purpose": "public"}
    )
    assert client.post("/files/upload/", data=logo, content_type="application/json").json()["code"] == 9184


def test_public_images_need_no_sign_in(api, user, grant):
    from django.test import Client

    grant(user, "can_publish_public_files")
    logo = services.store(content=png_bytes(), file_name="logo.png", user=user, purpose="public")
    private = services.store(content=png_bytes(), file_name="me.png", user=user)
    anonymous = Client()
    shown = anonymous.post("/files/public-image/", data={"uid": str(logo.uid)})
    assert shown.status_code == 200 and shown.json()["mime"] == "image/png"
    assert anonymous.post("/files/public-image/", data={"file_path": private.path}).status_code == 404
    assert anonymous.post("/files/public-image/", data={}).status_code == 400
    assert anonymous.get(f"/files/{logo.uid}/").status_code == 200  # public: streamed without a token


def test_public_lookups_are_throttled(settings, user, grant):
    from django.test import Client

    settings.RATE_LIMITS = {**settings.RATE_LIMITS, "public-file": (2, 60)}
    grant(user, "can_publish_public_files")
    logo = services.store(content=png_bytes(), file_name="logo.png", user=user, purpose="public")
    anonymous = Client()
    anonymous.post("/files/public-image/", data={"uid": str(logo.uid)})
    anonymous.post("/files/public-image/", data={"uid": str(logo.uid)})
    assert anonymous.post("/files/public-image/", data={"uid": str(logo.uid)}).status_code == 429


def test_a_registered_file_missing_from_storage_is_reported_not_crashed(api, user):
    paper = services.store(content=PDF, file_name="paper.pdf", file_type="Document", user=user)
    default_storage.delete(paper.storage_key)
    with pytest.raises(NotFoundError):
        services.read_as_base64(paper)
    assert api(user).get(f"/files/{paper.uid}/").status_code == 404
    assert services.signed_url(paper) is None  # local disk is served through the API
