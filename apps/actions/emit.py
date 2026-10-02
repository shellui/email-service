"""Record an email event and enqueue Shellui Actions deliveries plus service callbacks."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.utils import timezone

from apps.actions.models import ActionOutbox, ActionRule, EventLog
from apps.actions.webhook_signing import generate_webhook_signing_secret
from apps.email.crypto import decrypt_text, encrypt_text


def build_envelope(*, event_type: str, company_id: int, data: dict, event_id: str | None = None) -> dict:
    return {
        'id': event_id or str(uuid.uuid4()),
        'type': event_type,
        'time': timezone.now().isoformat(),
        'company': {'id': company_id},
        'data': data,
    }


def emit_email_event(*, company_id: int, event_type: str, data: dict, service: str = '') -> None:
    """Persist the event log row and one outbox row per matching webhook rule and callback."""
    envelope = build_envelope(event_type=event_type, company_id=company_id, data=data)
    EventLog.objects.create(company_id=company_id, event_type=event_type, data=data)
    rules = ActionRule.objects.filter(company_id=company_id, event_type=event_type, enabled=True)
    now = timezone.now()
    for rule in rules:
        secret = (rule.config or {}).get('secret') or ''
        if secret.startswith('enc:'):
            secret = decrypt_text(secret[4:])
        ActionOutbox.objects.create(
            company_id=company_id,
            action_rule=rule,
            event_type=event_type,
            envelope=envelope,
            status=ActionOutbox.STATUS_PENDING,
            next_attempt_at=now,
            webhook_id=f'msgwh_{uuid.uuid4().hex}',
            target_url=(rule.config or {}).get('url') or '',
        )
        # Keep the plaintext only in memory. The outbox signs at delivery time from the rule.
        del secret
    if service:
        from apps.email.models import ServiceClient

        client = (
            ServiceClient.objects.filter(service=service, active=True)
            .exclude(callback_url='')
            .order_by('-last_used_at', '-id')
            .first()
        )
        if client and client.callback_url:
            ActionOutbox.objects.create(
                company_id=company_id,
                action_rule=None,
                event_type=event_type,
                envelope=envelope,
                status=ActionOutbox.STATUS_PENDING,
                next_attempt_at=now,
                webhook_id=f'msgwh_{uuid.uuid4().hex}',
                target_url=client.callback_url,
                callback_service=client.service,
            )
    if getattr(settings, 'ACTIONS_WEBHOOK_SYNC_DELIVERY', False):
        from apps.actions.delivery import deliver_due

        deliver_due(limit=20)


def store_rule_secret(secret: str) -> str:
    if not secret:
        secret = generate_webhook_signing_secret()
    return 'enc:' + encrypt_text(secret)
