"""Amazon SES adapter over the SESv2 HTTP API.

https://docs.aws.amazon.com/ses/latest/APIReference-V2/API_SendEmail.html

Uses the HTTPS API, not SMTP, so regions without an SMTP endpoint (for
example Zurich, ``eu-central-2``) work. Requests are signed with SigV4 by
hand so the service does not need boto3.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import re

import requests

from apps.providers.base import ProviderMessage, ProviderResult, sanitize_header_value
from apps.providers.resend import format_from

USER_AGENT = 'shellui-email-service/1.0'
DEFAULT_REGION = 'eu-central-2'
SERVICE = 'ses'
SEND_PATH = '/v2/email/outbound-emails'

REGION_PATTERN = re.compile(r'^[a-z]{2}(-gov)?-[a-z]+-\d{1,2}$')
ACCESS_KEY_PATTERN = re.compile(r'^[A-Z0-9]{16,128}$')
CONFIGURATION_SET_PATTERN = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
TAG_PATTERN = re.compile(r'[^A-Za-z0-9_-]')

# Headers SES sets itself or that the Simple content type refuses.
_RESERVED_HEADERS = frozenset(
    {'from', 'to', 'cc', 'bcc', 'subject', 'reply-to', 'content-type', 'mime-version', 'date', 'return-path'}
)

_RETRYABLE_ERRORS = frozenset({'TooManyRequestsException', 'LimitExceededException', 'ThrottlingException'})
_UNAUTHORIZED_ERRORS = frozenset(
    {
        'AccountSuspendedException',
        'SendingPausedException',
        'AccessDeniedException',
        'UnrecognizedClientException',
        'InvalidSignatureException',
        'SignatureDoesNotMatch',
        'IncompleteSignature',
        'MissingAuthenticationToken',
    }
)


def valid_region(region: str) -> bool:
    return bool(REGION_PATTERN.match(region or ''))


def validate_credentials(credentials: dict) -> dict[str, list[str]]:
    """Field errors for company SES credentials. Empty when valid."""
    errors: dict[str, list[str]] = {}
    access_key_id = str(credentials.get('access_key_id') or '').strip()
    if not access_key_id:
        errors['access_key_id'] = ['required']
    elif not ACCESS_KEY_PATTERN.match(access_key_id):
        errors['access_key_id'] = ['invalid_format']
    if not str(credentials.get('secret_access_key') or '').strip():
        errors['secret_access_key'] = ['required']
    region = str(credentials.get('region') or DEFAULT_REGION).strip()
    if not valid_region(region):
        errors['region'] = ['invalid_region']
    configuration_set = str(credentials.get('configuration_set') or '').strip()
    if configuration_set and not CONFIGURATION_SET_PATTERN.match(configuration_set):
        errors['configuration_set'] = ['invalid_format']
    return errors


def normalize_credentials(credentials: dict) -> dict:
    """Keep only the SES keys, trimmed, with the default region filled in."""
    cleaned = {
        'access_key_id': str(credentials.get('access_key_id') or '').strip(),
        'secret_access_key': str(credentials.get('secret_access_key') or '').strip(),
        'region': str(credentials.get('region') or DEFAULT_REGION).strip(),
    }
    configuration_set = str(credentials.get('configuration_set') or '').strip()
    if configuration_set:
        cleaned['configuration_set'] = configuration_set
    return cleaned


def _hmac(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode('utf-8'), hashlib.sha256).digest()


def sign_request(
    *, method: str, host: str, path: str, body: bytes, region: str, access_key_id: str, secret_access_key: str,
    now: datetime.datetime | None = None,
) -> dict[str, str]:
    """SigV4 headers for one request. https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_sigv.html"""
    now = now or datetime.datetime.now(datetime.UTC)
    amz_date = now.strftime('%Y%m%dT%H%M%SZ')
    date_stamp = now.strftime('%Y%m%d')
    payload_hash = hashlib.sha256(body).hexdigest()
    headers = {'content-type': 'application/json', 'host': host, 'x-amz-date': amz_date}
    signed_headers = ';'.join(sorted(headers))
    canonical_headers = ''.join(f'{name}:{headers[name]}\n' for name in sorted(headers))
    canonical_request = '\n'.join([method, path, '', canonical_headers, signed_headers, payload_hash])
    scope = f'{date_stamp}/{region}/{SERVICE}/aws4_request'
    string_to_sign = '\n'.join(
        ['AWS4-HMAC-SHA256', amz_date, scope, hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()]
    )
    key = _hmac(('AWS4' + secret_access_key).encode('utf-8'), date_stamp)
    key = _hmac(key, region)
    key = _hmac(key, SERVICE)
    key = _hmac(key, 'aws4_request')
    signature = hmac.new(key, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
    return {
        'Content-Type': 'application/json',
        'X-Amz-Date': amz_date,
        'X-Amz-Content-Sha256': payload_hash,
        'Authorization': (
            f'AWS4-HMAC-SHA256 Credential={access_key_id}/{scope}, '
            f'SignedHeaders={signed_headers}, Signature={signature}'
        ),
    }


def build_payload(message: ProviderMessage, credentials: dict) -> dict:
    headers = []
    reply_to = []
    for name, value in (message.headers or {}).items():
        safe_name = sanitize_header_value(str(name)).strip()
        safe_value = sanitize_header_value(str(value)).strip()
        if not safe_name:
            continue
        if safe_name.lower() == 'reply-to':
            reply_to.append(safe_value)
            continue
        if safe_name.lower() in _RESERVED_HEADERS:
            continue
        headers.append({'Name': safe_name, 'Value': safe_value})
    simple: dict = {
        'Subject': {'Data': message.subject, 'Charset': 'UTF-8'},
        'Body': {},
    }
    if message.html:
        simple['Body']['Html'] = {'Data': message.html, 'Charset': 'UTF-8'}
    if message.text:
        simple['Body']['Text'] = {'Data': message.text, 'Charset': 'UTF-8'}
    if headers:
        simple['Headers'] = headers
    payload: dict = {
        'FromEmailAddress': format_from(message.from_name, message.from_email),
        'Destination': {'ToAddresses': [message.to_email]},
        'Content': {'Simple': simple},
    }
    if reply_to:
        payload['ReplyToAddresses'] = reply_to
    tags = [
        {'Name': TAG_PATTERN.sub('_', str(key))[:256], 'Value': TAG_PATTERN.sub('_', str(value))[:256]}
        for key, value in (message.tags or {}).items()
        if str(key) and str(value)
    ]
    tags.append({'Name': 'message_id', 'Value': TAG_PATTERN.sub('_', message.message_id)[:256]})
    payload['EmailTags'] = tags
    if credentials.get('configuration_set'):
        payload['ConfigurationSetName'] = credentials['configuration_set']
    return payload


def _error_type(response: requests.Response) -> str:
    raw = response.headers.get('x-amzn-ErrorType') or ''
    if not raw:
        try:
            body = response.json()
        except ValueError:
            body = {}
        if isinstance(body, dict):
            raw = str(body.get('__type') or body.get('code') or body.get('Code') or '')
    return raw.split(':', 1)[0].rsplit('#', 1)[-1]


class SesProvider:
    name = 'ses'

    def send(self, message: ProviderMessage, credentials: dict) -> ProviderResult:
        credentials = credentials or {}
        access_key_id = str(credentials.get('access_key_id') or '')
        secret_access_key = str(credentials.get('secret_access_key') or '')
        region = str(credentials.get('region') or DEFAULT_REGION)
        if not access_key_id or not secret_access_key:
            return ProviderResult(ok=False, retryable=False, error_code='provider_not_configured')
        if not valid_region(region):
            return ProviderResult(ok=False, retryable=False, error_code='provider_not_configured')
        host = f'email.{region}.amazonaws.com'
        body = json.dumps(build_payload(message, credentials), separators=(',', ':')).encode('utf-8')
        headers = sign_request(
            method='POST', host=host, path=SEND_PATH, body=body, region=region,
            access_key_id=access_key_id, secret_access_key=secret_access_key,
        )
        headers['User-Agent'] = USER_AGENT
        try:
            response = requests.post(f'https://{host}{SEND_PATH}', data=body, headers=headers, timeout=10)
        except requests.Timeout:
            return ProviderResult(ok=False, retryable=True, error_code='provider_timeout')
        except requests.RequestException:
            return ProviderResult(ok=False, retryable=True, error_code='provider_unreachable')
        if 200 <= response.status_code < 300:
            try:
                data = response.json() if response.content else {}
            except ValueError:
                data = {}
            return ProviderResult(ok=True, provider_message_id=str((data or {}).get('MessageId') or ''))
        error = _error_type(response)
        if error in _RETRYABLE_ERRORS or response.status_code == 429:
            return ProviderResult(
                ok=False, retryable=True, retry_after_seconds=_retry_after(response), error_code='provider_rate_limited'
            )
        if response.status_code >= 500:
            return ProviderResult(
                ok=False, retryable=True, retry_after_seconds=_retry_after(response), error_code='provider_unavailable'
            )
        if error in _UNAUTHORIZED_ERRORS or response.status_code in {401, 403}:
            return ProviderResult(ok=False, retryable=False, error_code='provider_unauthorized')
        return ProviderResult(ok=False, retryable=False, error_code='provider_rejected')


def _retry_after(response: requests.Response) -> float | None:
    raw = response.headers.get('Retry-After')
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None
