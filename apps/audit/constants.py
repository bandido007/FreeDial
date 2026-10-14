"""How the change trail and the endpoint log decide what to record.

Which models are audited
------------------------
Every model under ``apps/`` that subclasses
:class:`~apps.core.models.BaseModel` is audited automatically. A model states
an exception **on itself**, so adding a business app never edits this file:

==========================  ===================================================
Class attribute             Effect
==========================  ===================================================
``AUDIT_EXCLUDE = True``    never audited (logs, short-lived secret carriers)
``AUDIT_INCLUDE = True``    audited although it is not a ``BaseModel``
``AUDIT_MASK_FIELDS = (…)``  these fields are recorded as ``[REDACTED]``
``AUDIT_IGNORE_FIELDS``     these fields never appear in a diff
==========================  ===================================================

Models from third-party packages are named in the ``AUDIT_EXTRA_MODELS``
setting (``["oauth2_provider.application"]``).
"""

# Fields never included in a change diff (pure bookkeeping noise).
IGNORED_DIFF_FIELDS = frozenset(
    {
        "id",
        "uid",
        "created_at",
        "updated_at",
        "last_login",
        "created_by_id",
        "updated_by_id",
        "deleted_by_id",
        "deactivated_by_id",
    }
)

# A field whose name contains one of these tokens has its values masked in the
# diff and in logged request variables (the *fact* of the change is still
# recorded).
SENSITIVE_FIELD_TOKENS = (
    "password",
    "secret",
    "token",
    "code_hash",
    "api_key",
    "otp",
    "credential",
    "authorization",
    "private_key",
    "recovery",
)
SENSITIVE_MASK = "[REDACTED]"


def names_a_secret(name) -> bool:
    """Whether a field or variable name looks like it carries a secret.

    Compared with underscores removed, so the camelCase spellings GraphQL
    clients send (``apiKey``, ``newPassword``, ``codeHash``) match the same
    tokens as the snake_case model fields.
    """
    flattened = str(name).lower().replace("_", "")
    return any(token.replace("_", "") in flattened for token in SENSITIVE_FIELD_TOKENS)


# GraphQL operations that are pure IDE/tooling chatter — never persisted.
SKIPPED_OPERATIONS = frozenset({"IntrospectionQuery"})

DEFAULT_TRACKED_PREFIXES = ("/graphql", "/oauth2/", "/files/")
