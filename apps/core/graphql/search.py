"""Translate a generic ``SearchDataDto`` into a Django ``Q`` expression.

The frontend sends a list of ``{fieldName, searchType, fieldValue|fieldValues}``
descriptors plus an AND/OR combination flag; this builds the equivalent ORM
filter. Field names arrive in camelCase / dotted form and are normalised to
snake_case ORM lookups.

If a ``model`` is supplied, unknown root fields are skipped defensively (so a
malicious or stale client can't filter on arbitrary attributes). The model is
passed in **explicitly** — unlike the legacy implementation which inferred it by
parsing the caller's source with the ``ast`` module.
"""

from django.db.models import Model, Q

from apps.core.enums import SearchCombinationType, SearchOperationType
from apps.core.utils import camel_to_snake

# Operator -> function producing the Q object for (field, value, values).
_OPERATORS = {
    SearchOperationType.Equals.value: lambda f, v, vs: Q(**{f: v}),
    SearchOperationType.NotEqual.value: lambda f, v, vs: ~Q(**{f: v}),
    SearchOperationType.GreaterThan.value: lambda f, v, vs: Q(**{f"{f}__gt": v}),
    SearchOperationType.GreaterThanEqual.value: lambda f, v, vs: Q(**{f"{f}__gte": v}),
    SearchOperationType.LessThan.value: lambda f, v, vs: Q(**{f"{f}__lt": v}),
    SearchOperationType.LessThanEqual.value: lambda f, v, vs: Q(**{f"{f}__lte": v}),
    SearchOperationType.Like.value: lambda f, v, vs: Q(**{f"{f}__icontains": v}),
    SearchOperationType.NotLike.value: lambda f, v, vs: ~Q(**{f"{f}__icontains": v}),
    SearchOperationType.IsNull.value: lambda f, v, vs: Q(**{f"{f}__isnull": True}),
    SearchOperationType.IsNotNull.value: lambda f, v, vs: Q(**{f"{f}__isnull": False}),
    SearchOperationType.Between.value: lambda f, v, vs: (
        Q(**{f"{f}__gte": vs[0], f"{f}__lte": vs[1]}) if vs and len(vs) == 2 else None
    ),
    SearchOperationType.In.value: lambda f, v, vs: Q(**{f"{f}__in": vs}) if vs else None,
    SearchOperationType.NotIn.value: lambda f, v, vs: ~Q(**{f"{f}__in": vs}) if vs else None,
}


class GenericSpecificationSearch:
    @classmethod
    def build_search_filters(cls, search_data, model: type[Model] | None = None) -> Q:
        filters = Q()
        if not search_data or not getattr(search_data, "search_fields", None):
            return filters

        combine_and = search_data.search_combination_type != SearchCombinationType.OR.value

        valid_fields = None
        if model is not None:
            valid_fields = {f.name for f in model._meta.get_fields()}

        for field in search_data.search_fields:
            field_name = camel_to_snake(field.field_name)

            if valid_fields is not None and field_name.split("__", 1)[0] not in valid_fields:
                continue

            builder = _OPERATORS.get(field.search_type.value)
            if builder is None:
                continue

            q = builder(field_name, field.field_value, field.field_values)
            if q is None:  # operator needed values it didn't get
                continue

            filters = (filters & q) if combine_and else (filters | q)

        return filters
