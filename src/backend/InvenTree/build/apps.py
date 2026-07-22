"""Django app for the BuildOrder module."""

from django.apps import AppConfig


class BuildConfig(AppConfig):
    """BuildOrder app config class."""

    name = 'build'

    def ready(self):
        """Apply BlueOasis build patches: propagate the USED stock status to build outputs."""
        import build.used_status_patch  # noqa: F401
