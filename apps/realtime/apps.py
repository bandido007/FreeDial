from django.apps import AppConfig


class RealtimeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.realtime"
    label = "realtime"
    verbose_name = "Live"
    # This app's status codes live in this band (checked by `manage.py check`).
    status_code_range = (10500, 10599)
