from django.apps import AppConfig


class AgentdeskConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.agentdesk"
    label = "agentdesk"
    verbose_name = "Agent page"
    # This app's status codes live in this band (checked by `manage.py check`).
    status_code_range = (10600, 10699)
