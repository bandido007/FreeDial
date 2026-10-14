"""Asterisk's realtime tables, made by Asterisk's own Alembic scripts.

The scripts live in the Asterisk source tree (``contrib/ast-db-manage``), so the
schema always matches the Asterisk that reads it. They run as Asterisk's README
shows: ``alembic -c <ini> upgrade head`` from inside ``ast-db-manage``, because
its ``env.py`` names the version table after the relative ``script_location``
(``alembic_version_config``).
"""

import os
import subprocess  # noqa: S404 — runs alembic with fixed arguments
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.db import connection

TREE = "config"  # the realtime configuration tree; cdr, queue_log and voicemail are others


class AsteriskSchemaError(Exception):
    pass


def ast_db_manage_dir() -> Path:
    path = Path(settings.ASTERISK_SRC_DIR) / "contrib" / "ast-db-manage"
    if not (path / TREE / "versions").is_dir():
        raise AsteriskSchemaError(f"Asterisk's Alembic scripts not found in {path} (set ASTERISK_SRC_DIR)")
    return path


def _url(db: dict) -> str:
    user, password = quote(db.get("USER") or "", safe=""), quote(db.get("PASSWORD") or "", safe="")
    host, port = db.get("HOST") or "127.0.0.1", db.get("PORT") or 5432
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db['NAME']}"


def _alembic(db: dict, *args: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        ini = Path(tmp) / "alembic.ini"
        # The URL carries the password: the file is readable by us only and removed at once.
        ini.touch(mode=0o600)
        ini.write_text(
            f"[alembic]\nscript_location = {TREE}\nsqlalchemy.url = {_url(db).replace('%', '%%')}\n"
        )
        done = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "alembic", "-c", str(ini), *args],
            cwd=ast_db_manage_dir(),
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONWARNINGS": "ignore"},
            check=False,
        )
    if done.returncode != 0:
        raise AsteriskSchemaError(f"alembic {' '.join(args)} failed:\n{done.stderr.strip()}")
    return done.stdout.strip()


def upgrade(db: dict | None = None) -> None:
    """Create or bring up to date the realtime tables in ``db`` (default: the app's database)."""
    _alembic(db or connection.settings_dict, "upgrade", "head")


def current_revision(db: dict | None = None) -> str:
    out = _alembic(db or connection.settings_dict, "current")
    return out.split()[0] if out else ""


def head_revision() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(ast_db_manage_dir() / TREE))
    return ScriptDirectory.from_config(config).get_current_head() or ""


ASTERISK_TABLES = ("ps_endpoints", "ps_auths", "ps_aors")


def grant_asterisk_role(*, user: str, password: str) -> None:
    """Create (or update) the role Asterisk logs in with: it may only read the PJSIP tables."""
    from psycopg import sql

    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [user])
        verb = "ALTER" if cursor.fetchone() else "CREATE"
        cursor.execute(
            sql.SQL(verb + " ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(user), sql.Literal(password))
        )
        cursor.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(connection.settings_dict["NAME"]), sql.Identifier(user)
            )
        )
        cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(user)))
        for table in ASTERISK_TABLES:
            cursor.execute(
                sql.SQL("GRANT SELECT ON {} TO {}").format(sql.Identifier(table), sql.Identifier(user))
            )
