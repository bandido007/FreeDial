from django.apps import AppConfig


class TeamsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.teams"
    label = "teams"
    verbose_name = "Teams"
    # This app's status codes live in this band (checked by `manage.py check`).
    status_code_range = (10100, 10199)

    def ready(self):
        # Connect signal receivers here when the app has some:
        #     from apps.teams import receivers
        #     receivers.connect()
        pass
