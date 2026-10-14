"""Write-side business logic (services) for the notifications domain.

Owns notification creation/delivery and the delivery-configuration CRUD. The
delivery entry points are:

* :func:`send_now` — create the row and deliver **synchronously**, returning the
  outcome. Used where the caller must tell the person whether the message went
  out (verification codes, password-reset links).
* :func:`queue` — create the row and hand delivery to Celery (non-blocking), for
  events that don't need an inline result.
* :func:`queue_for` — fan one event out to many people across channels.

All converge on :func:`deliver`, which records attempts/status/remarks so the
admin overview and resend machinery have a single source of truth. CRUD services
encrypt secrets on write and enforce a single default per channel.

A notification is a side effect, never part of the domain write that caused it:
nothing here may fail the caller's transaction.
"""

import logging

from django.db import transaction
from django.utils import timezone

from apps.core.enums import to_values
from apps.core.exceptions import ValidationError
from apps.notifications import catalogue, selectors
from apps.notifications.backends import dispatch
from apps.notifications.backends import sms as sms_backend
from apps.notifications.enums import NotificationChannelChoices, NotificationStatusChoices
from apps.notifications.models import (
    EmailConfiguration,
    Notification,
    NotificationPreference,
    NotificationTemplate,
    SmsConfiguration,
)
from apps.notifications.notifications import GENERIC

logger = logging.getLogger(__name__)

CANNOT_RESEND = 9160
MANDATORY_CATEGORY = 9161
UNKNOWN_CATEGORY = 9162
UNKNOWN_PROVIDER = 9163
REDACTED = "[redacted]"


class _SafeDict(dict):
    """``str.format_map`` helper: leaves unknown ``{tokens}`` untouched."""

    def __missing__(self, key):
        return "{" + key + "}"


def _format(text: str, variables: dict | None) -> str:
    try:
        return (text or "").format_map(_SafeDict(variables or {}))
    except (ValueError, IndexError, AttributeError):
        # A stray brace in administrator-edited copy must not stop the send.
        return text or ""


def render_for(category: str | None, channel: str, variables: dict | None) -> tuple[str, str]:
    """Resolve (subject, message) from the active template for the pair, formatted.

    Returns ("", "") when no template is configured — callers pass explicit
    subject/message in that case.
    """
    if not category:
        return "", ""
    template = selectors.active_template(category, channel)
    if template is None:
        return "", ""
    return _format(template.subject, variables), _format(template.body, variables)


# ── creation + delivery ──────────────────────────────────────────────────────
def create_notification(
    *,
    channel: str,
    category: str = "",
    subject: str = "",
    message: str = "",
    to_email: str = "",
    to_phone: str = "",
    full_name: str = "",
    receiver=None,
    html_template: str = "",
    variables: dict | None = None,
    url: str = "",
    title: str = "",
) -> Notification:
    """Persist a PENDING notification row (does not send).

    Copy an administrator saved for this category and channel wins over the
    text the caller passed, so edited wording takes effect without a release.
    """
    channel = to_values(channel)
    category = to_values(category) or ""
    if category:
        t_subject, t_message = render_for(category, channel, variables)
        subject = t_subject or subject
        message = t_message or message

    return Notification.objects.create(
        channel=channel,
        category=category,
        status=NotificationStatusChoices.PENDING.value,
        title=title or "",
        subject=subject or "",
        message=message or "",
        html_template=html_template or "",
        variables=variables or {},
        url=url or "",
        receiver=receiver,
        email_to=to_email or (getattr(receiver, "email", "") if receiver else ""),
        phone_to=to_phone or (getattr(receiver, "phone", "") if receiver else ""),
        full_name=full_name or (receiver.get_full_name() if receiver else ""),
    )


def deliver(notification: Notification) -> tuple[Notification, bool]:
    """Attempt delivery once; record status/attempts/remarks. Returns (row, ok)."""
    try:
        ok, remarks = dispatch(notification)
    except Exception as exc:  # noqa: BLE001 — a backend bug is a failed delivery, not a crash
        logger.exception("[deliver] backend raised for notification %s", notification.uid)
        ok, remarks = False, f"Delivery error: {exc}"
    return _record_attempt(notification, ok, remarks), ok


