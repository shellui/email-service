import argparse
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.actions.models import ActionOutbox, EventLog
from apps.actions.scheduled_jobs import TRIGGER_COMMAND, TRIGGERS, purge_old_runs, run_recorded
from apps.email.models import Message, MessageEvent, SendRequest
from apps.email.newsletters import purge as purge_newsletters

JOB = 'purge_expired_data'


def purge(now) -> dict[str, int]:
    message_before = now - timedelta(days=settings.EMAIL_MESSAGE_RETENTION_DAYS)
    log_before = now - timedelta(days=settings.EVENT_LOG_RETENTION_DAYS)
    idem_before = now - timedelta(hours=settings.EMAIL_IDEMPOTENCY_HOURS)
    old_messages = Message.objects.filter(accepted_at__lt=message_before)
    MessageEvent.objects.filter(message__in=old_messages).delete()
    messages_deleted, _rest = old_messages.delete()
    requests_deleted, _rest = SendRequest.objects.filter(created_at__lt=idem_before).delete()
    logs_deleted, _rest = EventLog.objects.filter(created_at__lt=log_before).delete()
    deliveries_deleted, _rest = ActionOutbox.objects.filter(
        status__in=[ActionOutbox.STATUS_DELIVERED, ActionOutbox.STATUS_DEAD],
        created_at__lt=log_before,
    ).delete()
    pending_deleted, unsubscribed_cleared = purge_newsletters(now)
    runs_deleted, _finished = purge_old_runs(now=now, batch_size=1000, deadline=None)
    return {
        'messages': messages_deleted,
        'send_requests': requests_deleted,
        'event_log': logs_deleted,
        'webhook_deliveries': deliveries_deleted,
        'newsletter_pending': pending_deleted,
        'newsletter_cleared': unsubscribed_cleared,
        'scheduled_job_runs': runs_deleted,
    }


class Command(BaseCommand):
    help = (
        'Delete expired messages, send requests, event log rows, finished webhook deliveries, '
        'unconfirmed newsletter sign-ups, and scheduled job runs.'
    )

    def add_arguments(self, parser):
        # Set to "celery" by the Celery task; cron runs keep the default.
        parser.add_argument('--trigger', choices=TRIGGERS, default=TRIGGER_COMMAND, help=argparse.SUPPRESS)

    def handle(self, *args, **options):
        def work(_run_id):
            counts = purge(timezone.now())
            return counts, counts

        counts, run_id = run_recorded(JOB, options.get('trigger') or TRIGGER_COMMAND, work)
        self.stdout.write(
            f"messages={counts['messages']} send_requests={counts['send_requests']} "
            f"event_log={counts['event_log']} deliveries={counts['webhook_deliveries']} "
            f"newsletter_pending={counts['newsletter_pending']} "
            f"newsletter_cleared={counts['newsletter_cleared']} "
            f"scheduled_job_runs={counts['scheduled_job_runs']} run_id={run_id}"
        )
