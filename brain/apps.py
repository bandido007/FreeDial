from django.apps import AppConfig


class BrainConfig(AppConfig):
    """Registered only so ``manage.py run_brain`` exists. The brain has no models."""

    name = "brain"
    label = "brain"
    verbose_name = "Call control (brain)"