def _record_attempt(notification: Notification, ok: bool, remarks: str) -> Notification:
    """Book one delivery attempt on the row (shared by the normal path and test sends)."""
    notification.attempts = (notification.attempts or 0) + 1
    notification.is_sent = ok
    notification.status = (
        NotificationStatusChoices.SENT.value if ok else NotificationStatusChoices.FAILED.value
    )
    notification.remarks = remarks or ""
    if ok:
        notification.sent_time = timezone.now()
    notification.save(update_fields=["attempts", "is_sent", "status", "remarks", "sent_time"])
    return notification


def _redact(notification: Notification) -> None:
    """Scrub secret content from a row (keeps the audit metadata).

    Used for one-time secrets (codes, tokenised links): the log entry — channel,
    recipient, status, attempts, sent time — stays, the secret does not. Storing
    only a hash of a code would be pointless if the code sat here in the clear.
    """
    notification.message = REDACTED
    notification.variables = {}
    notification.html_template = ""
    notification.url = ""
    notification.is_redacted = True
    notification.save(update_fields=["message", "variables", "html_template", "url", "is_redacted"])


def _skip(notification: Notification) -> Notification:
    notification.status = NotificationStatusChoices.SKIPPED.value
    notification.remarks = "The recipient turned this kind of notification off."
    notification.save(update_fields=["status", "remarks"])
    return notification


def send_now(
    *,
    channel: str,
    category: str = "",
    subject: str = "",
    message: str = "",
    to_email: str = "",
    to_phone: str = "",
    full_name: str = "",
    receiver=None,
    html_template: str = "",
    variables: dict | None = None,
    url: str = "",
    title: str = "",
    sensitive: bool | None = None,
) -> tuple[Notification, bool]:
    """Create and deliver synchronously; returns (notification, ok).

    ``sensitive`` scrubs the stored row after the send. It defaults to what the
    category declares, so a caller cannot forget it for a verification code.
    """
    category = to_values(category) or ""
    channel = to_values(channel)
    if sensitive is None:
        sensitive = catalogue.is_sensitive(category)
    notification = create_notification(
        channel=channel,
        category=category,
        subject=subject,
        message=message,
        to_email=to_email,
        to_phone=to_phone,
        full_name=full_name,
        receiver=receiver,
        html_template=html_template,
        variables=variables,
        url=url,
        title=title,
    )
    if not selectors.wants(receiver, category, channel):
        return _skip(notification), False
    notification, ok = deliver(notification)
    if sensitive:
        _redact(notification)
    return notification, ok


def queue(**kwargs) -> Notification:
    """Create a row and hand delivery to Celery (non-blocking)."""
    notification = create_notification(**kwargs)
    if not selectors.wants(notification.receiver, notification.category, notification.channel):
        return _skip(notification)
    # Local import avoids importing the Celery task at module load.
    from apps.notifications.tasks import send_notification_task

    transaction_dispatch(send_notification_task, str(notification.uid))
    return notification


def queue_for(
    users,
    category,
    *,
    channels=None,
    title="",
    subject="",
    message="",
    sms_text=None,
    html_template="",
    variables=None,
    url="",
) -> int:
    """Fan one event out to many people across channels — best-effort.

        queue_for(approvers, INVOICE_SUBMITTED,
                  channels=(INAPP, EMAIL, SMS),
                  title=..., message=long_email_body,
                  sms_text=short_sms, variables=vars,
                  url=routes.invoice_detail(invoice.uid))

    Contract:
    * recipients are deduped by id; ``None`` entries are dropped;
    * ``sms_text`` is the SMS body (the gateway sends it raw, so keep it short —
      ``message`` is only a fallback when no ``sms_text`` is given);
    * the SMS channel is silently skipped for a user with no phone — a missing
      number must never poison the e-mail leg;
    * ``url`` is either one deep link for everybody, or a *resolver*
      ``Callable[[user], str]`` (see :mod:`apps.notifications.routes`) when the
      right page depends on who is reading;
    * never raises: delivery is a side effect, not part of the domain write.

    Returns the number of notification rows queued.
    """
    if channels is None:
        channels = (
            NotificationChannelChoices.INAPP.value,
            NotificationChannelChoices.EMAIL.value,
        )
    recipients = {user.id: user for user in users if user is not None}.values()
    queued = 0
    for user in recipients:
        try:
            link = url(user) if callable(url) else url
        except Exception as exc:  # noqa: BLE001 — a bad resolver must not stop the fan-out
            logger.warning("[queue_for] url resolver failed for user %s: %s", user.pk, exc)
            link = ""
        for channel in channels:
            channel = to_values(channel)
            is_sms = channel == NotificationChannelChoices.SMS.value
            if is_sms and not (getattr(user, "phone", "") or "").strip():
                continue
            try:
                row = queue(
                    channel=channel,
                    category=to_values(category),
                    receiver=user,
                    title=title,
                    subject=subject or title,
                    message=sms_text if is_sms and sms_text else message,
                    html_template="" if is_sms else html_template,
                    variables={"full_name": user.get_full_name() or user.email, **(variables or {})},
                    url=link,
                )
                if row.status != NotificationStatusChoices.SKIPPED.value:
                    queued += 1
            except Exception as exc:  # noqa: BLE001 — notifications are best-effort
                logger.warning("[queue_for] %s/%s to user %s failed: %s", category, channel, user.pk, exc)
    return queued


