"""Custom manager for the email-based User model."""

from django.contrib.auth.models import BaseUserManager

from apps.core.enums import AccountTypeChoices


class UserManager(BaseUserManager):
    """Create users keyed by email, and hide soft-deleted users by default.

    Because ``get_queryset`` filters out ``deleted=True`` rows, a soft-deleted
    account can no longer authenticate (the auth backend resolves users through
    this manager). Use :attr:`User.all_objects` for admin/restore flows.
    """

    use_in_migrations = True

    def get_queryset(self):
        return super().get_queryset().filter(deleted=False)

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("An email address is required")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("is_verified", True)
        # Platform operator.
        extra_fields.setdefault("account_type", AccountTypeChoices.System.value)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        user = self._create_user(email, password, **extra_fields)
        self._grant_system_role(user)
        return user

    @staticmethod
    def _grant_system_role(user) -> None:
        """A superuser holds the system role, so the API sees them as one too.

        Skipped quietly when the catalogue has not been synced yet —
        ``manage.py sync_rbac`` grants it to the bootstrap administrator.
        """
        try:
            from apps.rbac import selectors, services
            from apps.rbac.permissions import SYSTEM_ROLE_NAME

            role = selectors.get_role_by_name(SYSTEM_ROLE_NAME)
            if role is not None:
                services.grant_role(user, role, source="superuser")
        except Exception:  # noqa: BLE001 — e.g. tables not migrated yet
            pass
