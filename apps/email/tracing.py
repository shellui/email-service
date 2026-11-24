"""Trace context for a message that is queued in one process and sent in another.

The HTTP span's W3C ``traceparent`` is stored on the message. The lane worker
starts ``email.send`` as a child of that span, so the provider call stays on
the request trace. Recipient addresses and message content are not span attributes.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager

_TRACEPARENT = re.compile(r'^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$')


def trace_fields() -> dict[str, str]:
    """``traceparent`` and ``tracestate`` for the active span, or an empty dict."""
    try:
        from opentelemetry.propagate import inject
    except ImportError:
        return {}
    carrier: dict[str, str] = {}
    try:
        inject(carrier)
    except Exception:  # noqa: BLE001 - a tracer failure must not block queueing
        return {}
    parent = ''
    state = ''
    for key, value in carrier.items():
        if not isinstance(value, str):
            continue
        lowered = key.lower()
        if lowered == 'traceparent' and _TRACEPARENT.fullmatch(value.strip()):
            parent = value.strip()
        elif lowered == 'tracestate':
            state = value.strip()[:512]
    if not parent:
        return {}
    return {'traceparent': parent, 'tracestate': state}


def tag_span(*, company_id: int | None = None, user_id: int | None = None, agent_id: str | None = None) -> None:
    """Set opaque Shellui ids on the current span. Free text is ignored by the helper."""
    try:
        from shellui_django.telemetry import set_shellui_attributes
    except ImportError:
        return
    extra = {'agent_id': agent_id} if agent_id else None
    set_shellui_attributes(company_id=company_id, user_id=user_id, attributes=extra)


@contextmanager
def email_send_span(message) -> Iterator[object | None]:
    """Child of the span that queued ``message``. A missing parent starts a new trace."""
    carrier: dict[str, str] = {}
    parent = (getattr(message, 'traceparent', '') or '').strip()
    if parent:
        carrier['traceparent'] = parent
        state = (getattr(message, 'tracestate', '') or '').strip()
        if state:
            carrier['tracestate'] = state
    try:
        from opentelemetry import trace
        from opentelemetry.propagate import extract
    except ImportError:
        yield None
        return
    kwargs = {'context': extract(carrier)} if carrier else {}
    tracer = trace.get_tracer('shellui.email')
    with tracer.start_as_current_span('email.send', **kwargs) as span:
        if span.is_recording():
            span.set_attribute('email.lane', message.lane)
            span.set_attribute('email.template_id', message.template_key)
        tag_span(company_id=message.company_id, user_id=message.to_user_id)
        try:
            yield span
        finally:
            _set_result_attributes(span, message.pk)


@contextmanager
def provider_send_span(provider: str, *, lane: str, template_id: str) -> Iterator[object | None]:
    """Span around the provider call. Resend and SES HTTP calls nest under it."""
    try:
        from opentelemetry import trace
    except ImportError:
        yield None
        return
    tracer = trace.get_tracer('shellui.email')
    with tracer.start_as_current_span('email.provider.send') as span:
        if span.is_recording():
            span.set_attribute('email.provider', provider)
            span.set_attribute('email.lane', lane)
            span.set_attribute('email.template_id', template_id)
        yield span


def _set_result_attributes(span, message_id) -> None:
    if span is None or not span.is_recording():
        return
    try:
        from apps.email.models import Message

        row = Message.objects.filter(pk=message_id).values('provider', 'status').first()
    except Exception:  # noqa: BLE001 - span attributes are optional
        return
    if not row:
        return
    if row['provider']:
        span.set_attribute('email.provider', row['provider'])
    if row['status']:
        span.set_attribute('email.status', row['status'])
