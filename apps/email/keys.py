"""Issue service API keys. The plaintext value is returned once."""

from __future__ import annotations

import secrets

from apps.authapi.service_auth import SERVICE_KEY_PREFIX, hash_service_key
from apps.email.models import ServiceClient


def issue_service_key(
    *,
    service: str,
    name: str = '',
    allowed_lanes: list[str] | None = None,
    allowed_template_prefixes: list[str] | None = None,
    allowed_company_ids: list[int] | None = None,
    callback_url: str = '',
    callback_secret: str = '',
) -> tuple[str, ServiceClient]:
    raw = SERVICE_KEY_PREFIX + secrets.token_urlsafe(32)
    from apps.email.crypto import encrypt_text

    client = ServiceClient.objects.create(
        service=service,
        name=name or service,
        key_prefix=raw[:12],
        key_hash=hash_service_key(raw),
        allowed_lanes=list(allowed_lanes or ['transactional']),
        allowed_template_prefixes=list(allowed_template_prefixes or [f'{service}.']),
        allowed_company_ids=allowed_company_ids,
        callback_url=callback_url,
        callback_secret_ciphertext=encrypt_text(callback_secret) if callback_secret else '',
    )
    return raw, client
