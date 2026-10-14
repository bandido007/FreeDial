"""GraphQL input types for files and the attachment-type catalogue."""

import graphene

from apps.attachments.constants import FileCategoryEnum
from apps.core.graphql.localized import LocalizedInput


class AttachmentDefinitionInput(graphene.InputObjectType):
    uid = graphene.UUID(description="Required when updating")
    name = LocalizedInput(description="Required (English) when creating")
    description = LocalizedInput()
    kind = graphene.String(description='"Document" or "Image" (e.g. passport-size photo)')
    allowed_extensions = graphene.List(graphene.String, description='e.g. ["pdf","jpg"]; empty = any')
    max_size_mb = graphene.Int()
    is_active = graphene.Boolean()


class AttachmentDefinitionFilterInput(graphene.InputObjectType):
    uid = graphene.UUID()
    kind = graphene.String()
    is_active = graphene.Boolean()


class AttachmentFilterInput(graphene.InputObjectType):
    uid = graphene.UUID()
    file_type = graphene.Field(FileCategoryEnum)
    purpose = graphene.String()
    entity_type = graphene.String()
    entity_uid = graphene.UUID()
    uploaded_by_uid = graphene.UUID()
    search = graphene.String(description="Free text on the file name")
