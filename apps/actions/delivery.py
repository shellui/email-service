"""Deliver pending Shellui Actions webhooks."""

from __future__ import annotations

import time
from datetime import timedelta

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from requests.adapters import HTTPAdapter

from apps.actions.emit import store_rule_secret
from apps.actions.models import ActionOutbox, DeliveryAttempt
from apps.actions.ssrf import SSRFError, resolve_webhook_endpoint
from apps.actions.webhook_signing import encode_webhook_envelope, sign_webhook_body
from apps.email.crypto import decrypt_text

_RETRYABLE_STATUS = {404, 408, 409, 425, 429}
_DEAD_STATUS = {400, 401, 403, 405, 410, 413, 422}


def _secret_for(row: ActionOutbox) -> str:
    if row.action_rule_id:
        raw = (row.action_rule.config or {}).get('secret') or ''
        if raw.startswith('enc:'):
            return decrypt_text(raw[4:])
        return raw
    if row.callback_service:
        from apps.email.models import ServiceClient

        client = (
            ServiceClient.objects.filter(service=row.callback_service, active=True)
            .exclude(callback_secret_ciphertext='')
            .order_by('-id')
            .first()
        )
        if client:
            return decrypt_text(client.callback_secret_ciphertext)
    return ''


def _backoff_seconds(attempt: int, retry_after: float | None) -> int:
    if retry_after is not None:
        return max(1, min(int(retry_after), 3600))
    delay = 30 * (2 ** max(attempt - 1, 0))
    return min(delay, 3600)


class _PinnedTLSAdapter(HTTPAdapter):
    """HTTPS to a pinned IP, with SNI and certificate checks on the original hostname."""

    def __init__(self, hostname: str, **kwargs):
        self._hostname = hostname
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs['server_hostname'] = self._hostname
        pool_kwargs['assert_hostname'] = self._hostname
        return super().init_poolmanager(connections, maxsize, block, **pool_kwargs)


def _pinned_url(resolved) -> str:
    host = resolved.connect_host
    if ':' in host and not host.startswith('['):
        netloc = f'[{host}]:{resolved.port}'
    else:
        netloc = f'{host}:{resolved.port}'
    return f'{resolved.scheme}://{netloc}{resolved.path}'


def _post_pinned(resolved, body: bytes, headers: dict, timeout: float):
    """POST to the resolved IP. Redirects are not followed."""
    request_headers = dict(headers)
    request_headers['Host'] = resolved.host_header
    session = requests.Session()
    try:
        if resolved.scheme == 'https':
            hostname = resolved.host_header.split(':', 1)[0]
            session.mount('https://', _PinnedTLSAdapter(hostname))
        return session.post(
            _pinned_url(resolved),
            data=body,
            headers=request_headers,
            timeout=timeout,
            allow_redirects=False,
        )
    finally:
        session.close()


def deliver_one(row_id) -> None:
    with transaction.atomic():
        row = ActionOutbox.objects.select_for_update().get(pk=row_id)
        if row.status not in {ActionOutbox.STATUS_PENDING, ActionOutbox.STATUS_FAILED}:
            return
        row.attempt_count += 1
        row.locked_until = timezone.now() + timedelta(seconds=settings.ACTIONS_WEBHOOK_RETRY_LEASE_SECONDS)
        row.save(update_fields=['attempt_count', 'locked_until', 'updated_at'])
        secret = _secret_for(row)
        url = row.target_url
        body = encode_webhook_envelope(row.envelope)
        headers = sign_webhook_body(secret=secret or 'missing', body=body, webhook_id=row.webhook_id)
        headers['Content-Type'] = 'application/json'
        headers['User-Agent'] = 'shellui-email-webhooks/1.0'
        headers['X-Shellui-Event'] = row.event_type
        headers['X-Shellui-Delivery-Attempt'] = str(row.attempt_count)
        attempt_number = row.attempt_count
        allow_private = settings.ACTIONS_WEBHOOK_ALLOW_PRIVATE
        if row.action_rule_id and (row.action_rule.config or {}).get('allow_private_urls'):
            allow_private = True

    started = time.monotonic()
    error_code = ''
    http_status = None
    retryable = False
    retry_after = None
    try:
        resolved = resolve_webhook_endpoint(url, allow_private=allow_private)
        response = _post_pinned(
            resolved,
            body,
            headers,
            settings.ACTIONS_WEBHOOK_TIMEOUT_SECONDS,
        )
        http_status = response.status_code
        if 300 <= http_status < 400:
            error_code = 'redirect_blocked'
            retryable = False
        elif 200 <= http_status < 300:
            error_code = ''
        elif http_status in _DEAD_STATUS:
            error_code = f'http_{http_status}'
            retryable = False
        elif http_status in _RETRYABLE_STATUS or http_status >= 500:
            error_code = f'http_{http_status}'
            retryable = True
            raw_after = response.headers.get('Retry-After')
            if raw_after:
                try:
                    retry_after = float(raw_after)
                except ValueError:
                    retry_after = None
        else:
            error_code = f'http_{http_status}'
            retryable = True
    except SSRFError:
        error_code = 'ssrf_blocked'
        retryable = False
    except requests.Timeout:
        error_code = 'timeout'
        retryable = True
    except requests.RequestException:
        error_code = 'connection_error'
        retryable = True
    duration_ms = int((time.monotonic() - started) * 1000)

    with transaction.atomic():
        row = ActionOutbox.objects.select_for_update().get(pk=row_id)
        DeliveryAttempt.objects.create(
            outbox=row,
            status=DeliveryAttempt.STATUS_SUCCESS if not error_code else DeliveryAttempt.STATUS_FAILURE,
            http_status=http_status,
            error_code=error_code,
            attempt_number=attempt_number,
            duration_ms=duration_ms,
        )
        if not error_code:
            row.status = ActionOutbox.STATUS_DELIVERED
            row.delivered_at = timezone.now()
            row.last_error = ''
            row.locked_until = None
        elif retryable and attempt_number < settings.ACTIONS_OUTBOX_MAX_ATTEMPTS:
            row.status = ActionOutbox.STATUS_FAILED
            row.last_error = error_code
            row.next_attempt_at = timezone.now() + timedelta(seconds=_backoff_seconds(attempt_number, retry_after))
            row.locked_until = None
        else:
            row.status = ActionOutbox.STATUS_DEAD
            row.last_error = error_code or 'delivery_failed'
            row.locked_until = None
        row.save()


def deliver_due(*, limit: int = 50) -> int:
    now = timezone.now()
    ids = list(
        ActionOutbox.objects.filter(
            status__in=[ActionOutbox.STATUS_PENDING, ActionOutbox.STATUS_FAILED],
            next_attempt_at__lte=now,
        )
        .order_by('next_attempt_at')
        .values_list('id', flat=True)[:limit]
    )
    for row_id in ids:
        deliver_one(row_id)
    return len(ids)


def rotate_secret_value(new_secret: str | None = None) -> str:
    return store_rule_secret(new_secret or '')
