from django.core.management.base import BaseCommand

from apps.email.broadcasts import process_broadcasts
from apps.email.service import sweep


class Command(BaseCommand):
    help = 'Expire overdue messages, release stale send leases, and move broadcasts forward.'

    def handle(self, *args, **options):
        result = sweep()
        broadcasts = process_broadcasts()
        self.stdout.write(f"expired={result['expired']} released={result['released']} broadcasts={broadcasts}")
