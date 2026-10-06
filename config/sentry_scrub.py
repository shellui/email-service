"""Keep sign-in links out of Sentry events.

``POST /api/v1/send`` and ``/events`` bodies carry ``magic_link_url``. Frame locals in
the send path hold the rendered HTML and text, which contain it too. Neither is a
named secret that Sentry's default denylist would catch, so request bodies and locals
are never attached, and query strings are dropped from URLs.
"""

from __future__ import annotations

from typing import Any

FILTERED = '[Filtered]'

EXTRA_DENYLIST = [
    'magic_link_url',
    'invitation_url',
    'confirm_url',
    'variables',
    'variables_ciphertext',
    'html',
    'text',
    'payload',
    'query_string',
    'referer',
    'http_referer',
]

_DROPPED_HEADERS = {'referer', 'cookie', 'authorization'}


def _without_query(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return value.split('?', 1)[0].split('#', 1)[0]


def before_send(event: dict, hint: dict | None = None) -> dict:
    request = event.get('request')
    if isinstance(request, dict):
        if 'url' in request:
            request['url'] = _without_query(request['url'])
        if request.get('query_string'):
            request['query_string'] = FILTERED
        if request.get('data'):
            request['data'] = FILTERED
        headers = request.get('headers')
        if isinstance(headers, dict):
            for name in list(headers):
                if name.lower() in _DROPPED_HEADERS:
                    headers[name] = FILTERED
    return event


def sentry_options() -> dict:
    """Keyword arguments for ``sentry_sdk.init`` that keep message content out of events."""
    from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

    return {
        'send_default_pii': False,
        'max_request_body_size': 'never',
        'include_local_variables': False,
        'before_send': before_send,
        'event_scrubber': EventScrubber(denylist=[*DEFAULT_DENYLIST, *EXTRA_DENYLIST], recursive=True),
    }
