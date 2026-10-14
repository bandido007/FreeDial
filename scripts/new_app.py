#!/usr/bin/env python
"""Create a business app from ``scripts/app_template`` and wire it in.

    make app name=invoices                 # model "Invoice", codes in the next free band
    python scripts/new_app.py invoices --model Bill --verbose "Billing"

What it does:

1. Renders every ``*.tmpl`` under ``scripts/app_template`` into ``apps/<name>/``
   (a CRUD app with the project's layering: models → selectors/services →
   GraphQL types/inputs/queries/mutations, permissions, status codes, tests).
2. Picks a status-code band: the next free block of 100 at or above 10000.
3. Inserts the app at the four markers the starter keeps for it:
   ``INSTALLED_APPS`` (config/settings/base.py), ``PROJECT_MODULES``
   (config/modules.py) and the two import-linter lists in pyproject.toml.

Then: ``make migrations app=<name> && make migrate && make seed && make schema``.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "scripts" / "app_template"
APPS = ROOT / "apps"
PROJECT_CODE_FLOOR = 10000
BAND = 100

MARKERS = {
    ROOT / "config" / "settings" / "base.py": ("    # <scaffold:project-apps>", '    "apps.{name}",'),
    ROOT / "config" / "modules.py": ("    # <scaffold:project-modules>", '    "apps.{name}",'),
    ROOT / "pyproject.toml": ("    # <scaffold:project-layers>", '    "apps.{name}",  # project'),
}
FORBIDDEN_MARKER = ("    # <scaffold:project-forbidden>", '    "apps.{name}",  # project')


def snake_to_pascal(value: str) -> str:
    return "".join(part.capitalize() for part in value.split("_"))


def singular(value: str) -> str:
    if value.endswith("ies"):
        return value[:-3] + "y"
    if value.endswith("ses") or value.endswith("xes"):
        return value[:-2]
    if value.endswith("s") and not value.endswith("ss"):
        return value[:-1]
    return value


def next_code_band() -> int:
    """The lowest 100-block at or above 10000 no app has a code in."""
    taken = set()
    for path in APPS.glob("*/status_codes.json"):
        try:
            for entry in json.loads(path.read_text()):
                code = entry.get("code")
                if isinstance(code, int) and code >= PROJECT_CODE_FLOOR:
                    taken.add(code // BAND)
        except (ValueError, AttributeError):
            continue
    band = PROJECT_CODE_FLOOR // BAND
    while band in taken:
        band += 1
    return band * BAND


def context(name: str, model: str, verbose: str) -> dict[str, str]:
    model_snake = re.sub(r"(?<!^)(?=[A-Z])", "_", model).lower()
    model_verbose = model_snake.replace("_", " ")
    base = next_code_band()
    return {
        "name": name,
        "ClassName": snake_to_pascal(name),
        "Verbose name": verbose,
        "Model": model,
        "MODEL_UPPER": model_snake.upper(),
        "model_snake": model_snake,
        "model_verbose": model_verbose,
        "model_verbose_cap": model_verbose.capitalize(),
        "article": "an" if model_verbose[0] in "aeiou" else "a",
        "Article": "An" if model_verbose[0] in "aeiou" else "A",
        "view_code": f"can_view_{model_snake}s",
        "manage_code": f"can_manage_{model_snake}s",
        "code_base": str(base),
        "code_base_1": str(base + 1),
        "code_end": str(base + BAND - 1),
    }


def render(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    leftover = re.findall(r"{{[^}]+}}", text)
    if leftover:
        raise SystemExit(f"template placeholder(s) without a value: {sorted(set(leftover))}")
    return text


def write_app(values: dict[str, str]) -> Path:
    target = APPS / values["name"]
    if target.exists():
        raise SystemExit(f"apps/{values['name']} already exists")
    for source in sorted(TEMPLATE.rglob("*.tmpl")):
        relative = source.relative_to(TEMPLATE)
        destination = target / render(str(relative)[: -len(".tmpl")], values)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(render(source.read_text(), values))
    return target


def format_with_ruff(target: Path) -> None:
    """Format the generated files the way the pre-commit hook would."""
    import shutil
    import subprocess

    ruff = shutil.which("ruff") or str(ROOT / "venv" / "bin" / "ruff")
    if not Path(ruff).exists():
        return
    subprocess.run([ruff, "format", "-q", str(target)], check=False)  # noqa: S603
    subprocess.run([ruff, "check", "-q", "--fix", str(target)], check=False)  # noqa: S603


def insert_at_marker(path: Path, marker: str, line: str) -> None:
    text = path.read_text()
    if line in text:
        return
    if marker not in text:
        raise SystemExit(f"marker {marker.strip()!r} is missing from {path.relative_to(ROOT)}")
    path.write_text(text.replace(marker, f"{line}\n{marker}", 1))


def wire(name: str) -> None:
    for path, (marker, template) in MARKERS.items():
        insert_at_marker(path, marker, template.format(name=name))
    marker, template = FORBIDDEN_MARKER
    insert_at_marker(ROOT / "pyproject.toml", marker, template.format(name=name))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", help="snake_case app name, plural (invoices, work_orders)")
    parser.add_argument("--model", help="PascalCase name of the first model (default: singular of name)")
    parser.add_argument("--verbose", help="human name of the app (default: from name)")
    parser.add_argument("--no-wire", action="store_true", help="only write the files")
    args = parser.parse_args(argv)

    name = args.name.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise SystemExit("the app name must be snake_case: letters, digits, underscores")
    if name in {
        "core",
        "users",
        "rbac",
        "authentication",
        "notifications",
        "audit",
        "attachments",
        "configuration",
        "workflows",
        "sso",
        "example",
        "tests",
        "config",
    }:
        raise SystemExit(f"'{name}' is reserved")
    model = args.model or snake_to_pascal(singular(name))
    verbose = args.verbose or name.replace("_", " ").capitalize()

    values = context(name, model, verbose)
    target = write_app(values)
    format_with_ruff(target)
    if not args.no_wire:
        wire(name)

    print(f"Created apps/{name}/ (model {model}, status codes {values['code_base']}–{values['code_end']}).")
    print("Next:")
    print(f"  make migrations app={name} && make migrate")
    print("  make seed          # writes the app's permissions")
    print("  make schema        # refreshes docs/schema.graphql")
    print("  make test")
    print(f"Then replace the {model} model with your real one — the files show where everything goes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
