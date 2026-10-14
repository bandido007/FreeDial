from django.apps import AppConfig


class ConfigurationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.configuration"
    label = "configuration"
    verbose_name = "System settings"

    def ready(self):
        from apps.configuration import receivers, services
        from apps.core.hooks import set_policy_provider

        # `policy()` (apps.core) asks this app for the value an administrator saved.
        set_policy_provider(services.override_for)
        receivers.connect()
