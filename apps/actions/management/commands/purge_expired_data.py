from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.actions.models import ActionOutbox, EventLog
from apps.email.models import Message, MessageEvent, SendRequest


class Command(BaseCommand):
    help = 'Delete expired messages, send requests, event log rows, and finished webhook deliveries.'

    def handle(self, *args, **options):
        now = timezone.now()
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
        self.stdout.write(
            f'messages={messages_deleted} send_requests={requests_deleted} '
            f'event_log={logs_deleted} deliveries={deliveries_deleted}'
        )
