"""The foundation's user: everything about a person that any system needs.

:class:`AbstractStarterUser` is abstract on purpose. The concrete model is
``apps.users.models.User``, which a project extends with its own fields::

    class User(AbstractStarterUser):
        department = models.ForeignKey("hr.Department", null=True, on_delete=models.SET_NULL)

Keeping the foundation's fields here and the project's fields there means a
later update from the starter touches this file, not yours.

A person has ONE coarse ``account_type`` (System / Internal / External) and an
``auth_provider`` (how they prove who they are). What they may *do* comes from
their roles (see :mod:`apps.rbac`), never from the account type.
"""

from typing import ClassVar

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.core.context import get_current_user
from apps.core.enums import (
    AccountStatusChoices,
    AccountTypeChoices,
    AuthProviderChoices,
    GenderChoices,
)
from apps.core.hooks import policy
from apps.core.utils import generate_uuid7
from apps.users.managers import UserManager

DEFAULT_PROFILE_REQUIRED_FIELDS = ("first_name", "last_name", "phone")


class AbstractStarterUser(AbstractBaseUser, PermissionsMixin):
    """Authenticated account, identified by a unique email address."""

    # Not a BaseModel, yet account changes are the single most important thing
    # a change trail records.
    AUDIT_INCLUDE = True
    AUDIT_MASK_FIELDS = ("password",)
    AUDIT_IGNORE_FIELDS = ("password_changed_at",)

    uid = models.UUIDField(default=generate_uuid7, unique=True, editable=False, db_index=True)

    email = models.EmailField(unique=True, verbose_name="Email address")
    # A requested new email, held until confirmed by a code (email-change flow).
    pending_email = models.EmailField(blank=True, default="")
    first_name = models.CharField(max_length=150, blank=True)
    middle_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=20, blank=True, db_index=True)
    gender = models.CharField(max_length=10, choices=GenderChoices.choices(), blank=True)
    photo = models.CharField(max_length=600, blank=True, default="")
    date_of_birth = models.DateField(null=True, blank=True)

    # Coarse origin category.
    account_type = models.CharField(
        max_length=20,
        choices=AccountTypeChoices.choices(),
        default=AccountTypeChoices.External.value,
        db_index=True,
    )
    # How this account authenticates.
    auth_provider = models.CharField(
        max_length=10,
        choices=AuthProviderChoices.choices(),
        default=AuthProviderChoices.DB.value,
    )

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False, help_text="May sign in to the Django admin site.")
    is_verified = models.BooleanField(default=False, help_text="Email address proven (code or link).")
    phone_verified = models.BooleanField(default=False, help_text="Phone proven (code sent by SMS).")
    # Operator-controlled access gate (orthogonal to is_verified / deleted).
    account_status = models.CharField(
        max_length=12,
        choices=AccountStatusChoices.choices(),
        default=AccountStatusChoices.ACTIVE.value,
        db_index=True,
    )

    # Maintained by apps.authentication; kept here so they can be filtered on
    # and read without a join.
    mfa_enabled = models.BooleanField(default=False)
    must_change_password = models.BooleanField(
        default=False, help_text="The next sign-in must set a new password."
    )
    password_changed_at = models.DateTimeField(null=True, blank=True)

    # Set when an administrator created the account and an invitation was sent;
    # the account counts as "invited, not yet activated" until a password is set.
    invited_at = models.DateTimeField(null=True, blank=True)

    date_joined = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)

    # Soft delete (a deleted user can no longer authenticate — see UserManager).
    deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        "self", related_name="users_created", null=True, blank=True, on_delete=models.SET_NULL
    )
    updated_by = models.ForeignKey(
        "self", related_name="users_updated", null=True, blank=True, on_delete=models.SET_NULL
    )

    objects = UserManager()
    all_objects = models.Manager()  # includes soft-deleted users

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []  # e-mail + password are the only required fields

    class Meta:
        abstract = True
        ordering = ["-date_joined"]

    def __str__(self):
        return self.get_full_name() or self.email

    def save(self, *args, **kwargs):
        actor = get_current_user()
        if actor is not None and getattr(actor, "is_authenticated", False) and actor.pk != self.pk:
            if self._state.adding and self.created_by_id is None:
                self.created_by = actor
            self.updated_by = actor

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = list(set(update_fields) | {"updated_at", "updated_by"})
        super().save(*args, **kwargs)

    # ── Names ────────────────────────────────────────────────────────────────
    def get_full_name(self) -> str:
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(p for p in parts if p).strip()

    def get_short_name(self) -> str:
        return self.first_name or self.email

    # ── Profile completeness ─────────────────────────────────────────────────
    def missing_profile_fields(self, required=None) -> list[str]:
        """The required profile fields that are still blank (empty = complete).

        The list comes from ``PROFILE_REQUIRED_FIELDS`` unless ``required`` is
        given — pass it to apply a stricter or looser rule to one action.
        """
        if required is None:
            required = policy("PROFILE_REQUIRED_FIELDS", DEFAULT_PROFILE_REQUIRED_FIELDS)
        return [name for name in required if not getattr(self, name, None)]

    @property
    def is_profile_complete(self) -> bool:
        return not self.missing_profile_fields()

    # ── Account state ────────────────────────────────────────────────────────
    @property
    def can_authenticate(self) -> bool:
        """Sign-in is allowed only for active, non-deleted, non-held accounts."""
        return (
            self.is_active and not self.deleted and self.account_status == AccountStatusChoices.ACTIVE.value
        )

    @property
    def is_staff_account(self) -> bool:
        return self.account_type in (AccountTypeChoices.System.value, AccountTypeChoices.Internal.value)

    @property
    def invitation_pending(self) -> bool:
        """Invited by an administrator and has not set a password yet."""
        return self.invited_at is not None and not self.has_usable_password()

    def set_account_status(self, status: str):
        self.account_status = status
        self.save(update_fields=["account_status"])

    def delete(self, using=None, keep_parents=False):
        """Soft delete: deactivate and flag, but keep the row for audit."""
        self.deleted = True
        self.is_active = False
        self.deleted_at = timezone.now()
        self.save(using=using)

    def hard_delete(self, using=None, keep_parents=False):
        super().delete(using=using, keep_parents=keep_parents)

    def deactivate(self):
        self.is_active = False
        self.deactivated_at = timezone.now()
        self.save()

    def activate(self):
        self.is_active = True
        self.deactivated_at = None
        self.save()
