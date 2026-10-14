from django.apps import AppConfig


class CallsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.calls"
    label = "calls"
    verbose_name = "Calls"
    # This app's status codes live in this band (checked by `manage.py check`).
    status_code_range = (10400, 10499)
