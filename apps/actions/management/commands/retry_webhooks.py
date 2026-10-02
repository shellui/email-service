from django.core.management.base import BaseCommand

from apps.actions.delivery import deliver_due


class Command(BaseCommand):
    help = 'Retry pending Shellui Actions webhook deliveries.'

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=50)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        if options['dry_run']:
            self.stdout.write('dry-run')
            return
        count = deliver_due(limit=options['batch_size'])
        self.stdout.write(f'processed={count}')
