"""SMS delivery backend — a pluggable provider registry.

Three providers ship with the starter:

* ``LOG`` — no gateway. The payload is written to the log; that counts as a
  delivery only where ``settings.SMS_ALLOW_LOG_PROVIDER`` says so (development,
  the test suite). Anywhere else it fails with a clear remark instead of telling
  a person their code was sent.
* ``MGOV`` — the **eGA mGov SMS gateway** (``…/gateway/sms/v2/send-sms``): a
  compact JSON body ``{recipients, message, datetime, mobileServiceId,
  senderId, messageId}`` signed with HMAC-SHA256 under the API key (base64
  digest in the ``hash`` header) and identified by the ``sysId`` header. The
  gateway answers with a status code — ``48200`` means accepted, everything
  else is mapped to a human remark so the notification log says *why* (wrong
  sender ID, empty balance, restricted words…).
* ``HTTP_JSON`` — a generic gateway: ``POST {to, message, sender}`` as JSON with
  ``Authorization: Bearer <api key>``. Any 2xx answer counts as accepted.

Adding a provider
-----------------
Write ``send(phone, message, config) -> (ok, remarks)`` and register it from
your app's ``AppConfig.ready()``::

    from apps.notifications.backends import sms
    sms.register_provider("ACME", "Acme SMS", send_via_acme)

**Configuration precedence, per field.** The active
:class:`~apps.notifications.models.SmsConfiguration` row wins; any value it
leaves blank falls back to the ``SMS_*`` settings read from the environment. A
deployment can therefore run from ``.env`` alone — no row at all — or override
individual values from the admin screen.
"""

import base64
import hashlib
import hmac
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

import requests
from django.conf import settings
from django.utils import timezone

from apps.core.exceptions import ValidationError
from apps.core.utils import Utils
from apps.core.validators import normalize_phone

logger = logging.getLogger(__name__)

# (connect, read) — give a gateway on a slow network a moment.
REQUEST_TIMEOUT_SECONDS = (5, 30)

# Two GSM-7 segments. Long enough for a message with a link, short enough that
# a template mistake cannot fan out a 6-segment bill.
SMS_MAX_CHARS = 320

# Provider keys (stored on SmsConfiguration.provider).
PROVIDER_LOG = "LOG"
PROVIDER_MGOV = "MGOV"
PROVIDER_HTTP_JSON = "HTTP_JSON"

NO_GATEWAY_REMARK = "No SMS gateway is configured (the LOG provider only delivers in development)"

# ── mGov gateway response codes ──────────────────────────────────────────────
MGOV_SUCCESS_CODE = "48200"
MGOV_RESPONSE_CODES = {
    # Per-message problems (fixable by the sender)
    "48225": "Invalid recipient phone number",
    "48207": "Message content rejected by gateway (restricted words)",
    # Account and balance (ops)
    "48206": "Insufficient SMS account balance",
    "48247": "SMS billing account not found",
    "48253": "SMS billing deduction failed",
    # Credentials and configuration
    "48241": "Invalid API key or credentials (check the API key)",
    "48250": "Authentication failed (check the API key)",
    "48251": "Unauthorized (check the API key permissions)",
    "48259": "Invalid System ID (check SMS_SYSTEM_ID)",
    "48260": "Hash mismatch (check the API key / payload signing)",
    "48229": "Not authorized for this Service ID (check SMS_SERVICE_CODE)",
    "48231": "Sender ID not registered (check SMS_SENDER_ID)",
    "48244": "Sender ID not whitelisted (check SMS_SENDER_ID)",
    "48248": "Sender ID deactivated (check SMS_SENDER_ID)",
    # Request format (developer)
    "48242": "Required data field missing from request",
    "48246": "Mandatory headers missing from request",
    "48249": "Request validation error",
    "48245": "Invalid datetime format (gateway allows yyyy-MM-dd HH:mm)",
    # Transient / server-side (retryable)
    "48252": "Gateway timeout, retry later",
    "48261": "Gateway internal error, retry later",
    "48228": "Unexpected gateway error, retry later",
}

# ── Gateway settings: row first, then the environment ────────────────────────
GATEWAY_FIELDS = ("api_url", "api_key", "sender_id", "system_id", "service_code")
GATEWAY_LABELS = {
    "api_url": "API URL (SMS_API_URL)",
    "api_key": "API key (SMS_API_KEY)",
    "sender_id": "Sender ID (SMS_SENDER_ID)",
    "system_id": "System ID (SMS_SYSTEM_ID)",
    "service_code": "Service code (SMS_SERVICE_CODE)",
}
# What each built-in provider cannot work without.
REQUIRED_FIELDS = {
    PROVIDER_LOG: (),
    PROVIDER_MGOV: GATEWAY_FIELDS,
    PROVIDER_HTTP_JSON: ("api_url", "api_key"),
}


