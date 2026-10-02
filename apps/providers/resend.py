"""Resend adapter. https://resend.com/docs/api-reference/emails/send-email"""

from __future__ import annotations

import requests

from apps.providers.base import ProviderMessage, ProviderResult, sanitize_header_value

RESEND_URL = 'https://api.resend.com/emails'
USER_AGENT = 'shellui-email-service/1.0'


def _format_from(name: str, email: str) -> str:
    safe_email = sanitize_header_value(email).strip()
    safe_name = sanitize_header_value(name).replace('"', '').strip()
    if safe_name:
        return f'"{safe_name}" <{safe_email}>'
    return safe_email


class ResendProvider:
    name = 'resend'

    def send(self, message: ProviderMessage, credentials: dict) -> ProviderResult:
        api_key = (credentials or {}).get('api_key') or ''
        if not api_key:
            return ProviderResult(ok=False, retryable=False, error_code='provider_not_configured')
        payload = {
            'from': _format_from(message.from_name, message.from_email),
            'to': [message.to_email],
            'subject': message.subject,
            'html': message.html,
            'text': message.text,
            'headers': message.headers or None,
            'tags': [{'name': key, 'value': value} for key, value in (message.tags or {}).items()],
        }
        if not payload['headers']:
            payload.pop('headers')
        if not payload['tags']:
            payload.pop('tags')
        try:
            response = requests.post(
                RESEND_URL,
                json=payload,
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'User-Agent': USER_AGENT,
                    'Idempotency-Key': message.idempotency_key,
                    'Content-Type': 'application/json',
                },
                timeout=10,
            )
        except requests.Timeout:
            return ProviderResult(ok=False, retryable=True, error_code='provider_timeout')
        except requests.RequestException:
            return ProviderResult(ok=False, retryable=True, error_code='provider_unreachable')
        if 200 <= response.status_code < 300:
            body = response.json() if response.content else {}
            return ProviderResult(ok=True, provider_message_id=str(body.get('id') or ''))
        if response.status_code in {401, 403}:
            return ProviderResult(ok=False, retryable=False, error_code='provider_unauthorized')
        if response.status_code == 429 or response.status_code >= 500:
            retry_after = _retry_after(response)
            code = 'provider_rate_limited' if response.status_code == 429 else 'provider_unavailable'
            return ProviderResult(
                ok=False,
                retryable=True,
                retry_after_seconds=retry_after,
                error_code=code,
            )
        return ProviderResult(ok=False, retryable=False, error_code='provider_rejected')


def _retry_after(response: requests.Response) -> float | None:
    raw = response.headers.get('Retry-After')
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None
