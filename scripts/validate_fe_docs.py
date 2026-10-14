"""Validate the frontend's GraphQL documents against the backend schema.

Every ``*.graphql.ts`` / ``*.queries.ts`` / ``*.mutations.ts`` / ``*.fragments.ts``
file under the frontend's ``src/app`` is scanned. Logic lives in
:mod:`apps.core.frontend_documents`; the pytest guard
``apps/core/tests/test_frontend_documents.py`` runs the same check on every
backend test run when a frontend checkout is present.

Run via ``make validate-fe`` or directly:

    venv/bin/python scripts/validate_fe_docs.py [path/to/frontend/src/app]
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # runnable from anywhere
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.testing")
import django  # noqa: E402

django.setup()

from apps.core.frontend_documents import (  # noqa: E402
    collect_documents,
    default_frontend_dir,
    validate_documents,
)
from config.schema import schema  # noqa: E402


def main(argv: list[str]) -> int:
    app_dir = Path(argv[1]).resolve() if len(argv) > 1 else default_frontend_dir()
    if not app_dir.is_dir():
        # No frontend checkout beside this one: nothing to validate, not a failure
        # (the pytest guard skips the same way).
        print(f"No frontend checkout at {app_dir} — skipped. Set FRONTEND_APP_DIR to point at one.")
        return 0

    documents = collect_documents(app_dir)
    if not documents:
        print(f"No GraphQL documents found under {app_dir}")
        return 1

    failures = validate_documents(schema.graphql_schema, documents)
    by_file: dict[str, int] = {}
    for doc in documents:
        by_file[doc.file.name] = by_file.get(doc.file.name, 0) + 1
    for name, count in sorted(by_file.items()):
        print(f"{name}: {count} documents checked")
    for failure in failures:
        print(f"  INVALID {failure}")
    print(
        f"ALL {len(documents)} FE DOCUMENTS VALID"
        if not failures
        else f"{len(failures)} FAILURES across {len(documents)} documents"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
