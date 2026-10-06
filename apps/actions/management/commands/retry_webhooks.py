import argparse

from django.core.management.base import BaseCommand

from apps.actions.delivery import deliver_due_stats
from apps.actions.scheduled_jobs import TRIGGER_COMMAND, TRIGGERS, run_recorded

JOB = 'retry_webhooks'


def run_counts(stats: dict) -> dict[str, int]:
    """Run summary stored on ``ScheduledJobRun.counts``."""
    return {
        'webhook_deliveries_attempted': stats['processed'],
        'webhook_deliveries_succeeded': stats['delivered'],
        'webhook_deliveries_failed': stats['retried'],
        'webhook_deliveries_given_up': stats['dead'],
    }


class Command(BaseCommand):
    help = 'Send due Shellui Actions webhook deliveries, first tries and retries (every minute).'

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=50)
        parser.add_argument('--dry-run', action='store_true', help='Do nothing (not recorded as a run).')
        # Set to "celery" by the Celery task; cron runs keep the default.
        parser.add_argument('--trigger', choices=TRIGGERS, default=TRIGGER_COMMAND, help=argparse.SUPPRESS)

    def handle(self, *args, **options):
        if options['dry_run']:
            self.stdout.write('dry-run')
            return
        batch_size = max(1, int(options['batch_size']))

        def work(run_id):
            stats = deliver_due_stats(limit=batch_size, scheduled_job_run_id=run_id)
            return stats, run_counts(stats)

        stats, run_id = run_recorded(JOB, options.get('trigger') or TRIGGER_COMMAND, work)
        self.stdout.write(
            f"processed={stats['processed']} delivered={stats['delivered']} "
            f"retried={stats['retried']} dead={stats['dead']} run_id={run_id}"
        )
