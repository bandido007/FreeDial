"""REST serializers for the token endpoints.

The GraphQL layer has its own graphene types; these shape the user object that
is embedded in the ``/oauth2/access_token`` response so the client gets the
profile in the same round-trip as the tokens.
"""

from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.authentication import gates
from apps.core.hooks import resolve_permissions


class UserSummarySerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    authorities = serializers.SerializerMethodField()
    is_profile_complete = serializers.BooleanField(read_only=True)
    missing_profile_fields = serializers.SerializerMethodField()
    has_password = serializers.SerializerMethodField()

    class Meta:
        model = get_user_model()
        fields = (
            "uid",
            "email",
            "full_name",
            "first_name",
            "middle_name",
            "last_name",
            "phone",
            "gender",
            "photo",
            "date_of_birth",
            "account_type",
            "auth_provider",
            "account_status",
            "is_verified",
            "phone_verified",
            "mfa_enabled",
            "has_password",
            "is_profile_complete",
            "missing_profile_fields",
            "authorities",
        )
        read_only_fields = fields

    def get_full_name(self, obj) -> str:
        return obj.get_full_name()

    def get_missing_profile_fields(self, obj) -> list[str]:
        return obj.missing_profile_fields()

    def get_has_password(self, obj) -> bool:
        return obj.has_usable_password()

    def get_authorities(self, obj) -> list[str]:
        return sorted(resolve_permissions(obj))


def build_user_payload(user) -> dict:
    """Serialise a user for embedding in the token response."""
    return UserSummarySerializer(user).data


def build_requirements(user, request=None) -> dict:
    """What the account must do before it may use the API (see gates.restriction)."""
    required = gates.restriction(user, request)
    expires = gates.password_expires_at(user)
    return {
        "mfa_enrolment_required": bool(required and required[0] == gates.MFA_ENROLMENT_REQUIRED),
        "password_change_required": bool(required and required[0] == gates.PASSWORD_CHANGE_REQUIRED),
        "password_expires_at": expires.isoformat() if expires else None,
    }
