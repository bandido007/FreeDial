"""Per-app discovery.

The starter's apps *register themselves*: an app declares what it contributes
in a conventionally named file inside its own folder, and the foundation finds
it. Adding a business app therefore never edits a foundation file.

===========================  ==========================================
File in ``apps/<app>/``      What it declares
===========================  ==========================================
``status_codes.json``        response codes + translated messages
``permissions.py``           permission catalogue + default role grants
``notifications.py``         notification categories, templates, routes
``schedule.py``              Celery beat entries
``settings_catalogue.py``    runtime system settings
===========================  ==========================================
"""

from collections.abc import Iterator
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path

from django.apps import AppConfig, apps


def app_files(filename: str) -> Iterator[tuple[AppConfig, Path]]:
    """``(app_config, path)`` for every installed app that ships ``filename``."""
    for app_config in apps.get_app_configs():
        path = Path(app_config.path) / filename
        if path.is_file():
            yield app_config, path


def app_modules(module_name: str) -> Iterator[tuple[AppConfig, object]]:
    """``(app_config, module)`` for every installed app that has ``<app>.<module_name>``."""
    for app_config in apps.get_app_configs():
        dotted = f"{app_config.name}.{module_name}"
        try:
            if find_spec(dotted) is None:
                continue
        except (ImportError, ValueError):
            continue
        yield app_config, import_module(dotted)


def is_project_app(app_config: AppConfig) -> bool:
    """True for an app that lives under ``apps/`` (foundation, optional or business)."""
    return app_config.name.startswith("apps.")
