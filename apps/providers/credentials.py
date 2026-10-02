"""Drop flags that only the platform relay may set."""

from __future__ import annotations

INTERNAL_CREDENTIAL_KEYS = frozenset({'trusted_platform'})


def strip_internal_credentials(credentials: dict) -> dict:
    """Company-supplied credentials cannot carry platform trust flags."""
    cleaned = {}
    for key, value in (credentials or {}).items():
        name = str(key)
        if name in INTERNAL_CREDENTIAL_KEYS or name.startswith('_'):
            continue
        cleaned[key] = value
    return cleaned
