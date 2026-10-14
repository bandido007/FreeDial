"""Root GraphQL schema.

Each app contributes a ``Query`` (``queries.py``) and/or a ``Mutation``
(``mutations.py``). They are combined here through multiple inheritance.

To plug in a new app, add its label to ``config/modules.py`` — nothing in this
file changes. Optional modules are skipped when they are not installed.
"""

from importlib import import_module
from importlib.util import find_spec

import graphene
from django.apps import apps as django_apps

from config.modules import ALL_MODULES


def _collect(module_suffix: str, class_name: str) -> tuple:
    """The named class from every installed module that defines it."""
    found = []
    for label in ALL_MODULES:
        if not django_apps.is_installed(label):
            continue
        module_name = f"{label}.{module_suffix}"
        if find_spec(module_name) is None:
            continue
        cls = getattr(import_module(module_name), class_name, None)
        if cls is not None:
            found.append(cls)
    return tuple(found)


QUERY_BASES = _collect("queries", "Query")
MUTATION_BASES = _collect("mutations", "Mutation")


class Query(*QUERY_BASES, graphene.ObjectType):  # type: ignore[misc]
    """The unified root query, composed from every app's Query."""


class Mutation(*MUTATION_BASES, graphene.ObjectType):  # type: ignore[misc]
    """The unified root mutation, composed from every app's Mutation."""


schema = graphene.Schema(query=Query, mutation=Mutation)
