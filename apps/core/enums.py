"""Cross-cutting enumerations.

Only enums that *every* app may need live here. An enum that belongs to one
domain lives in that app's own ``enums.py`` — this file must never grow a
business concept.

Every enum subclasses :class:`BaseEnum`, which provides the ``choices()`` /
``dict()`` / ``values()`` helpers in one place. Each enum is also exposed as a
graphene Enum (``…Enum``) for use in the GraphQL schema.
"""

import enum

import graphene


class BaseEnum(enum.Enum):
    """Common helpers for all string enums."""

    @classmethod
    def choices(cls):
        """Django ``choices=`` tuples: ``[(value, name), ...]``."""
        return [(member.value, member.name) for member in cls]

    @classmethod
    def values(cls):
        return [member.value for member in cls]

    @classmethod
    def dict(cls):
        return [{"value": member.value, "name": member.name} for member in cls]


def to_values(value):
    """Normalise an enum / list-of-enums to its raw value(s)."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return [getattr(item, "value", item) for item in value]
    return getattr(value, "value", value)


class GenderChoices(BaseEnum):
    MALE = "MALE"
    FEMALE = "FEMALE"


GenderEnum = graphene.Enum.from_enum(GenderChoices)


# ── Client platform (sent via the `Platform` header) ───────────────────────
class PlatformChoices(BaseEnum):
    Web = "web"
    Mobile = "mobile"
    Desktop = "desktop"


PlatformEnum = graphene.Enum.from_enum(PlatformChoices)


# ── Generic search operators (used by the pageable search engine) ──────────
class SearchOperationType(BaseEnum):
    Equals = "Equals"
    NotEqual = "NotEqual"
    GreaterThan = "GreaterThan"
    GreaterThanEqual = "GreaterThanEqual"
    LessThan = "LessThan"
    LessThanEqual = "LessThanEqual"
    Between = "Between"
    Like = "Like"
    NotLike = "NotLike"
    In = "In"
    NotIn = "NotIn"
    IsNull = "IsNull"
    IsNotNull = "IsNotNull"


SearchOperationTypeEnum = graphene.Enum.from_enum(SearchOperationType)


class SearchCombinationType(BaseEnum):
    AND = "AND"
    OR = "OR"


SearchCombinationTypeEnum = graphene.Enum.from_enum(SearchCombinationType)


# ── Identity classification ────────────────────────────────────────────────
# Cross-cutting on purpose: the permission decorators, the notification deep
# links and the login gates all read these, so they live below every app.


# Coarse origin category — one per account.
class AccountTypeChoices(BaseEnum):
    System = "System"  # platform operators / administrators
    Internal = "Internal"  # the organisation's own staff (often from a directory)
    External = "External"  # everyone else: the public, partners, customers


AccountTypeEnum = graphene.Enum.from_enum(AccountTypeChoices)


# How the account proves who it is.
class AuthProviderChoices(BaseEnum):
    DB = "DB"  # e-mail + password held by this system
    LDAP = "LDAP"  # a directory (Active Directory / OpenLDAP)
    GOOGLE = "GOOGLE"  # Google sign-in
    OIDC = "OIDC"  # any other OpenID Connect provider


AuthProviderEnum = graphene.Enum.from_enum(AuthProviderChoices)


# Operator-controlled access gate. Orthogonal to ``is_verified`` (e-mail proof)
# and ``deleted`` (soft delete): this is the *operator's* switch. ACTIVE is the
# norm; SUSPENDED is a temporary hold (reversible), BLOCKED is a hard ban.
# Sign-in is refused for the latter two.
class AccountStatusChoices(BaseEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    BLOCKED = "BLOCKED"


AccountStatusEnum = graphene.Enum.from_enum(AccountStatusChoices)

# Account types that belong to the organisation running the system.
STAFF_ACCOUNT_TYPES = frozenset({AccountTypeChoices.System.value, AccountTypeChoices.Internal.value})
