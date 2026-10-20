"""Validate the Angular client's GraphQL documents against the live schema.

The frontend spells every operation as a backtick template string in
``*.graphql.ts`` / ``*.queries.ts`` / ``*.mutations.ts`` / ``*.fragments.ts``
files, composing shared selections with ``${CONST}`` interpolation (often
imported from a sibling file). A document that drifts from the schema — a
renamed field, a scalar selected as if it were an object — fails GraphQL
validation at runtime, and a client that unwraps the envelope sees only a
silent ``{status: false}``.

This module makes that drift a test failure instead: it extracts every
document, resolves interpolations (local consts first, then the file's
imports, then any same-named const anywhere as a last resort) and runs
``graphql.validate`` against ``config.schema``. It is pure Python with no
Django side effects at import time, so both the pytest guard
(``apps/core/tests/test_frontend_documents.py``) and the CLI
(``scripts/validate_fe_docs.py``) share it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from graphql import GraphQLSchema, parse, validate

DOC_SUFFIXES = (".graphql.ts", ".queries.ts", ".mutations.ts", ".fragments.ts")
ENV_FRONTEND_DIR = "FRONTEND_APP_DIR"

_CONST_RE = re.compile(r"(?:export\s+)?const\s+(\w+)\s*(?::\s*string)?\s*=\s*(?:gql\s*)?`([^`]*)`", re.S)
_IMPORT_RE = re.compile(r"import\s*\{([^}]*)\}\s*from\s*['\"]([^'\"]+)['\"]")
_INTERP_RE = re.compile(r"\$\{\s*(\w+)\s*\}")
_MAX_RESOLVE_PASSES = 8


@dataclass(frozen=True)
class FrontendDocument:
    """One GraphQL operation as the client sends it (interpolations resolved)."""

    file: Path
    name: str
    body: str

    @property
    def label(self) -> str:
        return f"{self.file.name}::{self.name}"


def default_frontend_dir() -> Path:
    """``src/app`` of the frontend checkout.

    :data:`ENV_FRONTEND_DIR` names it explicitly. Without it, the repository's
    ``frontend/src/app`` is preferred. Then the sibling
    folder ``<backend-folder-name with "backend" → "frontend">`` is tried, then
    a plain ``frontend`` sibling (CI checks the repos out side by side).
    """
    override = os.environ.get(ENV_FRONTEND_DIR)
    if override:
        return Path(override).expanduser().resolve()
    backend_root = Path(__file__).resolve().parents[2]
    local = backend_root / "frontend" / "src" / "app"
    if local.is_dir():
        return local
    sibling = backend_root.name.replace("backend", "frontend")
    for name in (sibling, "frontend"):
        candidate = backend_root.parent / name / "src" / "app"
        if name != backend_root.name and candidate.is_dir():
            return candidate
    return backend_root.parent / "frontend" / "src" / "app"


def _alias_roots(app_dir: Path) -> dict[str, Path]:
    """The tsconfig path aliases the client uses in imports."""
    return {"@core": app_dir / "core", "@shared": app_dir / "shared", "src/app": app_dir}


def _resolve_import(spec: str, importing_file: Path, app_dir: Path) -> Path | None:
    """Map an import specifier to the ``.ts`` file it names (relative or aliased)."""
    base: Path | None = None
    if spec.startswith("."):
        base = (importing_file.parent / spec).resolve()
    else:
        for alias, root in _alias_roots(app_dir).items():
            if spec == alias or spec.startswith(alias + "/"):
                base = (root / spec[len(alias) :].lstrip("/")).resolve()
                break
    if base is None:
        return None
    for candidate in (base.with_suffix(".ts") if base.suffix != ".ts" else base, base / "index.ts"):
        if candidate.is_file():
            return candidate
    return None


def _consts_in(text: str) -> dict[str, str]:
    return {name: body for name, body in _CONST_RE.findall(text)}


def _resolve(body: str, lookup: list[dict[str, str]]) -> str:
    """Substitute ``${NAME}`` from the first table that knows ``NAME``; iterate so
    fragments that contain fragments settle. Unknown names are left in place (and
    reported as a failure by :func:`validate_documents`)."""

    def replacement(match: re.Match) -> str:
        name = match.group(1)
        for table in lookup:
            if name in table:
                return table[name]
        return match.group(0)

    for _ in range(_MAX_RESOLVE_PASSES):
        resolved = _INTERP_RE.sub(replacement, body)
        if resolved == body:
            break
        body = resolved
    return body


def _is_operation(body: str) -> bool:
    return body.lstrip().startswith(("query", "mutation", "subscription"))


def collect_documents(app_dir: Path | None = None) -> list[FrontendDocument]:
    """Every operation document under ``app_dir`` with its interpolations resolved."""
    app_dir = (app_dir or default_frontend_dir()).resolve()
    files = sorted(p for p in app_dir.rglob("*.ts") if p.name.endswith(DOC_SUFFIXES))
    texts = {path: path.read_text(encoding="utf-8") for path in files}
    consts_by_file = {path: _consts_in(text) for path, text in texts.items()}

    global_consts: dict[str, str] = {}
    for consts in consts_by_file.values():
        for name, body in consts.items():
            global_consts.setdefault(name, body)

    documents: list[FrontendDocument] = []
    for path, text in texts.items():
        imported: dict[str, str] = {}
        for names, spec in _IMPORT_RE.findall(text):
            target = _resolve_import(spec, path, app_dir)
            if target is None:
                continue
            source = consts_by_file.get(target)
            if source is None:
                source = _consts_in(target.read_text(encoding="utf-8"))
            for raw in names.split(","):
                name = raw.strip().split(" as ")[0].strip()
                if name in source:
                    imported[name] = source[name]
        lookup = [consts_by_file[path], imported, global_consts]
        for name, body in consts_by_file[path].items():
            resolved = _resolve(body, lookup)
            if _is_operation(resolved):
                documents.append(FrontendDocument(file=path, name=name, body=resolved))
    return documents


def validate_documents(schema: GraphQLSchema, documents: list[FrontendDocument]) -> list[str]:
    """Human-readable failures (empty when every document is valid)."""
    failures: list[str] = []
    for doc in documents:
        unresolved = sorted(set(_INTERP_RE.findall(doc.body)))
        if unresolved:
            failures.append(f"{doc.label}: unresolved interpolation(s) {', '.join(unresolved)}")
            continue
        try:
            errors = validate(schema, parse(doc.body))
        except Exception as exc:  # noqa: BLE001 — a parse failure is a finding, not a crash
            failures.append(f"{doc.label}: parse failed — {exc}")
            continue
        failures.extend(f"{doc.label}: {error.message}" for error in errors)
    return failures
