"""E-mail delivery backend.

Sends one :class:`~apps.notifications.models.Notification` over SMTP. Connection
settings come from the active :class:`~apps.notifications.models.EmailConfiguration`
row (so an administrator can rotate credentials without a redeploy), falling
back to the project's ``EMAIL_*`` Django settings when no row is configured.

Uses Django's e-mail framework — ``get_connection`` + ``EmailMultiAlternatives``
with an HTML alternative rendered from a template.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.template import TemplateDoesNotExist
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import escape, strip_tags

logger = logging.getLogger(__name__)

DEFAULT_TEMPLATE = "emails/generic.html"
VERIFIED_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
UNVERIFIED_BACKEND = "apps.notifications.backends.smtp_unverified.EmailBackend"
SMTP_TIMEOUT_SECONDS = 20


def _active_config():
    from apps.notifications.models import EmailConfiguration

    return EmailConfiguration.objects.filter(is_default=True).first() or EmailConfiguration.objects.first()


def _connection_and_sender(config=None):
    """Return (connection, from_address) from ``config``, else the active
    configuration row, else the Django settings."""
    config = config or _active_config()
    if config is not None and config.host:
        connection = get_connection(
            backend=VERIFIED_BACKEND if config.verify_tls else UNVERIFIED_BACKEND,
            host=config.host,
            port=config.port,
            username=config.username,
            password=config.get_password(),
            use_tls=config.use_tls,
            use_ssl=config.use_ssl,
            timeout=SMTP_TIMEOUT_SECONDS,
            fail_silently=False,
        )
        # An operator-configured From name wins; otherwise the brand's, so a
        # bare address never reaches an inbox.
        address = config.from_email or config.username
        name = config.from_name or brand_context()["brand"].get("from_name", "")
        sender = (f"{name} <{address}>" if name and address else address) or settings.DEFAULT_FROM_EMAIL
        return connection, sender

    # Fall back to whatever EMAIL_* the environment configured.
    return get_connection(fail_silently=False), settings.DEFAULT_FROM_EMAIL


def brand_context() -> dict:
    """Identity every template renders with (settings.EMAIL_BRAND + the year)."""
    return {"brand": dict(getattr(settings, "EMAIL_BRAND", {}) or {}), "year": timezone.now().year}


def _render_html(notification) -> str:
    """Render the HTML body, preferring the notification's template, then default."""
    context = {"notification": notification, **brand_context(), **(notification.variables or {})}
    for template in (notification.html_template, DEFAULT_TEMPLATE):
        if not template:
            continue
        try:
            return render_to_string(template, context)
        except TemplateDoesNotExist:
            continue
    # No template available — wrap the plain message in minimal HTML.
    return f"<p>{escape(notification.message)}</p>"


def send(notification, config=None) -> tuple[bool, str]:
    """Send ``notification`` by e-mail. Returns ``(ok, remarks)``.

    ``config`` sends through one specific mail server (the "send a test
    message" action); by default the active configuration is used.
    """
    if not notification.email_to:
        return False, "No recipient e-mail address"
    try:
        connection, sender = _connection_and_sender(config)
        html_body = _render_html(notification)
        text_body = notification.message or strip_tags(html_body)

        email = EmailMultiAlternatives(
            subject=notification.subject or notification.title,
            body=text_body,
            from_email=sender,
            to=[notification.email_to],
            connection=connection,
        )
        email.attach_alternative(html_body, "text/html")
        email.send(fail_silently=False)
        logger.info("[email.send] delivered notification %s", notification.uid)
        return True, ""
    except Exception as exc:  # noqa: BLE001 — any SMTP failure is a delivery failure
        logger.error("[email.send] failed for notification %s: %s", notification.uid, exc)
        return False, str(exc)