def transaction_dispatch(task, uid: str) -> None:
    """Fire ``task`` after the current DB transaction commits (safe enqueue).

    Enqueuing is a side effect that must never break the domain flow: the
    notification row is already persisted (PENDING) and can be resent, so if the
    broker is unreachable we log and move on rather than letting the failure
    bubble out of the ``on_commit`` hook and fail the originating request.
    """

    def _enqueue() -> None:
        try:
            task.delay(uid)
        except Exception:  # broker down, result backend misconfigured, …
            logger.exception("[transaction_dispatch] could not enqueue notification %s", uid)

    transaction.on_commit(_enqueue)


def resend(*, uid) -> Notification:
    """Re-attempt delivery of a notification (the admin 'resend' feature)."""
    notification = selectors.get_notification(uid)
    if notification.channel == NotificationChannelChoices.INAPP.value:
        raise ValidationError("In-app notifications cannot be resent", code=CANNOT_RESEND)
    if notification.is_redacted or catalogue.is_sensitive(notification.category):
        # The secret is one-time and scrubbed after the first send; issuing a
        # new one is the person's own "send me a new code" action, not a replay.
        raise ValidationError(
            "This message carried a one-time code or link and cannot be replayed. "
            "Ask the person to request a new one.",
            code=CANNOT_RESEND,
        )
    notification.status = NotificationStatusChoices.PENDING.value
    notification.save(update_fields=["status"])
    notification, _ = deliver(notification)
    logger.info("[resend] %s channel=%s ok=%s", notification.uid, notification.channel, notification.is_sent)
    return notification


def mark_inapp_read(*, user, uid=None, read_all: bool = False) -> int:
    """Mark one or all of a user's in-app notifications as read; returns the count."""
    qs = Notification.objects.filter(
        receiver=user, channel=NotificationChannelChoices.INAPP.value, is_read=False
    )
    if not read_all:
        if uid is None:
            raise ValidationError("Provide a uid or set readAll=true", code=9005)
        qs = qs.filter(uid=uid)
    return qs.update(is_read=True, updated_at=timezone.now())


# ── preferences ──────────────────────────────────────────────────────────────
def set_preference(*, user, category: str, channel, enabled: bool) -> NotificationPreference:
    """Turn one category on one channel on or off for ``user``."""
    channel = to_values(channel)
    if channel not in NotificationChannelChoices.values():
        raise ValidationError("Unknown notification channel", code=9002)
    if not catalogue.is_known(category):
        raise ValidationError("Unknown notification category", code=UNKNOWN_CATEGORY)
    if catalogue.is_mandatory(category) and not enabled:
        raise ValidationError(
            "This notice protects your account and cannot be turned off.", code=MANDATORY_CATEGORY
        )
    preference, _ = NotificationPreference.objects.update_or_create(
        user=user, category=category, channel=channel, defaults={"enabled": bool(enabled)}
    )
    return preference


# ════════════════════════════════════════════════════════════════════════════
# EmailConfiguration CRUD
# ════════════════════════════════════════════════════════════════════════════
def _clear_other_defaults(model, keep_pk=None) -> None:
    qs = model.objects.filter(is_default=True)
    if keep_pk is not None:
        qs = qs.exclude(pk=keep_pk)
    for row in qs:  # per-row save, so each change reaches the audit trail
        row.is_default = False
        row.save(update_fields=["is_default"])


