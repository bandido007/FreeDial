"""Helpers for reading the request context inside GraphQL resolvers.

Centralises the ``info.context`` access patterns (current user, headers,
language, platform) so resolvers stay terse and null-safe.
"""


def current_user(info):
    user = getattr(getattr(info, "context", None), "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    return None


def username(info):
    user = current_user(info)
    # User is email-based; get_username() returns the USERNAME_FIELD value.
    return user.get_username() if user else None


def account_type(info):
    user = current_user(info)
    return user.account_type if user else None


def header(info, key, default=None):
    try:
        return info.context.headers.get(key, default)
    except Exception:
        return default


def language(info) -> str:
    return (header(info, "Content-Language", "en") or "en").lower()


def platform(info) -> str:
    return (header(info, "Platform", "web") or "web").lower()
