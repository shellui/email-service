"""Default Django test runner."""

from django.test.runner import DiscoverRunner


class IsolatedDiscoverRunner(DiscoverRunner):
    """Placeholder runner so tests stay on the local SQLite database."""
