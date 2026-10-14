"""Domain-level exceptions.

Services and selectors raise these instead of returning ad-hoc error tuples.
The GraphQL error-logging decorator maps them onto the standard envelope
(``{status, code, message, data}``) so the API surface stays consistent.
"""


class ApplicationError(Exception):
    """Base class for expected, business-rule violations.

    Carries an optional status ``code`` (see core/status_codes.json) so the
    response layer can translate it to a localised message.
    """

    default_code = 9004  # "Failed to perform this operation"

    def __init__(self, message: str | None = None, code: int | None = None):
        self.message = message or "An application error occurred"
        self.code = code or self.default_code
        super().__init__(self.message)


class ValidationError(ApplicationError):
    default_code = 9002  # "Invalid input provided"


class NotFoundError(ApplicationError):
    default_code = 9003  # "Record not found"


class PermissionDeniedError(ApplicationError):
    default_code = 9000  # "You're not authorized to perform this action"


class DuplicateError(ApplicationError):
    default_code = 9006  # "Duplicate record"


class ThrottledError(ApplicationError):
    """Too many requests from the same origin in the configured window."""

    default_code = 9018  # "Too many attempts. Please slow down and try again shortly."
