"""System checks for the starter's conventions.

Run by ``manage.py check`` (and therefore by ``runserver``, ``migrate`` and
CI). Each check turns a convention that would otherwise fail silently at
runtime into an error at start-up.

==========  ================================================================
ID          Meaning
==========  ================================================================
starter.E001  Two apps declare the same status code.
starter.E002  A status code has no message in a configured API language.
starter.E003  A business app uses a status code below 10000.
starter.E004  A status-code entry is malformed.
starter.E005  A status code falls outside the range its app declared.
==========  ================================================================
"""

from django.apps import apps
from django.core.checks import Error, register

from apps.core.graphql import status as status_catalogue

PROJECT_CODE_FLOOR = 10000
FOUNDATION_PREFIX = "apps."


def _foundation_labels() -> set[str]:
    """Labels of the apps the starter ships (they own the numbers below 10000)."""
    from config.modules import FOUNDATION_MODULES, OPTIONAL_MODULES

    shipped = {"apps.core", *FOUNDATION_MODULES, *OPTIONAL_MODULES}
    return {config.label for config in apps.get_app_configs() if config.name in shipped}


@register("starter")
def check_status_codes(app_configs, **kwargs):
    errors = []
    seen: dict[int, str] = {}
    foundation = _foundation_labels()
    required_keys = [status_catalogue.message_key(lang) for lang in status_catalogue.languages()]

    for label, entry in status_catalogue.load_catalogue():
        code = entry.get("code")
        if not isinstance(code, int) or not isinstance(entry.get("status"), bool):
            errors.append(
                Error(
                    f"Malformed status-code entry in '{label}': {entry!r}",
                    hint='Each entry needs an integer "code" and a boolean "status".',
                    id="starter.E004",
                )
            )
            continue

        if code in seen:
            errors.append(
                Error(
                    f"Status code {code} is declared by both '{seen[code]}' and '{label}'.",
                    hint="Pick an unused number in your app's range.",
                    id="starter.E001",
                )
            )
        else:
            seen[code] = label

        missing = [key for key in required_keys if not (entry.get(key) or "").strip()]
        if missing:
            errors.append(
                Error(
                    f"Status code {code} ('{label}') has no {', '.join(missing)}.",
                    hint="Add the translation, or remove the language from API_LANGUAGES.",
                    id="starter.E002",
                )
            )

        if label not in foundation and code < PROJECT_CODE_FLOOR:
            errors.append(
                Error(
                    f"Status code {code} ('{label}') is in the foundation range.",
                    hint=f"Business apps use codes from {PROJECT_CODE_FLOOR} upwards.",
                    id="starter.E003",
                )
            )

        declared = getattr(apps.get_app_config(label), "status_code_range", None)
        if declared and not (declared[0] <= code <= declared[1]):
            errors.append(
                Error(
                    f"Status code {code} is outside the range {declared} declared by '{label}'.",
                    id="starter.E005",
                )
            )

    return errors
