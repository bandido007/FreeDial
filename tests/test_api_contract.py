"""Guards on the API as a whole: the schema snapshot, operation names,
permission codes and the module list. Each one fails the build with the exact
thing to fix, so conventions hold without anyone remembering them."""

import re
from importlib import import_module
from importlib.util import find_spec

import pytest
from django.apps import apps as django_apps
from django.core.management import CommandError, call_command

from apps.core.management.commands.export_schema import SCHEMA_PATH, render_schema
from config.modules import ALL_MODULES
from config.schema import MUTATION_BASES, QUERY_BASES, schema

DOCUMENTED_PERMISSIONS_RE = re.compile(r"permissions=\[([^\]]*)\]")


def test_the_schema_snapshot_is_current():
    assert SCHEMA_PATH.exists(), "run `make schema` and commit docs/schema.graphql"
    assert SCHEMA_PATH.read_text() == render_schema(), (
        "docs/schema.graphql differs from the live schema — run `make schema`, review the "
        "diff (it is the API change), and commit it."
    )


def test_the_snapshot_check_fails_when_stale(tmp_path, monkeypatch):
    from apps.core.management.commands import export_schema as command

    monkeypatch.setattr(command, "SCHEMA_PATH", tmp_path / "schema.graphql")
    with pytest.raises(CommandError):
        call_command("export_schema", "--check")
    call_command("export_schema")
    call_command("export_schema", "--check")


def _own_fields(cls) -> set[str]:
    """The GraphQL fields an app's Query/Mutation class declares itself."""
    return {name for name, value in vars(cls).items() if not name.startswith("_") and _is_field(value)}


def _is_field(value) -> bool:
    import graphene

    return (
        isinstance(value, graphene.types.mountedtype.MountedType | graphene.types.unmountedtype.UnmountedType)
        or (isinstance(value, type) and issubclass(value, graphene.Mutation))
        or hasattr(value, "Field")
    )


def test_operation_names_never_collide_across_apps():
    """Two apps declaring ``getItems`` would silently shadow one another."""
    for bases, kind in ((QUERY_BASES, "query"), (MUTATION_BASES, "mutation")):
        owners: dict[str, str] = {}
        for cls in bases:
            for name in _own_fields(cls):
                if name in owners:
                    pytest.fail(
                        f"{kind} '{name}' is declared by both {owners[name]} and {cls.__module__}; "
                        "rename one of them."
                    )
                owners[name] = cls.__module__


def test_every_operation_uses_the_envelope_or_a_scalar_list():
    """Business operations answer with the standard envelope so clients read
    ``status/code/message`` the same way everywhere."""
    for root in (schema.graphql_schema.query_type, schema.graphql_schema.mutation_type):
        for name, field in root.fields.items():
            named = _unwrap(field.type)
            assert {"status", "code", "message"} <= set(getattr(named, "fields", {}) or {}) or getattr(
                named, "name", ""
            ) in ("String", "Boolean", "Int"), f"'{name}' does not answer with the standard envelope"


def _unwrap(graphql_type):
    while hasattr(graphql_type, "of_type"):
        graphql_type = graphql_type.of_type
    return graphql_type


def test_every_permission_a_resolver_requires_is_declared():
    """A code required in ``@has_permission`` but declared in no ``permissions.py``
    can never be granted — the operation would be dead."""
    from apps.rbac import catalogue

    declared = catalogue.collect().codes()
    required: dict[str, set[str]] = {}
    for cls in (*QUERY_BASES, *MUTATION_BASES):
        for name, value in vars(cls).items():
            target = value.mutate if isinstance(value, type) and hasattr(value, "mutate") else value
            for code in getattr(target, "required_permissions", ()) or ():
                required.setdefault(code, set()).add(f"{cls.__module__}.{name}")
    undeclared = {code: where for code, where in required.items() if code not in declared}
    assert not undeclared, f"permissions required but declared nowhere: {undeclared}"


def test_every_documented_permission_is_declared():
    """The ``permissions=[...]`` note in an operation's description is what the
    API reference and the frontend read; it must name real codes."""
    from apps.rbac import catalogue

    declared = catalogue.collect().codes()
    wrong = {}
    for root in (schema.graphql_schema.query_type, schema.graphql_schema.mutation_type):
        for name, field in root.fields.items():
            for match in DOCUMENTED_PERMISSIONS_RE.finditer(field.description or ""):
                codes = [code.strip().strip("'\"") for code in match.group(1).split(",") if code.strip()]
                unknown = [code for code in codes if code not in declared]
                if unknown:
                    wrong[name] = unknown
    assert not wrong, f"operations documenting unknown permission codes: {wrong}"


def test_every_declared_permission_is_used_somewhere():
    """A declared code no resolver requires is either dead or a policy read by
    hand (``resolve_permissions``); both must show up in a grep of the code."""
    from pathlib import Path

    from django.conf import settings

    from apps.rbac import catalogue

    apps_dir = Path(settings.BASE_DIR) / "apps"
    declarations, source = [], []
    for path in apps_dir.rglob("*.py"):
        if "tests" in path.parts:
            continue
        (declarations if path.name == "permissions.py" else source).append(path.read_text())
    code_text = "\n".join(source)
    # A code is usually referenced through the constant permissions.py gives it.
    constant_re = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=\s*['\"]([a-z0-9_]+)['\"]", re.MULTILINE)
    names_of: dict[str, set[str]] = {}
    for text in declarations:
        for constant, code in constant_re.findall(text):
            names_of.setdefault(code, set()).add(constant)
    unused = sorted(
        code
        for code in catalogue.collect().codes()
        if code not in code_text
        and not any(re.search(rf"\b{name}\b", code_text) for name in names_of.get(code, ()))
    )
    assert not unused, f"declared permissions nothing checks: {unused}"


def test_modules_are_installed_apps_or_deliberately_optional():
    from config.modules import FOUNDATION_MODULES, OPTIONAL_MODULES

    for label in FOUNDATION_MODULES:
        assert django_apps.is_installed(label), f"{label} is a foundation module but is not installed"
    assert set(OPTIONAL_MODULES) <= {"apps.workflows", "apps.sso", "apps.example"}
    for label in ALL_MODULES:
        if django_apps.is_installed(label):
            for suffix in ("queries", "mutations"):
                if find_spec(f"{label}.{suffix}") is not None:
                    import_module(f"{label}.{suffix}")


def test_status_codes_are_unique_and_in_their_band():
    from apps.core.checks import check_status_codes

    assert check_status_codes(None) == []


def test_the_permission_catalogue_is_sound():
    from apps.rbac import catalogue

    assert catalogue.problems() == []
