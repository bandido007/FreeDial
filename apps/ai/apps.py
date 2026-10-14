from django.apps import AppConfig


class AiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ai"
    label = "ai"
    verbose_name = "AI agents"
    # This app's status codes live in this band (checked by `manage.py check`).
    status_code_range = (10800, 10899)

    def ready(self):
        from apps.ai import receivers

        receivers.connect()
