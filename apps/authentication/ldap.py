"""Directory (LDAP) sign-in against a stored directory configuration.

A person whose e-mail address ends in a configuration's ``email_domain`` is
authenticated against that directory.

Flow for a sign-in attempt (e-mail + password):
  1. find the active :class:`~apps.authentication.models.LdapConfig` for the
     address's domain; none → return ``None`` so the next backend in
     ``AUTHENTICATION_BACKENDS`` (the local password) handles the person;
  2. bind to the directory with the service account, search for the person's
     entry by the configured e-mail attribute, then re-bind as that entry with
     the supplied password to verify it;
  3. on success, find the local account — or create it on first sign-in — and
     return it. **No password is stored locally**: the directory stays the only
     place it is checked.

``python-ldap`` is an optional dependency (it needs system libraries), so the
import is guarded: without it the backend returns ``None`` and authentication
falls through to the local backends.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from apps.core.exceptions import ApplicationError, ValidationError

logger = logging.getLogger(__name__)

# Bound the directory walk so a misconfigured base DN can't pin a worker; the
# server cookie still paginates a large directory in 500-entry slices.
LDAP_PAGE_SIZE = 500
LDAP_TIMEOUT_SECONDS = 10
DIRECTORY_UNREACHABLE = 9125

try:  # python-ldap is optional (see requirements/ldap.txt).
    import ldap
    from ldap.filter import escape_filter_chars

    LDAP_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on an optional system dependency
    ldap = None
    LDAP_AVAILABLE = False

    def escape_filter_chars(value, escape_mode=0):
        """RFC 4515 escaping, for building filters where python-ldap is absent."""
        replacements = {"\\": r"\5c", "*": r"\2a", "(": r"\28", ")": r"\29", "\x00": r"\00"}
        return "".join(replacements.get(char, char) for char in str(value))


def config_for_email(email: str):
    """The active directory configuration that owns ``email``'s domain, if any."""
    from apps.authentication.models import LdapConfig

    domain = (email or "").rsplit("@", 1)[-1].strip().lower()
    if not domain or "@" not in (email or ""):
        return None
    return LdapConfig.objects.filter(is_active=True, email_domain__iexact=domain).first()


def _connect(config):
    connection = ldap.initialize(config.ldap_url)
    connection.set_option(ldap.OPT_REFERRALS, 0)
    connection.set_option(ldap.OPT_NETWORK_TIMEOUT, LDAP_TIMEOUT_SECONDS)
    connection.set_option(ldap.OPT_TIMEOUT, LDAP_TIMEOUT_SECONDS)
    connection.simple_bind_s(config.bind_dn, config.get_bind_password())
    return connection


