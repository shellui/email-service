import argparse

from django.core.management.base import BaseCommand

from apps.actions.delivery import deliver_due
from apps.actions.scheduled_jobs import TRIGGER_COMMAND, TRIGGERS, run_recorded

JOB = 'retry_webhooks'


def run_counts(stats: dict) -> dict[str, int]:
    """Run summary stored on ``ScheduledJobRun.counts``."""

    def n(key: str) -> int:
        return int(stats.get(key) or 0)

    return {
        'webhook_deliveries_attempted': n('processed'),
        'webhook_deliveries_succeeded': n('delivered'),
        'webhook_deliveries_failed': n('retried'),
        'webhook_deliveries_given_up': n('dead'),
    }


class Command(BaseCommand):
    help = 'Retry pending Shellui Actions webhook deliveries.'

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=50)
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print a line and do not deliver or record a run.',
        )
        # Set to "celery" by the Celery task; external runs keep the default.
        parser.add_argument('--trigger', choices=TRIGGERS, default=TRIGGER_COMMAND, help=argparse.SUPPRESS)

    def handle(self, *args, **options):
        batch_size = max(1, int(options['batch_size']))
        if options['dry_run']:
            self.stdout.write('dry-run')
            return

        def work(run_id):
            stats = deliver_due(limit=batch_size, scheduled_job_run_id=run_id)
            return stats, run_counts(stats)

        stats, run_id = run_recorded(JOB, options.get('trigger') or TRIGGER_COMMAND, work)
        self.stdout.write(
            f"processed={stats['processed']} delivered={stats['delivered']} "
            f"retried={stats['retried']} dead={stats['dead']} run_id={run_id}"
        )
