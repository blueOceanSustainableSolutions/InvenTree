"""Django app for the Fleet module."""

from django.apps import AppConfig


class FleetConfig(AppConfig):
    """Fleet app config class."""

    name = 'fleet'

    def ready(self):
        """Connect the fleet signal receivers."""
        import fleet.signals  # noqa: F401
