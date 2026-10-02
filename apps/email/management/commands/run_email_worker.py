import time

from django.core.management.base import BaseCommand

from apps.email.service import deliver_due


class Command(BaseCommand):
    help = 'Claim and send queued messages for one lane.'

    def add_arguments(self, parser):
        parser.add_argument('--lane', required=True, choices=['auth', 'transactional', 'bulk'])
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--poll-seconds', type=float, default=1.0)
        parser.add_argument('--batch-size', type=int, default=20)

    def handle(self, *args, **options):
        lane = options['lane']
        while True:
            count = deliver_due(lane, limit=options['batch_size'])
            if options['once']:
                self.stdout.write(f'processed={count}')
                return
            if count == 0:
                time.sleep(options['poll_seconds'])
