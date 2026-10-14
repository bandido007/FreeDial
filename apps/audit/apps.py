from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.audit"
    label = "audit"
    verbose_name = "Audit & system logs"

    def ready(self):
        # Import inside ready() so app loading stays side-effect free.
        from apps.audit import context, signals

        signals.connect_signals()
        context.connect()
