from django.apps import AppConfig


class RbacConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.rbac"
    label = "rbac"
    verbose_name = "Roles and permissions"

    def ready(self):
        from apps.core.hooks import set_permission_resolver
        from apps.rbac import receivers
        from apps.rbac.selectors import get_user_permission_codes

        # `@has_permission` (apps.core) asks this app what a user may do.
        set_permission_resolver(get_user_permission_codes)
        receivers.connect()
