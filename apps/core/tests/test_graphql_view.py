"""The hardened GraphQL view: refusals are answered in the envelope."""

import json

import pytest
from django.test import Client

pytestmark = pytest.mark.django_db

ROLES = """
query Roles($searchDto: SearchDataDto!) {
  getAllRolesPageable(searchDto: $searchDto) { status code message }
}
"""
PAGE = {"searchDto": {"pageableParam": {"pageNumber": 1, "itemsPerPage": 5}}}


def post(query, variables=None, **extra):
    body = {"query": query, "variables": variables or {}}
    return Client().post("/graphql", data=json.dumps(body), content_type="application/json", **extra)


def test_variable_coercion_error_is_enveloped_not_400():
    broken = {"searchDto": {"pageableParam": {"pageNumber": "one", "itemsPerPage": 5}}}
    response = post(ROLES, broken)

    assert response.status_code == 200
    body = response.json()
    assert "errors" not in body
    envelope = body["data"]["getAllRolesPageable"]
    assert envelope["status"] is False
    assert envelope["code"] == 9002
    assert "pageNumber" in envelope["message"]


def test_well_formed_request_still_reaches_the_resolver():
    # Anonymous → the resolver's own 9000 envelope, proving the wrapper only
    # steps in for request-level errors.
    envelope = post(ROLES, PAGE).json()["data"]["getAllRolesPageable"]
    assert envelope["status"] is False
    assert envelope["code"] == 9000


def test_a_query_that_is_too_deep_is_refused(settings):
    settings.GRAPHQL_MAX_DEPTH = 2
    response = post("query { getAllPermissionGroups { dataList { permissions { code } } } }")
    envelope = response.json()["data"]["getAllPermissionGroups"]
    assert envelope["code"] == 9002
    assert "nested too deeply" in envelope["message"]


def test_a_query_that_selects_too_much_is_refused(settings):
    settings.GRAPHQL_MAX_COMPLEXITY = 3
    response = post("query { getAllPermissionGroups { status code message dataList { uid } } }")
    envelope = response.json()["data"]["getAllPermissionGroups"]
    assert envelope["code"] == 9002
    assert "too many fields" in envelope["message"]


def test_alias_flooding_is_refused(settings):
    settings.GRAPHQL_MAX_ALIASES = 3
    aliases = " ".join(f"a{i}: getRegistrationStatus {{ code }}" for i in range(5))
    response = post(f"query {{ {aliases} }}")
    body = response.json()
    assert next(iter(body["data"].values()))["code"] == 9002
    assert "aliases" in next(iter(body["data"].values()))["message"]


def test_a_malformed_document_says_what_is_wrong():
    response = post("query { unterminated ")
    assert response.status_code == 400
    assert "Syntax Error" in response.json()["errors"][0]["message"]


def test_introspection_is_off_when_the_ide_is_off(settings):
    settings.GRAPHIQL = False
    from django.urls import clear_url_caches

    clear_url_caches()
    response = post("query { __schema { types { name } } }")
    assert response.status_code == 200
    assert "Introspection is disabled" in json.dumps(response.json())


def test_only_json_posts_are_accepted_when_the_ide_is_off(settings):
    settings.GRAPHIQL = False
    assert Client().get("/graphql").status_code == 405
    assert Client().post("/graphql", data="query { a }", content_type="text/plain").status_code == 415


def test_batches_are_refused_unless_enabled():
    body = json.dumps([{"query": "query { getRegistrationStatus { code } }"}])
    response = Client().post("/graphql", data=body, content_type="application/json")
    assert response.status_code == 400


def test_the_api_is_also_served_under_the_mount_prefix():
    body = json.dumps({"query": ROLES, "variables": PAGE})
    response = Client().post("/backend/graphql", data=body, content_type="application/json")
    assert response.json()["data"]["getAllRolesPageable"]["code"] == 9000
