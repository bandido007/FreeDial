from django.apps import AppConfig


class SetupConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.setup"
    label = "setup"
    verbose_name = "Setup guide"
    # This app's status codes live in this band (checked by `manage.py check`).
    status_code_range = (10500, 10599)

    def ready(self):
        # Connect signal receivers here when the app has some:
        #     from apps.setup import receivers
        #     receivers.connect()
        pass
