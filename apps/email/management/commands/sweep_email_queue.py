import argparse

from django.core.management.base import BaseCommand

from apps.actions.scheduled_jobs import TRIGGER_COMMAND, TRIGGERS, run_recorded
from apps.email.broadcasts import process_broadcasts
from apps.email.service import sweep

JOB = 'sweep_email_queue'


class Command(BaseCommand):
    help = 'Expire overdue messages, release stale send leases, and move broadcasts forward.'

    def add_arguments(self, parser):
        # Set to "celery" by the Celery task; external runs keep the default.
        parser.add_argument('--trigger', choices=TRIGGERS, default=TRIGGER_COMMAND, help=argparse.SUPPRESS)

    def handle(self, *args, **options):
        def work(_run_id):
            result = sweep()
            broadcasts = process_broadcasts()
            counts = {
                'expired': int(result['expired']),
                'released': int(result['released']),
                'broadcasts': int(broadcasts),
            }
            return counts, counts

        stats, run_id = run_recorded(JOB, options.get('trigger') or TRIGGER_COMMAND, work)
        self.stdout.write(
            f"expired={stats['expired']} released={stats['released']} broadcasts={stats['broadcasts']} "
            f'run_id={run_id}'
        )
