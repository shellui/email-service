"""Signed one-click unsubscribe tokens. The address is not in the token."""

from __future__ import annotations

import hashlib
import hmac

from django.conf import settings

from apps.email.crypto import email_hmac


def unsubscribe_signature(company_id: int, email_hmac_value: str, category: str) -> str:
    return hmac.new(
        settings.EMAIL_HASH_PEPPER.encode('utf-8'),
        f'{company_id}|{email_hmac_value}|{category}'.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def unsubscribe_token(company_id: int, email: str, category: str) -> str:
    digest = email_hmac(email)
    signature = unsubscribe_signature(company_id, digest, category)
    return f'{company_id}.{category}.{digest}.{signature}'


def unsubscribe_url(company_id: int, email: str, category: str) -> str:
    token = unsubscribe_token(company_id, email, category)
    return f'{settings.PUBLIC_BASE_URL.rstrip("/")}/u/{token}'


def parse_unsubscribe_token(token: str) -> tuple[int, str, str] | None:
    parts = (token or '').split('.')
    if len(parts) != 4:
        return None
    try:
        company_id = int(parts[0])
    except ValueError:
        return None
    category, digest, signature = parts[1], parts[2], parts[3]
    if not category or not digest or not signature:
        return None
    expected = unsubscribe_signature(company_id, digest, category)
    if not hmac.compare_digest(expected, signature):
        return None
    return company_id, category, digest