@dataclass
class GatewaySettings:
    """The values a gateway needs, with a note of where each came from."""

    api_url: str = ""
    api_key: str = ""
    sender_id: str = ""
    system_id: str = ""
    service_code: str = ""
    from_env: list[str] = field(default_factory=list)
    required: tuple = GATEWAY_FIELDS

    @property
    def missing(self) -> list[str]:
        return [name for name in self.required if not getattr(self, name)]

    @property
    def ready(self) -> bool:
        return not self.missing


def env_gateway() -> dict[str, str]:
    """The ``SMS_*`` settings as a plain mapping (blank when unset)."""
    return {name: (getattr(settings, f"SMS_{name.upper()}", "") or "").strip() for name in GATEWAY_FIELDS}


def resolve_gateway(config=None, provider: str | None = None) -> GatewaySettings:
    """Merge a configuration row with the environment: the row wins per field,
    blanks fall back to the environment. ``config`` may be ``None`` (env only)."""
    env = env_gateway()
    provider = provider or provider_for(config)
    resolved = GatewaySettings(required=REQUIRED_FIELDS.get(provider, ("api_url",)))
    for name in GATEWAY_FIELDS:
        own = ""
        if config is not None:
            own = config.get_api_key() if name == "api_key" else getattr(config, name, "")
            own = (own or "").strip()
        if own:
            setattr(resolved, name, own)
        elif env[name]:
            setattr(resolved, name, env[name])
            resolved.from_env.append(name)
    return resolved


def _active_config():
    from apps.notifications.models import SmsConfiguration

    return SmsConfiguration.objects.filter(is_default=True).first() or SmsConfiguration.objects.first()


def log_provider_allowed() -> bool:
    """Whether "delivering" to the log counts as success in this environment."""
    return bool(getattr(settings, "SMS_ALLOW_LOG_PROVIDER", settings.DEBUG))


def clamp_sms(message: str) -> str:
    message = (message or "").strip()
    if len(message) <= SMS_MAX_CHARS:
        return message
    return message[: SMS_MAX_CHARS - 1] + "…"


def deliverable_phone(raw: str | None) -> str:
    """The number as the gateway should receive it.

    The shared validator is the source of truth — it upgrades local shapes
    (``0755…``, ``755…``) to the international form exactly as registration
    does, so a profile-edited number sends the same way it was validated.
    Anything it rejects falls back to the light tidy-up so older rows still get
    a best-effort attempt.
    """
    if not raw:
        return ""
    try:
        return normalize_phone(str(raw))
    except ValidationError:
        return Utils.normalize_phone(raw)


# ── Providers ────────────────────────────────────────────────────────────────
def send_via_log(phone: str, message: str, config) -> tuple[bool, str]:
    """The no-gateway provider: log that a message was produced; succeed only
    where that is an acceptable outcome (development / CI).

    The notification row still records exactly what *would* have been sent, so
    flows (and tests) exercise the full path without a live gateway — but a
    production deployment without a gateway surfaces as a failed notification
    instead of a phantom delivery. The body is logged only in DEBUG: it may
    carry a verification code.
    """
    if settings.DEBUG:
        logger.info("[sms.LOG] to=%s chars=%s body=%r", phone, len(message), message)
    else:
        logger.info("[sms.LOG] to=%s chars=%s", phone, len(message))
    if not log_provider_allowed():
        return False, NO_GATEWAY_REMARK
    return True, "logged (no live gateway configured)"


def _mgov_response_code(response) -> str | None:
    """Pull the mGov status code out of a gateway response body, if present."""
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    for key in ("code", "statusCode", "status", "responseCode"):
        if body.get(key) is not None:
            return str(body[key])
    return None


