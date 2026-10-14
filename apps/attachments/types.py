"""GraphQL output types for files and the attachment-type catalogue."""

import graphene
from graphene_django import DjangoObjectType

from apps.attachments.constants import FileCategoryEnum
from apps.attachments.models import Attachment, AttachmentDefinition
from apps.core.graphql.localized import LocalizedType


def _localized(base: str):
    def resolver(self, info):
        return {"en": getattr(self, f"{base}_en", ""), "sw": getattr(self, f"{base}_sw", "")}

    return resolver


class AttachmentDefinitionType(DjangoObjectType):
    name = graphene.Field(LocalizedType)
    description = graphene.Field(LocalizedType)
    kind = graphene.String()
    allowed_extensions = graphene.List(graphene.String)

    class Meta:
        model = AttachmentDefinition
        fields = ("uid", "code", "kind", "max_size_mb", "is_active", "created_at", "updated_at")

    resolve_name = _localized("name")
    resolve_description = _localized("description")

    def resolve_allowed_extensions(self, info):
        return self.allowed_extensions or []


class AttachmentType(DjangoObjectType):
    """A stored file. The content itself is fetched from the file endpoints."""

    file_type = FileCategoryEnum()
    uploaded_by_uid = graphene.UUID()
    uploaded_by_name = graphene.String()
    definition_uid = graphene.UUID()
    download_url = graphene.String(description="GET this, with the bearer token, to download the file.")

    class Meta:
        model = Attachment
        fields = (
            "uid",
            "name",
            "path",
            "file_type",
            "mime_type",
            "size",
            "purpose",
            "is_public",
            "entity_type",
            "entity_uid",
            "scan_status",
            "created_at",
        )

    def resolve_uploaded_by_uid(self, info):
        return self.created_by.uid if self.created_by_id else None

    def resolve_uploaded_by_name(self, info):
        return self.created_by.get_full_name() if self.created_by_id else ""

    def resolve_definition_uid(self, info):
        return self.definition.uid if self.definition_id else None

    def resolve_download_url(self, info):
        return f"/files/{self.uid}/"


class AllowedFileExtensionType(graphene.ObjectType):
    """One pickable file type for a definition of ``kind``."""

    kind = graphene.String()
    extension = graphene.String()
    label = graphene.String()
    mime = graphene.String()


class AttachmentStatsType(graphene.ObjectType):
    """Counter block for the Attachment types dashboard."""

    definitions_total = graphene.Int()
    definitions_active = graphene.Int()
    definitions_documents = graphene.Int()
    definitions_images = graphene.Int()
    definitions_in_use = graphene.Int(description="Types that files have been uploaded for.")
    files_total = graphene.Int()
    files_images = graphene.Int()
    files_documents = graphene.Int()
    storage_bytes = graphene.Float(description="Bytes stored across every attachment.")
