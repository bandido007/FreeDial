from django.apps import AppConfig


class AgentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.agents"
    label = "agents"
    verbose_name = "Agents"
    # This app's status codes live in this band (checked by `manage.py check`).
    status_code_range = (10200, 10299)

    def ready(self):
        from apps.agents import receivers

        receivers.connect()