def create_email_configuration(
    *,
    name,
    host,
    port=587,
    username="",
    password="",
    use_tls=True,
    use_ssl=False,
    verify_tls=True,
    from_email="",
    from_name="",
    is_default=False,
) -> EmailConfiguration:
    if not name or not host:
        raise ValidationError("name and host are required", code=9005)
    if use_tls and use_ssl:
        raise ValidationError("Choose either TLS or SSL, not both.", code=9002)
    config = EmailConfiguration(
        name=name,
        host=host,
        port=port or 587,
        username=username or "",
        use_tls=bool(use_tls),
        use_ssl=bool(use_ssl),
        verify_tls=True if verify_tls is None else bool(verify_tls),
        from_email=from_email or "",
        from_name=from_name or "",
        is_default=bool(is_default),
    )
    config.set_password(password)
    if config.is_default:
        _clear_other_defaults(EmailConfiguration)
    config.save()
    return config


def update_email_configuration(*, uid, **fields) -> EmailConfiguration:
    config = selectors.get_email_configuration(uid)
    password = fields.pop("password", None)
    for attr in (
        "name",
        "host",
        "port",
        "username",
        "use_tls",
        "use_ssl",
        "verify_tls",
        "from_email",
        "from_name",
    ):
        if fields.get(attr) is not None:
            setattr(config, attr, fields[attr])
    if config.use_tls and config.use_ssl:
        raise ValidationError("Choose either TLS or SSL, not both.", code=9002)
    if password:
        config.set_password(password)
    if fields.get("is_default") is not None:
        config.is_default = bool(fields["is_default"])
        if config.is_default:
            _clear_other_defaults(EmailConfiguration, keep_pk=config.pk)
    config.save()
    return config


def delete_email_configuration(*, uid) -> EmailConfiguration:
    config = selectors.get_email_configuration(uid)
    config.delete()
    return config


def send_test_email(*, uid, to_email, actor=None) -> tuple[Notification, bool]:
    """Push one real message through a mail server so ops can prove it works."""
    from apps.notifications.backends import email as email_backend

    config = selectors.get_email_configuration(uid)
    if not to_email:
        raise ValidationError("Enter the address to send the test message to.", code=9005)
    notification = create_notification(
        channel=NotificationChannelChoices.EMAIL.value,
        category=GENERIC,
        title="Mail server test",
        subject="Mail server test",
        message=f"Test message sent through '{config.name}'. If you can read this, e-mail delivery is working.",
        to_email=to_email,
        full_name=getattr(actor, "get_full_name", lambda: "")() or "",
    )
    ok, remarks = email_backend.send(notification, config=config)
    _record_attempt(notification, ok, remarks)
    return notification, ok


# ════════════════════════════════════════════════════════════════════════════
# SmsConfiguration CRUD
# ════════════════════════════════════════════════════════════════════════════
_SMS_TEXT_FIELDS = ("api_url", "sender_id", "system_id", "service_code", "username")


def _sms_provider(value) -> str:
    provider = (value or sms_backend.PROVIDER_LOG).strip().upper()
    if provider not in sms_backend.PROVIDERS:
        known = ", ".join(sorted(sms_backend.PROVIDERS))
        raise ValidationError(f"Unknown SMS provider '{value}'. Use one of: {known}.", code=UNKNOWN_PROVIDER)
    return provider


def create_sms_configuration(
    *,
    name,
    provider=None,
    api_url="",
    sender_id="",
    system_id="",
    service_code="",
    username="",
    api_key="",
    is_default=False,
) -> SmsConfiguration:
    """Create a gateway row. Only ``name`` is required: every gateway value may
    stay blank and fall back to the ``SMS_*`` settings in the environment."""
    if not name:
        raise ValidationError("A name is required", code=9005)
    config = SmsConfiguration(
        name=name.strip(),
        provider=_sms_provider(provider),
        api_url=(api_url or "").strip(),
        sender_id=(sender_id or "").strip(),
        system_id=(system_id or "").strip(),
        service_code=(service_code or "").strip(),
        username=(username or "").strip(),
        is_default=bool(is_default),
    )
    config.set_api_key((api_key or "").strip())
    if config.is_default:
        _clear_other_defaults(SmsConfiguration)
    config.save()
    return config


def update_sms_configuration(*, uid, **fields) -> SmsConfiguration:
    """PATCH semantics: ``None`` = leave alone; ``""`` = clear (→ environment fallback)."""
    config = selectors.get_sms_configuration(uid)
    api_key = fields.pop("api_key", None)
    if fields.get("provider") is not None:
        config.provider = _sms_provider(fields["provider"])
    for attr in ("name", *_SMS_TEXT_FIELDS):
        if fields.get(attr) is not None:
            setattr(config, attr, str(fields[attr]).strip())
    if not config.name:
        raise ValidationError("A name is required", code=9005)
    if api_key:
        config.set_api_key(api_key.strip())
    if fields.get("is_default") is not None:
        config.is_default = bool(fields["is_default"])
        if config.is_default:
            _clear_other_defaults(SmsConfiguration, keep_pk=config.pk)
    config.save()
    return config


