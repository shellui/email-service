"""Resend adapter. https://resend.com/docs/api-reference/emails/send-email"""

from __future__ import annotations

from urllib.parse import quote

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


class ResendMarketingError(Exception):
    def __init__(self, code: str, *, retryable: bool, retry_after: float | None = None):
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after
        super().__init__(code)


class ResendMarketing:
    """Segments, contacts, and broadcasts. https://resend.com/docs/api-reference/broadcasts/create-broadcast"""

    BASE_URL = 'https://api.resend.com'

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        try:
            response = requests.request(
                method,
                f'{self.BASE_URL}{path}',
                json=payload,
                headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'User-Agent': USER_AGENT,
                    'Content-Type': 'application/json',
                },
                timeout=10,
            )
        except requests.RequestException:
            raise ResendMarketingError('provider_unreachable', retryable=True)
        if response.status_code in {401, 403}:
            raise ResendMarketingError('provider_unauthorized', retryable=False)
        if response.status_code == 429 or response.status_code >= 500:
            code = 'provider_rate_limited' if response.status_code == 429 else 'provider_unavailable'
            raise ResendMarketingError(code, retryable=True, retry_after=_retry_after(response))
        try:
            body = response.json() if response.content else {}
        except ValueError:
            body = {}
        return response.status_code, body if isinstance(body, dict) else {}

    def _created_id(self, method: str, path: str, payload: dict) -> str:
        status, body = self._request(method, path, payload)
        if not 200 <= status < 300 or not body.get('id'):
            raise ResendMarketingError('provider_rejected', retryable=False)
        return str(body['id'])

    def create_segment(self, name: str) -> str:
        return self._created_id('POST', '/segments', {'name': name[:100]})

    def add_contact(self, segment_id: str, *, email: str, first_name: str, last_name: str) -> None:
        """Create the contact in the segment, or add the existing contact to it."""
        payload = {'email': email, 'segments': [{'id': segment_id}]}
        if first_name:
            payload['first_name'] = first_name
        if last_name:
            payload['last_name'] = last_name
        status, _body = self._request('POST', '/contacts', payload)
        if 200 <= status < 300:
            return
        status, _body = self._request('POST', f'/contacts/{quote(email, safe="@")}/segments/{segment_id}')
        if not 200 <= status < 300:
            raise ResendMarketingError('contact_rejected', retryable=False)

    def send_broadcast(
        self, *, segment_id: str, from_header: str, subject: str, html: str, text: str, name: str
    ) -> str:
        return self._created_id(
            'POST',
            '/broadcasts',
            {
                'segment_id': segment_id,
                'from': from_header,
                'subject': subject,
                'html': html,
                'text': text,
                'name': name[:100],
                'send': True,
            },
        )


def format_from(name: str, email: str) -> str:
    return _format_from(name, email)


def _retry_after(response: requests.Response) -> float | None:
    raw = response.headers.get('Retry-After')
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None