class ConfiguredLdapBackend(ModelBackend):
    """Authenticate a person against the directory that owns their e-mail domain."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not LDAP_AVAILABLE or not username or not password:
            return None

        email = username.strip().lower()
        config = config_for_email(email)
        if config is None:
            return None  # not a directory account — the local backends handle it

        attribute = config.email_attribute or "mail"
        connection = None
        try:
            connection = _connect(config)
            # The address is typed by whoever is signing in: escape it, or a
            # crafted value rewrites the search filter.
            search_filter = f"({attribute}={escape_filter_chars(email)})"
            results = connection.search_s(config.search_base, ldap.SCOPE_SUBTREE, search_filter, [attribute])
            entries = [entry for entry in results if entry and entry[0]]
            if len(entries) != 1:
                logger.warning("[ldap] %s directory entries matched a sign-in attempt", len(entries))
                return None
            # Re-bind as the person to verify the supplied password.
            connection.simple_bind_s(entries[0][0], password)
        except Exception as exc:  # noqa: BLE001 — any bind/search failure = authentication failure
            logger.warning("[ldap] sign-in failed against %s: %s", config.ldap_url, type(exc).__name__)
            return None
        finally:
            if connection is not None:
                try:
                    connection.unbind_s()
                except Exception:  # noqa: BLE001
                    pass

        from apps.authentication.directory_services import ensure_directory_account

        user = get_user_model().all_objects.filter(email__iexact=email).first()
        if user is not None and (user.deleted or not user.is_active):
            return None
        user = ensure_directory_account(email=email, config=config, user=user, active=True)
        logger.info("[ldap] user %s authenticated via %s", user.pk, config.ldap_url)
        return user if self.user_can_authenticate(user) else None


def fetch_directory_emails(config) -> list[str]:
    """Return every e-mail address in ``config``'s directory.

    Binds with the configuration's service account, then walks the directory
    under ``search_base`` with the Simple Paged Results control so large
    directories don't trip the server-side size limit. The control is sent
    non-critical, so servers that don't support it behave like a single search.

    Raises :class:`ApplicationError` when the directory cannot be reached and
    :class:`ValidationError` when the configuration is unusable. Returns a
    de-duplicated, lower-cased list. Does *no* database work, so the optional
    ``python-ldap`` dependency stays contained in this file.
    """
    domain = (config.email_domain or "").strip().lower()
    if not LDAP_AVAILABLE:
        # Without python-ldap, optionally synthesise a directory so the import
        # flow can be exercised without a server. Off by default; production
        # refuses to start with it on.
        if getattr(settings, "LDAP_SIMULATE_DIRECTORY", False):
            return simulated_directory_emails(config)
        raise ApplicationError(
            "Directory import is unavailable in this environment (python-ldap is not installed).",
            code=DIRECTORY_UNREACHABLE,
        )
    if not config or not config.ldap_url or not config.search_base:
        raise ValidationError(
            "The directory configuration needs both an LDAP URL and a search base.", code=9002
        )

    attribute = config.email_attribute or "mail"
    search_filter = f"({attribute}=*@{escape_filter_chars(domain)})" if domain else f"({attribute}=*)"

    connection = None
    rows = []
    try:
        from ldap.controls import SimplePagedResultsControl

        connection = _connect(config)
        page_control = SimplePagedResultsControl(criticality=False, size=LDAP_PAGE_SIZE, cookie="")
        while True:
            message_id = connection.search_ext(
                config.search_base,
                ldap.SCOPE_SUBTREE,
                search_filter,
                [attribute],
                serverctrls=[page_control],
            )
            _rtype, rdata, _rmsgid, serverctrls = connection.result3(message_id)
            rows.extend(rdata)

            cookie = next(
                (c.cookie for c in serverctrls if c.controlType == SimplePagedResultsControl.controlType),
                b"",
            )
            if not cookie:
                break
            page_control.cookie = cookie
    except ldap.SERVER_DOWN as exc:
        logger.error("[ldap] directory unreachable: %s", exc)
        raise ApplicationError("The directory server is unreachable.", code=DIRECTORY_UNREACHABLE) from exc
    except ldap.LDAPError as exc:
        logger.error("[ldap] directory search failed: %s", exc)
        raise ApplicationError("The directory search failed.", code=DIRECTORY_UNREACHABLE) from exc
    finally:
        if connection is not None:
            try:
                connection.unbind_s()
            except Exception:  # noqa: BLE001 - best effort on teardown
                pass

    seen, emails = set(), []
    for _dn, attributes in rows:
        if not isinstance(attributes, dict):
            continue
        raw = (attributes.get(attribute) or [None])[0]
        if not raw:
            continue
        email = (raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)).strip().lower()
        if email and email not in seen:
            seen.add(email)
            emails.append(email)
    logger.info("[ldap] %s entries from %s", len(emails), config.ldap_url)
    return emails


# A small, fixed roster used only by the simulated directory.
SIMULATED_DIRECTORY_NAMES = (
    "amina.hassan",
    "john.mwakyusa",
    "grace.kimaro",
    "peter.massawe",
    "fatma.juma",
    "daniel.shirima",
)


def _domain_from_search_base(config) -> str:
    """Reconstruct a DNS domain from a search base DN (``dc=example,dc=org``)."""
    base = getattr(config, "search_base", "") or ""
    parts = [seg.split("=", 1)[1] for seg in base.split(",") if seg.strip().lower().startswith("dc=")]
    return ".".join(part.strip() for part in parts if part.strip())


def simulated_directory_emails(config) -> list[str]:
    """Deterministic fake directory for local testing (``LDAP_SIMULATE_DIRECTORY``).

    Builds a handful of plausible addresses on the configuration's domain (its
    ``email_domain`` if set, else derived from the search base, else
    ``example.org``). Deterministic, so importing again is idempotent.
    """
    domain = (config.email_domain or _domain_from_search_base(config) or "example.org").strip().lstrip("@")
    emails = [f"{name}@{domain.lower()}" for name in SIMULATED_DIRECTORY_NAMES]
    logger.warning(
        "[ldap] python-ldap unavailable — returning %s SIMULATED entries for @%s "
        "(LDAP_SIMULATE_DIRECTORY is on). Never enable this in production.",
        len(emails),
        domain,
    )
    return emails
