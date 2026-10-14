"""Signals the users app sends.

``users`` sits below ``authentication`` in the dependency order, so it cannot
call it. Instead it *announces* what happened and the apps above react:

====================  ==========================================================
Signal                Who listens, and what they do
====================  ==========================================================
``user_registered``   authentication: writes the "registered" audit event
``user_invited``      authentication: issues the set-password link and mails it
``account_gated``     authentication: signs the account out everywhere
``email_changed``     authentication: signs the account out everywhere
``account_deleted``   your apps: release whatever they hold for that person
====================  ==========================================================

Business apps may connect to any of these the same way.

All are sent with ``send_robust``: a failing receiver is logged and never
breaks the action that caused the signal.
"""

import logging

from django.dispatch import Signal

logger = logging.getLogger(__name__)

# kwargs: user, ip_address, user_agent
user_registered = Signal()

# kwargs: user, invited_by. A receiver that sends the invitation returns
# {"email_ok": bool}; the service reports it to the administrator.
user_invited = Signal()

# kwargs: user, reason — one of the GATE_* constants below.
account_gated = Signal()

# kwargs: user, old_email
email_changed = Signal()

# kwargs: user, by_admin
account_deleted = Signal()

GATE_DEACTIVATED = "deactivated"
GATE_SUSPENDED = "suspended"
GATE_BLOCKED = "blocked"
GATE_DELETED = "deleted"


def announce(signal: Signal, **kwargs) -> list:
    """Send ``signal`` and return the receivers' answers, logging any failure."""
    answers = []
    for receiver, response in signal.send_robust(sender=None, **kwargs):
        if isinstance(response, Exception):
            logger.error(
                "[signals] receiver %s failed: %s",
                getattr(receiver, "__name__", receiver),
                response,
                exc_info=response,
            )
            continue
        answers.append(response)
    return answers
