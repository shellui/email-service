import argparse

from django.core.management.base import BaseCommand

from apps.actions.scheduled_jobs import TRIGGER_COMMAND, TRIGGERS, run_recorded
from apps.email.broadcasts import process_broadcasts
from apps.email.service import sweep

JOB = 'sweep_email_queue'


class Command(BaseCommand):
    help = 'Expire overdue messages, release stale send leases, and move broadcasts forward.'

    def add_arguments(self, parser):
        # Set to "celery" by the Celery task; cron runs keep the default.
        parser.add_argument('--trigger', choices=TRIGGERS, default=TRIGGER_COMMAND, help=argparse.SUPPRESS)

    def handle(self, *args, **options):
        def work(_run_id):
            result = sweep()
            broadcasts = process_broadcasts()
            counts = {
                'messages_expired': result['expired'],
                'send_leases_released': result['released'],
                'broadcasts_pending': broadcasts,
            }
            return (result, broadcasts), counts

        (result, broadcasts), run_id = run_recorded(JOB, options.get('trigger') or TRIGGER_COMMAND, work)
        self.stdout.write(
            f"expired={result['expired']} released={result['released']} broadcasts={broadcasts} run_id={run_id}"
        )
