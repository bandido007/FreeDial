"""Role-based access control.

:class:`PermissionGroup` → :class:`Permission`, bundled into a :class:`Role`,
granted to a user through :class:`UserRole`. A person's access is the union of
the permissions on every role they currently hold.

Permissions are **declared in code** (each app's ``permissions.py``) and written
here by ``manage.py sync_rbac``; roles and grants are managed from the screens.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.catalogue import LEVEL_CHANGE, LEVELS
from apps.core.models import BaseModel


class PermissionGroup(BaseModel):
    """A logical grouping of permissions (e.g. "User Management")."""

    name = models.CharField(max_length=255, unique=True)
    description = models.CharField(max_length=600, blank=True)
    app_label = models.CharField(max_length=100, blank=True, help_text="The app that declares this group.")

    class Meta:
        db_table = "permission_groups"
        ordering = ["name"]
        verbose_name_plural = "Permission groups"

    def __str__(self):
        return self.name


class Permission(BaseModel):
    """A single, code-addressable capability (e.g. ``can_create_role``).

    ``name`` is the short verb phrase shown on chips ("Create roles");
    ``description`` is the plain-language sentence a person assigning it to a
    role reads. Both come from the catalogue and are never typed by hand.
    """

    code = models.CharField(max_length=255, unique=True, db_index=True)
    name = models.CharField(max_length=255)
    description = models.CharField(max_length=600, blank=True, default="")
    level = models.CharField(
        max_length=10,
        choices=[(level, level) for level in LEVELS],
        default=LEVEL_CHANGE,
        help_text="VIEW = look only · CHANGE = create/edit/act · DECIDE = high impact.",
    )
    group = models.ForeignKey(
        PermissionGroup, related_name="permissions", null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        db_table = "permissions"
        ordering = ["code"]
        verbose_name_plural = "Permissions"

    def __str__(self):
        return self.code


class Role(BaseModel):
    """A named bundle of permissions that can be assigned to users."""

    name = models.CharField(max_length=255)
    description = models.CharField(max_length=600, blank=True)
    is_system = models.BooleanField(default=False, help_text="System roles cannot be edited or deleted")

    class Meta:
        db_table = "roles"
        ordering = ["name"]
        verbose_name_plural = "Roles"
        constraints = [
            models.UniqueConstraint(
                fields=["name"], condition=models.Q(deleted=False), name="unique_active_role_name"
            )
        ]

    def __str__(self):
        return self.name


class RolePermission(BaseModel):
    """Grants a single permission to a role (the role ↔ permission link)."""

    role = models.ForeignKey(Role, related_name="role_permissions", on_delete=models.CASCADE)
    permission = models.ForeignKey(Permission, related_name="permission_roles", on_delete=models.CASCADE)

    class Meta:
        db_table = "role_permissions"
        verbose_name_plural = "Role permissions"
        constraints = [
            models.UniqueConstraint(
                fields=["role", "permission"],
                condition=models.Q(deleted=False),
                name="unique_permission_per_role",
            )
        ]

    def __str__(self):
        return f"{self.role} → {self.permission}"


class UserRole(BaseModel):
    """Assigns a role to a user (the user ↔ role link).

    ``source`` says where a grant came from, so code that grants roles on its
    own (a directory sync, an approval workflow) can later find and revoke
    exactly the grants it made. Grants made from the screens use ``manual``.

    ``expires_at`` makes a grant temporary: once it passes, the role stops
    counting toward the user's permissions, and the nightly sweep retires the
    row.
    """

    SOURCE_MANUAL = "manual"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="user_roles", on_delete=models.CASCADE)
    role = models.ForeignKey(Role, related_name="role_users", on_delete=models.CASCADE)
    source = models.CharField(max_length=60, blank=True, default=SOURCE_MANUAL, db_index=True)
    source_ref = models.CharField(
        max_length=64, blank=True, default="", help_text="Identifier of the record that caused this grant."
    )
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = "user_roles"
        verbose_name_plural = "User roles"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "role"],
                condition=models.Q(deleted=False),
                name="unique_role_per_user",
            )
        ]

    def __str__(self):
        return f"{self.user} → {self.role}"

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= timezone.now()
