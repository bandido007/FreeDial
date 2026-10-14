"""GraphQL mutations for files and the attachment-type catalogue.

Uploading happens over REST (``POST /files/upload/``): a file is not a good
fit for a JSON document.
"""

import logging

import graphene

from apps.attachments import services
from apps.attachments.inputs import AttachmentDefinitionInput
from apps.attachments.permissions import MANAGE_ATTACHMENT_SETTINGS
from apps.attachments.types import AttachmentDefinitionType, AttachmentType
from apps.core.decorators import authenticated, has_permission, log_errors_and_warnings
from apps.core.enums import AccountTypeChoices
from apps.core.graphql.context import current_user
from apps.core.graphql.response import Response
from apps.core.utils import Utils

logger = logging.getLogger(__name__)

ADMIN_TYPES = [AccountTypeChoices.System.value]


def _localized(value, attr):
    return getattr(value, attr, None) if value is not None else None


class SaveAttachmentDefinition(graphene.Mutation):
    """Create (no uid) or update (uid) a configurable attachment type."""

    class Arguments:
        input = AttachmentDefinitionInput(required=True)

    Output = Response.get_response_type(AttachmentDefinitionType)

    @classmethod
    @has_permission(permissions=[MANAGE_ATTACHMENT_SETTINGS], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, input):
        name = getattr(input, "name", None)
        description = getattr(input, "description", None)
        if input.uid:
            definition = services.update_attachment_definition(
                uid=input.uid,
                name_en=_localized(name, "en"),
                name_sw=_localized(name, "sw"),
                description_en=_localized(description, "en"),
                description_sw=_localized(description, "sw"),
                kind=input.kind,
                allowed_extensions=input.allowed_extensions,
                max_size_mb=input.max_size_mb,
                is_active=input.is_active,
            )
            return Response.build_response(
                code=9001, info=info, data=definition, custom_message="Attachment type updated."
            )

        if not Utils.empty_and_null_checker(name, ["en"]):
            return Response.build_response(code=9005, info=info, custom_message="A name is required.")
        definition = services.create_attachment_definition(
            name_en=_localized(name, "en"),
            name_sw=_localized(name, "sw") or "",
            description_en=_localized(description, "en") or "",
            description_sw=_localized(description, "sw") or "",
            kind=input.kind or "Document",
            allowed_extensions=input.allowed_extensions,
            max_size_mb=input.max_size_mb,
            is_active=input.is_active if input.is_active is not None else True,
        )
        return Response.build_response(
            code=9001, info=info, data=definition, custom_message="Attachment type created."
        )


class DeleteAttachmentDefinition(graphene.Mutation):
    class Arguments:
        uid = graphene.UUID(required=True)

    Output = Response.get_response_type(AttachmentDefinitionType)

    @classmethod
    @has_permission(permissions=[MANAGE_ATTACHMENT_SETTINGS], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, uid):
        definition = services.delete_attachment_definition(uid=uid)
        return Response.build_response(
            code=9001, info=info, data=definition, custom_message="Attachment type deleted."
        )


class DeleteAttachment(graphene.Mutation):
    """Remove a file I uploaded (or any file, with "Delete uploaded files")."""

    class Arguments:
        uid = graphene.UUID(required=True)

    Output = Response.get_response_type(AttachmentType)

    @classmethod
    @authenticated()
    @log_errors_and_warnings
    def mutate(cls, root, info, uid):
        attachment = services.delete_attachment(uid=uid, user=current_user(info), request=info.context)
        return Response.build_response(
            code=9001, info=info, data=attachment, custom_message="The file was removed."
        )


class Mutation(graphene.ObjectType):
    save_attachment_definition = SaveAttachmentDefinition.Field(
        description="permissions=['can_manage_attachment_settings']"
    )
    delete_attachment_definition = DeleteAttachmentDefinition.Field(
        description="permissions=['can_manage_attachment_settings']"
    )
    delete_attachment = DeleteAttachment.Field(description="Authenticated (own files).")
