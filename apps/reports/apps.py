from django.apps import AppConfig


class ReportsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reports"
    label = "reports"
    verbose_name = "Reports"
    # This app's status codes live in this band (checked by `manage.py check`).
    status_code_range = (10900, 10999)
