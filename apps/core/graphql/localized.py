"""The shared bilingual string pair used across the schema.

One definition on purpose: graphene resolves schema types by NAME, so two apps
each declaring a ``LocalizedType`` register two classes for one name and one
silently shadows the other (harmless only while the shapes stay identical —
the committee facade bug showed what happens when they don't). Import these
instead of re-declaring.
"""

import graphene


class LocalizedType(graphene.ObjectType):
    """A translated string in both supported languages."""

    en = graphene.String()
    sw = graphene.String()


class LocalizedInput(graphene.InputObjectType):
    """A translated string supplied in either/both languages."""

    en = graphene.String()
    sw = graphene.String()
