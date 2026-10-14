"""Query guard rails: what a document costs, and when it is refused."""

from graphql import parse

from apps.core.graphql import limits

LIMITS = limits.QueryLimits(max_depth=3, max_complexity=6, max_aliases=2, max_root_fields=2)


def test_cost_counts_fields_depth_aliases_and_root_fields():
    cost = limits.measure(parse("query { a { b { c } } second: d }"))
    assert (cost.depth, cost.fields, cost.aliases, cost.root_fields) == (3, 4, 1, 2)


def test_fragments_are_expanded():
    document = parse(
        """
        query { a { ...Inner } }
        fragment Inner on A { b { c d } }
        """
    )
    cost = limits.measure(document)
    assert cost.depth == 3
    assert cost.fields == 4


def test_a_fragment_that_spreads_itself_does_not_loop():
    document = parse(
        """
        query { a { ...Loop } }
        fragment Loop on A { b { ...Loop } }
        """
    )
    assert limits.measure(document).fields == 2


def test_introspection_fields_are_free():
    cost = limits.measure(parse("query { __schema { types { name fields { name } } } a }"))
    assert (cost.fields, cost.depth) == (1, 1)


def test_the_most_expensive_operation_counts():
    document = parse("query Small { a } query Big { a { b { c } } }")
    assert limits.measure(document).depth == 3


def test_refusals_name_the_limit_that_was_passed():
    assert limits.check(parse("query { a }"), LIMITS) is None
    assert "nested too deeply" in limits.check(parse("query { a { b { c { d } } } }"), LIMITS)
    assert "too many fields" in limits.check(parse("query { a { b c d e f g } }"), LIMITS)
    assert "too many aliases" in limits.check(parse("query { x: a y: a z: a }"), LIMITS)
    assert "top-level" in limits.check(parse("query { a b c }"), LIMITS)


def test_limits_come_from_settings(settings):
    settings.GRAPHQL_MAX_DEPTH = 7
    settings.GRAPHQL_MAX_COMPLEXITY = 70
    settings.GRAPHQL_MAX_ALIASES = 5
    settings.GRAPHQL_MAX_ROOT_FIELDS = 4
    assert limits.QueryLimits.from_settings() == limits.QueryLimits(7, 70, 5, 4)
