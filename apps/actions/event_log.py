"""Write staff-only platform events. Company events still go through ``emit_email_event``."""

from __future__ import annotations

from typing import Any

from apps.actions.models import EventLog
from apps.actions.secret_redaction import redact_secrets

_EMPTY = (None, '', [], {})
_SKIP = frozenset({'company_id', 'user_id', 'error_message'})


def record_event(event_type: str, company_id: int | None, payload: dict[str, Any]) -> EventLog:
    """
    Insert one log row. ``company_id`` is null for staff-only platform events.

    Does not enqueue Shellui Actions deliveries. ``error_message`` is never stored:
    the run row keeps the sanitized text, and the log keeps ``error_key`` only.
    """
    raw = redact_secrets(payload or {})
    data = raw if isinstance(raw, dict) else {}
    cleaned = {
        key: value
        for key, value in data.items()
        if key not in _SKIP and value not in _EMPTY and value is not False
    }
    return EventLog.objects.create(
        company_id=None if company_id is None else int(company_id),
        event_type=event_type,
        data=cleaned,
    )
