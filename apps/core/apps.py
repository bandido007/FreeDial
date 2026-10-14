from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"
    verbose_name = "Core"

    def ready(self):
        # Registers the system checks (status-code catalogue, module wiring).
        from apps.core import checks  # noqa: F401
