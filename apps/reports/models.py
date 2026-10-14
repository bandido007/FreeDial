"""Reports have no table of their own: they are read from the Event log. ``Report`` (a proxy of
:class:`apps.calls.models.Event`) only gives them their place in the admin."""

from apps.calls.models import Event


class Report(Event):
    class Meta:
        proxy = True
        verbose_name = "report"
        verbose_name_plural = "Reports"
