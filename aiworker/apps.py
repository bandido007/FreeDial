from django.apps import AppConfig


class AiWorkerConfig(AppConfig):
    """Registered only so ``manage.py run_ai`` and ``ai_bakeoff`` exist. The worker has no models."""

    name = "aiworker"
    label = "aiworker"
    verbose_name = "AI agent worker"
