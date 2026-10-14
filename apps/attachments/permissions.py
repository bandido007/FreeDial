"""Permissions that guard files and the attachment-type catalogue."""

from apps.core.catalogue import Group, Perm, RoleSpec

VIEW_ATTACHMENT_SETTINGS = "can_view_attachment_settings"
MANAGE_ATTACHMENT_SETTINGS = "can_manage_attachment_settings"
VIEW_ALL_ATTACHMENTS = "can_view_all_attachments"
DELETE_ATTACHMENTS = "can_delete_attachments"
PUBLISH_PUBLIC_FILES = "can_publish_public_files"

GROUPS = [
    Group(
        name="Attachments",
        description="Uploaded files, and the kinds of attachment the system asks for.",
        permissions=[
            Perm(
                VIEW_ATTACHMENT_SETTINGS,
                "View attachment types",
                "See the catalogue of attachment types and the storage figures. Read-only.",
            ),
            Perm(
                MANAGE_ATTACHMENT_SETTINGS,
                "Manage attachment types",
                "Add, change or remove an attachment type: its name, the file types it accepts "
                "and its size limit.",
            ),
            Perm(
                VIEW_ALL_ATTACHMENTS,
                "View every uploaded file",
                "Open any private file, whoever uploaded it. High impact: it overrides the normal "
                "rule that people see only their own files. Read-only.",
                level="DECIDE",
            ),
            Perm(
                DELETE_ATTACHMENTS,
                "Delete uploaded files",
                "Remove a file that someone else uploaded. High impact: the file can no longer be opened.",
            ),
            Perm(
                PUBLISH_PUBLIC_FILES,
                "Publish public files",
                "Upload a file that anyone can open without signing in, such as a logo or a banner.",
                level="DECIDE",
            ),
        ],
    ),
]

ROLES = [
    RoleSpec(name="Security Auditor", permissions=[VIEW_ATTACHMENT_SETTINGS]),
]
