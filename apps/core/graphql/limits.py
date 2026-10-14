"""Query guard rails: depth, complexity, aliases and root fields.

GraphQL lets the *client* decide how much work a request is. Left unchecked,
one document can nest a relation twenty levels deep, select thousands of
fields, or repeat an expensive field under a hundred aliases (the classic way
to batch password guesses into a single request that a per-request rate limit
counts once).

:func:`measure` walks a parsed document once and returns its cost;
:func:`check` compares that to the configured limits. Fragments are expanded,
so hiding the weight inside a fragment does not help. The walk runs *before*
validation and execution, guards against fragment cycles on its own, and never
touches the database.

===================  ==============================  =======
Limit                Setting                         Default
===================  ==============================  =======
Nesting depth        ``GRAPHQL_MAX_DEPTH``           10
Fields selected      ``GRAPHQL_MAX_COMPLEXITY``      500
Aliased fields       ``GRAPHQL_MAX_ALIASES``         15
Top-level fields     ``GRAPHQL_MAX_ROOT_FIELDS``     10
===================  ==============================  =======

Introspection fields (``__schema``, ``__type``, ``__typename``) are not
counted: they are switched off wholesale in production, and the IDE's own
introspection query must work in development.
"""

from dataclasses import dataclass

from django.conf import settings
from graphql import (
    DocumentNode,
    FieldNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    InlineFragmentNode,
    OperationDefinitionNode,
)


@dataclass
class QueryCost:
    """What one document asks of the server."""

    depth: int = 0
    fields: int = 0
    aliases: int = 0
    root_fields: int = 0


@dataclass(frozen=True)
class QueryLimits:
    max_depth: int
    max_complexity: int
    max_aliases: int
    max_root_fields: int

    @classmethod
    def from_settings(cls) -> "QueryLimits":
        return cls(
            max_depth=int(getattr(settings, "GRAPHQL_MAX_DEPTH", 10)),
            max_complexity=int(getattr(settings, "GRAPHQL_MAX_COMPLEXITY", 500)),
            max_aliases=int(getattr(settings, "GRAPHQL_MAX_ALIASES", 15)),
            max_root_fields=int(getattr(settings, "GRAPHQL_MAX_ROOT_FIELDS", 10)),
        )


def _is_introspection(name: str) -> bool:
    return name.startswith("__")


def measure(document: DocumentNode) -> QueryCost:
    """The cost of the most expensive operation in ``document``."""
    fragments = {
        definition.name.value: definition
        for definition in document.definitions
        if isinstance(definition, FragmentDefinitionNode)
    }
    worst = QueryCost()
    for definition in document.definitions:
        if not isinstance(definition, OperationDefinitionNode):
            continue
        cost = QueryCost()
        _walk(definition.selection_set, 1, cost, fragments, frozenset(), is_root=True)
        worst = QueryCost(
            depth=max(worst.depth, cost.depth),
            fields=max(worst.fields, cost.fields),
            aliases=max(worst.aliases, cost.aliases),
            root_fields=max(worst.root_fields, cost.root_fields),
        )
    return worst


def _walk(selection_set, depth, cost, fragments, visiting, *, is_root=False) -> None:
    if selection_set is None:
        return
    for selection in selection_set.selections:
        if isinstance(selection, FieldNode):
            if _is_introspection(selection.name.value):
                continue
            cost.fields += 1
            cost.depth = max(cost.depth, depth)
            if is_root:
                cost.root_fields += 1
            if selection.alias is not None:
                cost.aliases += 1
            _walk(selection.selection_set, depth + 1, cost, fragments, visiting)
        elif isinstance(selection, InlineFragmentNode):
            _walk(selection.selection_set, depth, cost, fragments, visiting, is_root=is_root)
        elif isinstance(selection, FragmentSpreadNode):
            name = selection.name.value
            # A fragment that (directly or not) spreads itself is invalid
            # GraphQL; validation rejects it later. Here it just must not loop.
            if name in visiting or name not in fragments:
                continue
            _walk(
                fragments[name].selection_set,
                depth,
                cost,
                fragments,
                visiting | {name},
                is_root=is_root,
            )


def check(document: DocumentNode, limits: QueryLimits | None = None) -> str | None:
    """The reason ``document`` is refused, or ``None`` when it is within limits."""
    limits = limits or QueryLimits.from_settings()
    cost = measure(document)
    if cost.depth > limits.max_depth:
        return f"Query is nested too deeply ({cost.depth} levels; the limit is {limits.max_depth})."
    if cost.fields > limits.max_complexity:
        return f"Query selects too many fields ({cost.fields}; the limit is {limits.max_complexity})."
    if cost.aliases > limits.max_aliases:
        return f"Query uses too many aliases ({cost.aliases}; the limit is {limits.max_aliases})."
    if cost.root_fields > limits.max_root_fields:
        return (
            f"Query asks for too many top-level fields ({cost.root_fields}; "
            f"the limit is {limits.max_root_fields})."
        )
    return None
