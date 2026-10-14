"""GraphQL output types for system settings."""

import graphene
from graphene.types.generic import GenericScalar


class SystemSettingType(graphene.ObjectType):
    """One runtime setting: what it is, its value, and what it may be set to."""

    key = graphene.String()
    group = graphene.String()
    name = graphene.String()
    description = graphene.String()
    type = graphene.String(description="BOOLEAN · INTEGER · STRING · TEXT · CHOICE · LIST · EMAIL · URL")
    value = GenericScalar()
    default_value = GenericScalar()
    is_changed = graphene.Boolean(description="False while the setting is at its default.")
    choices = graphene.List(graphene.String)
    minimum = graphene.Int()
    maximum = graphene.Int()
    public = graphene.Boolean(description="Readable without signing in.")


class PublicSettingsType(graphene.ObjectType):
    """What the sign-in page needs, keyed by setting."""

    values = GenericScalar()
    project_name = graphene.String()
