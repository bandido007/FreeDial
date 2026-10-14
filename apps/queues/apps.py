from django.apps import AppConfig


class QueuesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.queues"
    label = "queues"
    verbose_name = "Queues"
    # This app's status codes live in this band (checked by `manage.py check`).
    status_code_range = (10300, 10399)

    def ready(self):
        from apps.queues import receivers

        receivers.connect()
