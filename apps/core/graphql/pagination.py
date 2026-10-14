"""Pageable GraphQL responses + the input DTOs that drive them.

Input DTOs (sent by the client):
    * :class:`PageableParam`  — page number, size, sort field & direction.
    * :class:`SearchFieldsDto` — one search clause (field + operator + value(s)).
    * :class:`SearchDataDto`   — pageable params + a list of search clauses.

Output:
    * :class:`Page` — a graphene Field that wraps a list type in
      ``{status, code, message, content, page}``.
    * :class:`PageObject` — pagination metadata (counts, navigation flags).
"""

import graphene
from django.conf import settings
from django.core.paginator import EmptyPage, Paginator

from apps.core.enums import SearchCombinationTypeEnum, SearchOperationTypeEnum
from apps.core.graphql.status import get_status, resolve_message
from apps.core.utils import camel_to_snake


# ── Input DTOs ─────────────────────────────────────────────────────────────
class PageableParam(graphene.InputObjectType):
    page_number = graphene.Int(required=True, description="1-based page number")
    items_per_page = graphene.Int(required=True, description="Page size")
    sort_by = graphene.String(default_value="id", description="Field to sort by")
    sort_direction = graphene.String(default_value="DESC", description="ASC or DESC")


class SearchFieldsDto(graphene.InputObjectType):
    field_name = graphene.String(required=True)
    search_type = graphene.Field(SearchOperationTypeEnum, required=True)
    field_value = graphene.String()
    field_values = graphene.List(graphene.String)


class SearchDataDto(graphene.InputObjectType):
    pageable_param = graphene.Field(PageableParam, required=True)
    search_combination_type = SearchCombinationTypeEnum()
    search_fields = graphene.List(SearchFieldsDto)


# ── Output ───────────────────────────────────────────────────────────────
class PageObject(graphene.ObjectType):
    number = graphene.Int()
    has_next_page = graphene.Boolean()
    has_previous_page = graphene.Boolean()
    current_page_number = graphene.Int()
    next_page_number = graphene.Int()
    previous_page_number = graphene.Int()
    number_of_pages = graphene.Int()
    items_per_page = graphene.Int()
    total_elements = graphene.Int()
    pages_number_array = graphene.List(graphene.Int)

    @classmethod
    def build(cls, paginator: Paginator, page, page_number: int, items_per_page: int):
        return cls(
            number=page.number,
            has_next_page=page.has_next(),
            has_previous_page=page.has_previous(),
            current_page_number=page_number,
            next_page_number=page.next_page_number() if page.has_next() else None,
            previous_page_number=page.previous_page_number() if page.has_previous() else None,
            number_of_pages=paginator.num_pages,
            items_per_page=items_per_page,
            total_elements=paginator.count,
            pages_number_array=list(range(1, paginator.num_pages + 1)),
        )


DEFAULT_PAGE_SIZE = 10


def _type_name(of_type) -> str:
    model = getattr(getattr(of_type, "_meta", None), "model", None)
    if model is not None:
        return model.__name__
    return getattr(of_type, "__name__", "Unknown")


def clamp_page_size(requested) -> int:
    """The page size actually served: at least 1, at most ``GRAPHQL_MAX_PAGE_SIZE``.

    The client chooses ``itemsPerPage``; without a ceiling a single request can
    ask for the whole table.
    """
    ceiling = int(getattr(settings, "GRAPHQL_MAX_PAGE_SIZE", 500) or 500)
    try:
        size = int(requested or DEFAULT_PAGE_SIZE)
    except (TypeError, ValueError):
        size = DEFAULT_PAGE_SIZE
    return max(1, min(size, ceiling))


class Page(graphene.Field):
    """A graphene Field that returns a paginated envelope around ``of_type``."""

    _cache: dict = {}

    def __init__(self, of_type, *args, **kwargs):
        if of_type in self._cache:
            page_type = self._cache[of_type]
        else:
            page_type = type(
                f"{_type_name(of_type)}PageResponse",
                (graphene.ObjectType,),
                {
                    "status": graphene.Boolean(),
                    "code": graphene.Int(),
                    "message": graphene.String(),
                    "content": graphene.List(of_type),
                    "page": graphene.Field(PageObject),
                },
            )
            self._cache[of_type] = page_type
        super().__init__(page_type, *args, **kwargs)

    @classmethod
    def build_response(cls, code, info=None, pageable=None, data=None, custom_message=None) -> dict:
        status_info = get_status(code)
        content, page_meta = [], None

        if data is not None and pageable is not None:
            sort_by = camel_to_snake(pageable.sort_by or "id")
            direction = (pageable.sort_direction or "DESC").upper()
            order_field = f"-{sort_by}" if direction == "DESC" else sort_by
            data = data.order_by(order_field)

            items_per_page = clamp_page_size(pageable.items_per_page)
            page_number = max(1, pageable.page_number or 1)
            paginator = Paginator(data, items_per_page)

            try:
                page = paginator.page(page_number)
            except EmptyPage:
                page = paginator.page(paginator.num_pages)

            content = list(page.object_list)
            page_meta = PageObject.build(paginator, page, page.number, items_per_page)

        return {
            "status": status_info["status"],
            "code": int(status_info["code"]),
            "message": resolve_message(code, info, custom_message),
            "content": content,
            "page": page_meta,
        }
