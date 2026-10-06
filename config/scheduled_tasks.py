"""
Run a management command as a scheduled Celery task.

Each task calls the same command cron users run, so both paths share one code path: the
command records the run (``trigger=celery`` here, ``command`` from cron). A Redis lock
(config.task_lock) makes sure only one run of each job is active across all containers.
See docs/scheduled-jobs.md#monitoring.
"""

import io
import logging

from django.core.management import call_command

from config.task_lock import task_lock

logger = logging.getLogger(__name__)


def run_locked_command(name: str, ttl: int, **options) -> str:
    """
    ``skipped`` when another run holds the lock, ``failed`` when the run failed (already
    recorded, logged at ERROR and sent to Sentry by the command), else the command summary.
    """
    from apps.actions.scheduled_jobs import (
        TRIGGER_CELERY,
        ScheduledJobFailed,
        record_failure_before_start,
        record_skipped,
    )

    try:
        with task_lock(name, ttl=ttl) as acquired:
            if not acquired:
                logger.info('%s: skipped, another run is in progress', name)
                record_skipped(name, TRIGGER_CELERY)
                return 'skipped'
            out = io.StringIO()
            call_command(name, stdout=out, no_color=True, trigger=TRIGGER_CELERY, **options)
            summary = out.getvalue().strip()
            if summary:
                logger.info('%s: %s', name, summary)
            return summary
    except ScheduledJobFailed:
        return 'failed'
    except Exception as exc:  # noqa: BLE001
        # Failed before the command could record it (Redis down while taking the lock).
        record_failure_before_start(name, TRIGGER_CELERY, exc)
        return 'failed'
