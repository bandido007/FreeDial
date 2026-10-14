"""Schema-drift guard: every GraphQL document the frontend sends must validate
against the current schema.

Skips (rather than fails) when no frontend checkout is beside the backend —
point ``FRONTEND_APP_DIR`` at the frontend's ``src/app`` to run it elsewhere
(CI checks both repositories out side by side).
"""

import textwrap
from pathlib import Path

import pytest
from graphql import build_schema

from apps.core.frontend_documents import (
    collect_documents,
    default_frontend_dir,
    validate_documents,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


@pytest.fixture
def toy_schema():
    return build_schema(
        """
        type Department { uid: ID! headUid: ID name: String }
        type Me { uid: ID! department: Department }
        type Query { me: Me }
        """
    )


def test_resolves_local_and_imported_fragments(tmp_path, toy_schema):
    app = tmp_path / "app"
    _write(
        app / "core/users/users.fragments.ts",
        """
        export const DEPARTMENT_FIELDS = `
          uid
          headUid
        `;
        """,
    )
    _write(
        app / "module/profile/graphql/me.queries.ts",
        """
        import { DEPARTMENT_FIELDS } from '@core/users/users.fragments';
        const ME_FIELDS = `uid department { ${DEPARTMENT_FIELDS} }`;
        export const ME_QUERY = `query Me { me { ${ME_FIELDS} } }`;
        """,
    )
    docs = collect_documents(app)
    assert [d.name for d in docs] == ["ME_QUERY"]
    assert "headUid" in docs[0].body and "${" not in docs[0].body
    assert validate_documents(toy_schema, docs) == []


def test_reports_drifted_selection(tmp_path, toy_schema):
    app = tmp_path / "app"
    _write(
        app / "module/profile/graphql/me.queries.ts",
        """
        export const ME_QUERY = `query Me { me { uid department { manager } } }`;
        """,
    )
    failures = validate_documents(toy_schema, collect_documents(app))
    assert len(failures) == 1
    assert "me.queries.ts::ME_QUERY" in failures[0]
    assert "manager" in failures[0]


def test_reports_unresolved_interpolation(tmp_path, toy_schema):
    app = tmp_path / "app"
    _write(app / "core/x/x.graphql.ts", "export const Q = `query Q { me { ${MISSING} } }`;\n")
    failures = validate_documents(toy_schema, collect_documents(app))
    assert failures == ["x.graphql.ts::Q: unresolved interpolation(s) MISSING"]


def test_frontend_documents_match_live_schema():
    """The real guard — the sibling frontend checkout against ``config.schema``."""
    app_dir = default_frontend_dir()
    if not app_dir.is_dir():
        pytest.skip(f"frontend checkout not found at {app_dir}")
    from config.schema import schema

    documents = collect_documents(app_dir)
    assert documents, f"no GraphQL documents found under {app_dir}"
    failures = validate_documents(schema.graphql_schema, documents)
    assert not failures, "Frontend documents drifted from the schema:\n  " + "\n  ".join(failures)
