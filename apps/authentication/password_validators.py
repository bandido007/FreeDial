"""Password validators wired into ``AUTH_PASSWORD_VALIDATORS``.

They run everywhere Django's ``validate_password`` is called — registration,
change, reset, invitation.

* :class:`PwnedPasswordValidator` — rejects passwords found in known breaches.
* :class:`PasswordHistoryValidator` — rejects a password the account used
  recently, and records each password that is accepted.
"""

import hashlib
import logging

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.hooks import policy

logger = logging.getLogger(__name__)

_API = "https://api.pwnedpasswords.com/range/"


class PwnedPasswordValidator:
    """Block passwords known to be breached (Have I Been Pwned).

    Uses the k-anonymity range API — only the **first 5 characters** of the
    SHA-1 hash ever leave the server, never the password or its full hash.
    Fail-open: if the API is unreachable (or the check is disabled), the
    password is allowed, so an outage can never lock people out.
    """

    def __init__(self, threshold: int = 1):
        self.threshold = threshold

    def validate(self, password, user=None):
        if not getattr(settings, "PWNED_PASSWORD_CHECK", False):
            return  # disabled (e.g. tests / offline development)
        count = self._breach_count(password)
        if count >= self.threshold:
            raise ValidationError(
                "This password has appeared in a known data breach. Please choose a different one.",
                code="password_pwned",
            )

    def _breach_count(self, password: str) -> int:
        try:
            import requests

            digest = hashlib.sha1(password.encode("utf-8"), usedforsecurity=False).hexdigest().upper()
            prefix, suffix = digest[:5], digest[5:]
            response = requests.get(_API + prefix, timeout=2.5, headers={"Add-Padding": "true"})
            if response.status_code != 200:
                return 0
            for line in response.text.splitlines():
                hash_suffix, _, count = line.partition(":")
                if hash_suffix.strip().upper() == suffix:
                    return int(count or 0)
            return 0
        except Exception:  # noqa: BLE001 — network error → fail open
            logger.warning("[PwnedPasswordValidator] check skipped (unreachable)", exc_info=True)
            return 0

    def get_help_text(self):
        return "Your password must not appear in a known data breach."


class PasswordHistoryValidator:
    """Refuse the reuse of recent passwords.

    ``validate`` compares the candidate with the account's last N password
    hashes (``PASSWORD_HISTORY_COUNT``; 0 switches the rule off).
    ``password_changed`` is Django's own hook — it is called whenever a user is
    saved with a new password, whatever code path set it — and records the new
    hash, trimming the history to N rows.
    """

    @staticmethod
    def depth() -> int:
        try:
            return max(0, int(policy("PASSWORD_HISTORY_COUNT", 0) or 0))
        except (TypeError, ValueError):
            return 0

    def validate(self, password, user=None):
        depth = self.depth()
        if depth <= 0 or user is None or not getattr(user, "pk", None):
            return
        from apps.authentication.models import PasswordHistory

        recent = PasswordHistory.objects.filter(user=user).order_by("-created_at")[:depth]
        for entry in recent:
            if check_password(password, entry.password_hash):
                raise ValidationError(
                    "You have used this password before. Choose one you have not used recently.",
                    code="password_reused",
                )

    def password_changed(self, password, user=None):
        if user is None or not getattr(user, "pk", None) or not user.password:
            return
        from apps.authentication.models import PasswordHistory

        try:
            depth = self.depth()
            if depth > 0:
                PasswordHistory.objects.create(user=user, password_hash=user.password)
                keep = list(
                    PasswordHistory.objects.filter(user=user)
                    .order_by("-created_at")
                    .values_list("pk", flat=True)[:depth]
                )
                PasswordHistory.objects.filter(user=user).exclude(pk__in=keep).delete()
            # Stamp the change without re-entering save() (and this hook).
            type(user).all_objects.filter(pk=user.pk).update(password_changed_at=timezone.now())
        except Exception:  # noqa: BLE001 — history must never block a password change
            logger.exception("[PasswordHistoryValidator] could not record the change")

    def get_help_text(self):
        depth = self.depth()
        if depth <= 0:
            return ""
        return f"Your password must be different from your last {depth} passwords."
