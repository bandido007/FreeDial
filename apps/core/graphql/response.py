"""The standard GraphQL response envelope.

Every non-paginated query/mutation returns the same shape::

    {
        status: Boolean      # convenience flag (== code is a success code)
        code: Int            # business status code (see status_codes.json)
        message: String      # localised, human-readable message
        data: <Type>         # single object  (nullable)
        dataList: [<Type>]   # list of objects (nullable)
    }

Usage in a resolver/mutation::

    Output = Response.get_response_type(UserType)
    ...
    return Response.build_response(code=9001, info=info, data=user)

And as a query field::

    me_query = Response(LoggedInUserType, description="...")
"""

import graphene

from apps.core.graphql.status import get_status, resolve_message


def _type_name(of_type) -> str:
    # Derive the envelope's name from the *graphene* class, never the Django
    # model: two types over the same model (say a full ``InvoiceType`` and a
    # trimmed ``InvoiceSummaryType``) would otherwise mint two different
    # envelopes with the SAME schema name — and graphene silently keeps one,
    # swapping the other query's whole response tree for the wrong type.
    name = getattr(of_type, "__name__", "Unknown")
    return name[:-4] if name.endswith("Type") and len(name) > 4 else name


class Response(graphene.Field):
    """A graphene Field whose type is the standard envelope around ``of_type``."""

    # Envelope types are generated once per wrapped type and reused.
    _cache: dict = {}

    def __init__(self, of_type, *args, **kwargs):
        super().__init__(self.get_response_type(of_type), *args, **kwargs)

    @classmethod
    def get_response_type(cls, of_type):
        if of_type in cls._cache:
            return cls._cache[of_type]

        response_type = type(
            f"{_type_name(of_type)}Response",
            (graphene.ObjectType,),
            {
                "status": graphene.Boolean(),
                "code": graphene.Int(),
                "message": graphene.String(),
                "data": graphene.Field(of_type),
                "dataList": graphene.List(of_type),
            },
        )
        cls._cache[of_type] = response_type
        return response_type

    @classmethod
    def build_response(cls, code, info=None, data=None, data_list=None, custom_message=None) -> dict:
        status_info = get_status(code)
        return {
            "status": status_info["status"],
            "code": int(status_info["code"]),
            "message": resolve_message(code, info, custom_message),
            "data": data,
            "dataList": data_list if data_list is not None else [],
        }
