"""Who may open a file.

A file's address is not a permission. Every read — by ID or by path — asks the
**policy of the file's purpose** whether this person may open it.

Built-in purposes:

===================  ======================================================
``general``          the uploader, or anyone with "View every uploaded file"
``profile-photo``    any signed-in person (photos appear next to names)
``public``           anyone, signed in or not
===================  ======================================================

A business app registers its own purpose from ``AppConfig.ready()``, usually to
tie a file to the record it belongs to::

    from apps.attachments import policies

    def may_open_invoice_file(user, attachment) -> bool:
        invoice = Invoice.objects.filter(uid=attachment.entity_uid).first()
        return invoice is not None and invoice.is_visible_to(user)

    policies.register("invoice-document", may_open_invoice_file, bucket="invoices")

An unknown purpose falls back to the ``general`` rule: private by default.
"""

from collections.abc import Callable

from apps.attachments.constants import (
    BUCKET_BY_PURPOSE,
    DEFAULT_BUCKET,
    PURPOSE_GENERAL,
    PURPOSE_PROFILE_PHOTO,
    PURPOSE_PUBLIC,
)
from apps.attachments.permissions import VIEW_ALL_ATTACHMENTS
from apps.core.hooks import resolve_permissions

Policy = Callable[[object, object], bool]

_policies: dict[str, Policy] = {}
_buckets: dict[str, str] = dict(BUCKET_BY_PURPOSE)


def _signed_in(user) -> bool:
    return user is not None and getattr(user, "is_authenticated", False)


def may_view_everything(user, request=None) -> bool:
    return _signed_in(user) and VIEW_ALL_ATTACHMENTS in resolve_permissions(user, request)


def owner_only(user, attachment) -> bool:
    if not _signed_in(user):
        return False
    return attachment.created_by_id == user.pk or may_view_everything(user)


def any_signed_in(user, attachment) -> bool:
    return _signed_in(user)


def anyone(user, attachment) -> bool:
    return True


def register(purpose: str, policy: Policy, *, bucket: str | None = None) -> None:
    """Register the read rule (and optionally the storage folder) of a purpose."""
    _policies[purpose] = policy
    if bucket:
        _buckets[purpose] = bucket


register(PURPOSE_GENERAL, owner_only)
register(PURPOSE_PROFILE_PHOTO, any_signed_in)
register(PURPOSE_PUBLIC, anyone)


def is_known(purpose: str) -> bool:
    return purpose in _policies


def bucket_for(purpose: str) -> str:
    return _buckets.get(purpose, DEFAULT_BUCKET)


def can_read(user, attachment) -> bool:
    """Whether ``user`` may open ``attachment``."""
    if attachment.is_public:
        return True
    policy = _policies.get(attachment.purpose, owner_only)
    try:
        return bool(policy(user, attachment))
    except Exception:  # noqa: BLE001 — a broken rule denies, it never grants
        return False
