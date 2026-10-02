"""Encryption for provider credentials and message variables, plus address HMAC."""

from __future__ import annotations

import hashlib
import hmac
import json

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet(setting_name: str) -> Fernet:
    key = getattr(settings, setting_name, '') or ''
    return Fernet(key.encode('ascii'))


def encrypt_json(payload: dict, *, setting: str = 'EMAIL_VARIABLES_KEY') -> str:
    raw = json.dumps(payload, separators=(',', ':'), sort_keys=True, ensure_ascii=False).encode('utf-8')
    return _fernet(setting).encrypt(raw).decode('ascii')


def decrypt_json(token: str, *, setting: str = 'EMAIL_VARIABLES_KEY') -> dict:
    if not token:
        return {}
    try:
        raw = _fernet(setting).decrypt(token.encode('ascii'))
    except (InvalidToken, ValueError):
        return {}
    data = json.loads(raw.decode('utf-8'))
    return data if isinstance(data, dict) else {}


def encrypt_text(value: str, *, setting: str = 'EMAIL_CREDENTIALS_KEY') -> str:
    if not value:
        return ''
    return _fernet(setting).encrypt(value.encode('utf-8')).decode('ascii')


def decrypt_text(token: str, *, setting: str = 'EMAIL_CREDENTIALS_KEY') -> str:
    if not token:
        return ''
    try:
        return _fernet(setting).decrypt(token.encode('ascii')).decode('utf-8')
    except (InvalidToken, ValueError):
        return ''


def email_hmac(address: str) -> str:
    normalized = (address or '').strip().lower()
    pepper = (settings.EMAIL_HASH_PEPPER or '').encode('utf-8')
    return hmac.new(pepper, normalized.encode('utf-8'), hashlib.sha256).hexdigest()


def mask_email(address: str) -> str:
    text = (address or '').strip().lower()
    if '@' not in text:
        return '***'
    local, domain = text.split('@', 1)
    if not local:
        return f'***@{domain}'
    return f'{local[0]}***@{domain}'


def mask_secret(secret: str) -> str:
    text = (secret or '').strip()
    if len(text) < 4:
        return '••••' if text else ''
    return f'••••{text[-4:]}'
