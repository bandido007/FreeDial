#!/usr/bin/env python
"""Write a ``.env`` for local development from ``.env.example``.

Fresh secrets are generated (SECRET_KEY, ENCRYPTION_KEYS, an administrator
password) so no two checkouts share a key, and the placeholders that must
never reach a real deployment are replaced. Never overwrites an existing
``.env`` — delete it first if you want a new one.

    python scripts/write_env.py            # → .env
    python scripts/write_env.py --print    # → stdout
"""

import argparse
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"
TARGET = ROOT / ".env"


def generated_values(project_name: str | None = None) -> dict[str, str]:
    from cryptography.fernet import Fernet

    values = {
        "SECRET_KEY": secrets.token_urlsafe(64),
        "ENCRYPTION_KEYS": Fernet.generate_key().decode(),
        "DEFAULT_ADMIN_PASSWORD": secrets.token_urlsafe(12) + "!A1",
    }
    if project_name:
        values["PROJECT_NAME"] = f'"{project_name}"'
        values["OAUTH_APP_NAME"] = f'"{project_name} Web"'
    return values


def render(example: str, values: dict[str, str]) -> str:
    """The example with ``KEY=…`` lines replaced for every key in ``values``."""
    lines = []
    for line in example.splitlines():
        match = re.match(r"^([A-Z_][A-Z0-9_]*)=", line)
        if match and match.group(1) in values:
            line = f"{match.group(1)}={values[match.group(1)]}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--print", action="store_true", help="write to stdout instead of .env")
    parser.add_argument("--project-name", help="also set PROJECT_NAME / OAUTH_APP_NAME")
    parser.add_argument("--force", action="store_true", help="overwrite an existing .env")
    args = parser.parse_args(argv)

    content = render(EXAMPLE.read_text(), generated_values(args.project_name))
    if args.print:
        sys.stdout.write(content)
        return 0
    if TARGET.exists() and not args.force:
        print(".env exists — leaving it alone (use --force to replace it)")
        return 0
    TARGET.write_text(content)
    print(f"Wrote {TARGET} with fresh SECRET_KEY, ENCRYPTION_KEYS and DEFAULT_ADMIN_PASSWORD.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
