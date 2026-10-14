from django.apps import AppConfig


class SupervisorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.supervisor"
    label = "supervisor"
    verbose_name = "Supervisor page"
    # This app's status codes live in this band (checked by `manage.py check`).
    status_code_range = (10700, 10799)
