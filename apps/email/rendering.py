"""Compose editor documents to HTML and text with renderer/compose.mjs. Placeholders survive until send time."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from django.conf import settings

from apps.email.service import SendError
from apps.email.substitution import find_tokens, reject_template_tags
from apps.email.theming import resolve

RENDERER_VERSION = 'react-email-editor-1'

_cache: dict[str, tuple[str, str]] = {}
_CACHE_LIMIT = 256


def document_tokens(document: dict, subject: str = '', preheader: str = '') -> set[str]:
    blobs = [subject, preheader, json.dumps(document, ensure_ascii=False)]
    tokens: set[str] = set()
    for blob in blobs:
        reject_template_tags(blob)
        tokens |= find_tokens(blob)
    return tokens


def _cache_key(item: dict) -> str:
    raw = json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _run_node(items: list[dict]) -> list[tuple[str, str]]:
    script = Path(settings.BASE_DIR) / 'renderer' / 'compose.mjs'
    try:
        completed = subprocess.run(
            [settings.EMAIL_NODE_BINARY, str(script)],
            input=json.dumps({'items': items}).encode('utf-8'),
            capture_output=True,
            check=False,
            timeout=settings.EMAIL_COMPOSE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SendError(503, 'renderer_unavailable') from exc
    if completed.returncode != 0:
        raise SendError(400, 'validation_failed', {'document': ['render_failed']})
    payload = json.loads(completed.stdout.decode('utf-8'))
    return [(row['html'], row['text']) for row in payload['items']]


def compose_many(items: list[dict]) -> list[tuple[str, str]]:
    """``items`` are ``{document, head, preheader, colors?}``. Returns ``(html, text)`` in the same order.

    ``colors`` are the theme's, see ``apps.email.theming``. Without them a design keeps its own.
    """
    items = [
        {'document': resolve(item['document'], item.get('colors')), 'head': item['head'], 'preheader': item['preheader']}
        for item in items
    ]
    keys = [_cache_key(item) for item in items]
    pending = [index for index, key in enumerate(keys) if key not in _cache]
    if pending:
        rendered = _run_node([items[index] for index in pending])
        if len(_cache) + len(rendered) > _CACHE_LIMIT:
            _cache.clear()
        for index, result in zip(pending, rendered):
            _cache[keys[index]] = result
    return [_cache[key] for key in keys]


def compose(document: dict, *, head: str = '', preheader: str = '', colors: dict | None = None) -> tuple[str, str]:
    return compose_many([{'document': document, 'head': head, 'preheader': preheader, 'colors': colors}])[0]


def checksum(subject: str, html: str, text: str) -> str:
    raw = f'{subject}\n{html}\n{text}'.encode('utf-8')
    return hashlib.sha256(raw).hexdigest()
