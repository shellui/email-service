from django.core.management.base import BaseCommand

from apps.email.service import sweep


class Command(BaseCommand):
    help = 'Expire overdue messages and release stale send leases.'

    def handle(self, *args, **options):
        result = sweep()
        self.stdout.write(f"expired={result['expired']} released={result['released']}")
