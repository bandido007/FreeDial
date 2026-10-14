from django.apps import AppConfig


class TelephonyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.telephony"
    label = "telephony"
    verbose_name = "Telephony"
    # This app's status codes live in this band (checked by `manage.py check`).
    status_code_range = (10000, 10099)

    def ready(self):
        from apps.telephony import receivers

        receivers.connect()
