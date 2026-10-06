"""
Run a management command as a scheduled Celery task.

Each task calls the same command cron users run, so both paths share one code path.
A Redis lock (config.task_lock) makes sure only one run of each job is active across
all containers. Errors propagate, so Celery logs them and Sentry reports them.
"""

import io
import logging

from django.core.management import call_command

from config.task_lock import task_lock

logger = logging.getLogger(__name__)


def run_locked_command(name: str, ttl: int, **options) -> str:
    """``skipped`` when another run holds the lock, else the command summary."""
    with task_lock(name, ttl=ttl) as acquired:
        if not acquired:
            logger.info('%s: skipped, another run is in progress', name)
            return 'skipped'
        out = io.StringIO()
        call_command(name, stdout=out, no_color=True, **options)
        summary = out.getvalue().strip()
        if summary:
            logger.info('%s: %s', name, summary)
        return summary