def send_test_sms(*, uid, phone, message="", actor=None) -> tuple[Notification, bool]:
    """Push one real message through a configuration so ops can prove the
    gateway before people depend on it. The attempt is a normal SMS
    notification row, so the outcome — and the gateway's own remark on failure —
    lands in the notification log like any other. ``message`` lets the
    administrator write their own text (restricted-word rejections are a real
    gateway outcome worth reproducing); blank = a default line."""
    config = selectors.get_sms_configuration(uid)
    to_phone = sms_backend.deliverable_phone(phone)
    if not to_phone:
        raise ValidationError("Enter the phone number to send the test message to.", code=9005)
    text = (message or "").strip() or (
        f"Test message via '{config.name}'. If you can read this, SMS delivery is working."
    )
    notification = create_notification(
        channel=NotificationChannelChoices.SMS.value,
        category=GENERIC,
        title="SMS gateway test",
        message=text,
        to_phone=to_phone,
        full_name=getattr(actor, "get_full_name", lambda: "")() or "",
    )
    ok, remarks = sms_backend.send_with_config(notification.phone_to, notification.message, config)
    _record_attempt(notification, ok, remarks)
    logger.info("[send_test_sms] via %s ok=%s :: %s", config.name, ok, remarks)
    return notification, ok


def delete_sms_configuration(*, uid) -> SmsConfiguration:
    config = selectors.get_sms_configuration(uid)
    config.delete()
    return config


# ════════════════════════════════════════════════════════════════════════════
# NotificationTemplate CRUD
# ════════════════════════════════════════════════════════════════════════════
def _channel(value) -> str | None:
    raw = to_values(value)
    if raw is None:
        return None
    if raw not in NotificationChannelChoices.values():
        raise ValidationError(f"Invalid channel: {raw}", code=9002)
    return raw


def _category(value) -> str | None:
    raw = to_values(value)
    if raw is None:
        return None
    if not catalogue.is_known(raw):
        raise ValidationError(f"Unknown notification category: {raw}", code=UNKNOWN_CATEGORY)
    return raw


def create_template(*, name, category, channel, subject="", body="", is_active=True) -> NotificationTemplate:
    cat = _category(category)
    chan = _channel(channel)
    if not name or not cat or not chan:
        raise ValidationError("name, category and channel are required", code=9005)
    if is_active:
        NotificationTemplate.objects.filter(category=cat, channel=chan, is_active=True).update(
            is_active=False
        )
    return NotificationTemplate.objects.create(
        name=name,
        category=cat,
        channel=chan,
        subject=subject or "",
        body=body or "",
        is_active=bool(is_active),
    )


def update_template(*, uid, **fields) -> NotificationTemplate:
    template = selectors.get_template(uid)
    if fields.get("category") is not None:
        template.category = _category(fields["category"]) or template.category
    if fields.get("channel") is not None:
        template.channel = _channel(fields["channel"]) or template.channel
    for attr in ("name", "subject", "body"):
        if fields.get(attr) is not None:
            setattr(template, attr, fields[attr])
    if fields.get("is_active") is not None:
        template.is_active = bool(fields["is_active"])
    if template.is_active:
        NotificationTemplate.objects.filter(
            category=template.category, channel=template.channel, is_active=True
        ).exclude(pk=template.pk).update(is_active=False)
    template.save()
    return template


def delete_template(*, uid) -> NotificationTemplate:
    template = selectors.get_template(uid)
    template.delete()
    return template


def install_default_templates(*, log=None) -> int:
    """Create the templates apps declare, where none exists yet.

    Matched by (category, channel) across **all** rows, deleted ones included:
    copy an administrator edited — or removed on purpose — is never replaced.
    """
    log = log or (lambda message: None)
    created = 0
    for category in catalogue.categories():
        for spec in category.templates:
            exists = NotificationTemplate.all_objects.filter(
                category=category.code, channel=spec.channel
            ).exists()
            if exists or not (spec.body or spec.subject):
                continue
            NotificationTemplate.objects.create(
                name=spec.name or f"{category.name} ({spec.channel.lower()})",
                category=category.code,
                channel=spec.channel,
                subject=spec.subject,
                body=spec.body,
                is_active=True,
            )
            created += 1
    log(f"  • {created} default template(s) installed")
    return created
