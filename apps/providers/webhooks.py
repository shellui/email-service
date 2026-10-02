"""Verify Resend (Svix / Standard Webhooks) callbacks and map them to message status."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

from apps.actions.webhook_signing import normalize_webhook_signing_secret

RESEND_STATUS = {
    'email.sent': 'sent',
    'email.delivered': 'delivered',
    'email.delivery_delayed': 'delivery_delayed',
    'email.bounced': 'bounced',
    'email.complained': 'complained',
    'email.failed': 'failed',
    'email.suppressed': 'suppressed',
}

MAX_AGE_SECONDS = 300


def verify_svix(*, secret: str, body: bytes, headers: dict[str, str]) -> bool:
    """Verify ``svix-id``, ``svix-timestamp``, and ``svix-signature`` over the raw body."""
    msg_id = headers.get('svix-id') or headers.get('webhook-id') or ''
    timestamp = headers.get('svix-timestamp') or headers.get('webhook-timestamp') or ''
    signature = headers.get('svix-signature') or headers.get('webhook-signature') or ''
    if not msg_id or not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(int(time.time()) - ts) > MAX_AGE_SECONDS:
        return False
    try:
        key = normalize_webhook_signing_secret(secret)
    except ValueError:
        return False
    signed = f'{msg_id}.{timestamp}.'.encode('utf-8') + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode('ascii')
    for part in signature.split(' '):
        part = part.strip()
        if part.startswith('v1,'):
            candidate = part[3:]
        elif part.startswith('v1='):
            candidate = part[3:]
        else:
            candidate = part
        if hmac.compare_digest(candidate, expected):
            return True
    return False
