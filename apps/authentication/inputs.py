"""GraphQL input types for the authentication domain."""

import graphene

from apps.authentication.enums import AuthEventEnum


class ChangePasswordInput(graphene.InputObjectType):
    old_password = graphene.String(required=True)
    new_password = graphene.String(required=True)
    confirm_password = graphene.String(required=True)


class SetPasswordInput(graphene.InputObjectType):
    token = graphene.String(required=True, description="The token from the e-mailed link")
    new_password = graphene.String(required=True)
    confirm_password = graphene.String(required=True)


class LoginHistoryFilterInput(graphene.InputObjectType):
    user_uid = graphene.UUID()
    is_new_device = graphene.Boolean()
    search = graphene.String(description="Free text across e-mail, address and device")
    date_from = graphene.DateTime(description="logged_in_at >= this")
    date_to = graphene.DateTime(description="logged_in_at <= this")


class AuthenticationEventFilterInput(graphene.InputObjectType):
    user_uid = graphene.UUID()
    event_type = graphene.Field(AuthEventEnum)
    status = graphene.String(description="success or failure")
    search = graphene.String(description="Free text across e-mail and address")
    request_id = graphene.String()
    date_from = graphene.DateTime(description="created_at >= this")
    date_to = graphene.DateTime(description="created_at <= this")


class LdapConfigInput(graphene.InputObjectType):
    uid = graphene.UUID(description="Required when updating")
    name = graphene.String()
    ldap_url = graphene.String(description="ldap:// or ldaps:// URL")
    bind_dn = graphene.String()
    bind_password = graphene.String(description="Write-only; stored encrypted")
    search_base = graphene.String()
    email_attribute = graphene.String(description="Directory attribute holding the e-mail (default 'mail')")
    email_domain = graphene.String(description="Addresses in this domain sign in against this directory")
    default_role_uid = graphene.UUID(description="Role granted to accounts from this directory")
    clear_default_role = graphene.Boolean(description="Remove the default role (update only)")
    is_active = graphene.Boolean()


class LdapConfigFilterInput(graphene.InputObjectType):
    uid = graphene.UUID()
    is_active = graphene.Boolean()
    active = graphene.Boolean()