def build_mgov_request(gateway: GatewaySettings, phone: str, message: str) -> tuple[str, dict, str]:
    """``(body, headers, message_id)`` exactly as the gateway must receive them.

    The HMAC is computed over the *exact* serialised body (compact separators),
    so the same string is signed and sent — a re-serialisation on the way out
    would change the hash. The recipient is sent without the leading ``+``,
    which is how the mGov contract expects an MSISDN.
    """
    message_id = str(uuid.uuid4())
    payload = {
        "recipients": phone.lstrip("+"),
        "message": message,
        "datetime": timezone.localtime().strftime("%Y-%m-%d %H:%M:%S"),
        "mobileServiceId": gateway.service_code,
        "senderId": gateway.sender_id,
        "messageId": message_id,
    }
    body = json.dumps(payload, separators=(",", ":"))
    digest = hmac.new(gateway.api_key.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()
    headers = {
        "Content-Type": "application/json",
        "hash": base64.b64encode(digest).decode("utf-8"),
        "sysId": gateway.system_id,
    }
    return body, headers, message_id


def _not_configured(gateway: GatewaySettings) -> tuple[bool, str]:
    labels = ", ".join(GATEWAY_LABELS[name] for name in gateway.missing)
    return False, f"SMS gateway not configured — missing {labels}"


def send_via_mgov(phone: str, message: str, config) -> tuple[bool, str]:
    """The eGA mGov SMS gateway. Returns ``(ok, remarks)``; the remark names the
    gateway code so ops can act on the notification log."""
    gateway = resolve_gateway(config, PROVIDER_MGOV)
    if gateway.missing:
        return _not_configured(gateway)

    body, headers, message_id = build_mgov_request(gateway, phone, message)
    try:
        response = requests.post(gateway.api_url, data=body, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:  # network / timeout / TLS
        logger.error("[sms.MGOV] gateway unreachable: %s", exc)
        return False, f"Gateway unreachable: {exc}"

    logger.info("[sms.MGOV] HTTP %s :: %s", response.status_code, response.text[:200])
    code = _mgov_response_code(response)
    if code == MGOV_SUCCESS_CODE:
        return True, f"mGov accepted (messageId {message_id})"
    if code is not None:
        known = MGOV_RESPONSE_CODES.get(code)
        remark = f"{known} (mGov {code})" if known else f"SMS gateway error {code}: {response.text[:300]}"
        logger.warning("[sms.MGOV] mGov code %s :: %s", code, response.text[:300])
        return False, remark
    # No recognisable gateway code in the body — fall back to the HTTP status.
    if 200 <= response.status_code < 300:
        return True, f"HTTP {response.status_code} (messageId {message_id})"
    return False, f"HTTP {response.status_code}: {response.text[:300]}"


def send_via_http_json(phone: str, message: str, config) -> tuple[bool, str]:
    """A generic JSON gateway: bearer-authenticated ``POST {to, message, sender}``."""
    gateway = resolve_gateway(config, PROVIDER_HTTP_JSON)
    if gateway.missing:
        return _not_configured(gateway)
    payload = {"to": phone, "message": message, "sender": gateway.sender_id}
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {gateway.api_key}"}
    try:
        response = requests.post(
            gateway.api_url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        logger.error("[sms.HTTP_JSON] gateway unreachable: %s", exc)
        return False, f"Gateway unreachable: {exc}"
    if 200 <= response.status_code < 300:
        return True, f"HTTP {response.status_code}"
    return False, f"HTTP {response.status_code}: {response.text[:300]}"


Sender = Callable[[str, str, object], tuple[bool, str]]
PROVIDERS: dict[str, Sender] = {}
PROVIDER_LABELS: dict[str, str] = {}


def register_provider(key: str, label: str, sender: Sender, *, required: tuple = ("api_url",)) -> None:
    """Make a provider selectable on the SMS configuration screen."""
    key = key.strip().upper()
    PROVIDERS[key] = sender
    PROVIDER_LABELS[key] = label
    REQUIRED_FIELDS.setdefault(key, tuple(required))


register_provider(PROVIDER_LOG, "Log only (no gateway — development and CI)", send_via_log, required=())
register_provider(PROVIDER_MGOV, "eGA mGov SMS gateway", send_via_mgov, required=GATEWAY_FIELDS)
register_provider(
    PROVIDER_HTTP_JSON,
    "Generic JSON gateway (bearer token)",
    send_via_http_json,
    required=("api_url", "api_key"),
)


def provider_for(config) -> str:
    """Which provider a send goes through: the row's, else the mGov gateway when
    the environment names one, else the log."""
    if config is not None:
        return getattr(config, "provider", None) or PROVIDER_LOG
    return PROVIDER_MGOV if env_gateway()["api_url"] else PROVIDER_LOG


def send_with_config(phone: str, message: str, config) -> tuple[bool, str]:
    """Send through an explicit configuration row (``None`` = environment only)."""
    phone = deliverable_phone(phone)
    if not phone:
        return False, "No recipient phone number"
    provider_key = provider_for(config)
    provider = PROVIDERS.get(provider_key)
    if provider is None:
        return False, f"Unknown SMS provider '{provider_key}'"
    return provider(phone, clamp_sms(message), config)


def send(notification) -> tuple[bool, str]:
    """Send ``notification`` by SMS through the active configuration."""
    return send_with_config(notification.phone_to, notification.message, _active_config())
