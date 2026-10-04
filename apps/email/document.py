"""React Email editor documents: walk, validate, and adapt a library design to one event.

A document is TipTap JSON: ``{"type": "doc", "content": [...]}``. Links live in
``link`` marks, ``button`` nodes and ``image`` nodes (``attrs.href``). Images
load from ``attrs.src``.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterator

from django.conf import settings

from apps.email.service import SendError
from apps.email.substitution import SubstitutionError, reject_template_tags

NODE_TYPES = {
    'doc',
    'paragraph',
    'text',
    'heading',
    'hardBreak',
    'horizontalRule',
    'bulletList',
    'orderedList',
    'listItem',
    'blockquote',
    'codeBlock',
    'table',
    'tableRow',
    'tableCell',
    'tableHeader',
    'body',
    'container',
    'section',
    'div',
    'button',
    'image',
    'previewText',
    'globalContent',
    'twoColumns',
    'threeColumns',
    'fourColumns',
    'columnsColumn',
}
MARK_TYPES = {'link', 'bold', 'italic', 'underline', 'strike', 'code', 'sup', 'uppercase', 'preservedStyle'}

ACTION_URL = 'action_url'
ASSETS_PREFIX = '{{ system.assets_url }}/'
SAMPLE_LINK = 'https://example.com'
_SAFE_HREF = ('https://', 'mailto:', 'tel:')
_UNSAFE_ATTR = re.compile(r'(?i)javascript:|vbscript:|expression\s*\(|data:text/html')
_ACTION_TOKEN = re.compile(r'\{\{\s*action_url\s*(?:\|\s*default\s*:\s*"[^"]*"\s*)?\}\}')


def empty_document() -> dict:
    return {'type': 'doc', 'content': [{'type': 'container', 'content': [{'type': 'paragraph'}]}]}


def nodes(document: dict) -> Iterator[dict]:
    stack = [document]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        yield node
        content = node.get('content')
        if isinstance(content, list):
            stack.extend(reversed(content))


def links(document: dict) -> Iterator[str]:
    """Every link target: link marks, buttons, and linked images."""
    for node in nodes(document):
        attrs = node.get('attrs') if isinstance(node.get('attrs'), dict) else {}
        if node.get('type') in {'button', 'image'} and attrs.get('href'):
            yield str(attrs['href'])
        for mark in node.get('marks') or []:
            if isinstance(mark, dict) and mark.get('type') == 'link':
                href = (mark.get('attrs') or {}).get('href')
                if href:
                    yield str(href)


def image_sources(document: dict) -> Iterator[str]:
    for node in nodes(document):
        if node.get('type') == 'image':
            yield str((node.get('attrs') or {}).get('src') or '')


def texts(document: dict) -> Iterator[str]:
    for node in nodes(document):
        if node.get('type') == 'text' and isinstance(node.get('text'), str):
            yield node['text']


def _href_allowed(href: str) -> bool:
    value = href.strip()
    if value.startswith('{{'):
        return True
    if value.lower().startswith('http://localhost') and settings.DEBUG:
        return True
    return value.lower().startswith(_SAFE_HREF)


def _src_allowed(src: str) -> bool:
    value = src.strip()
    return value.startswith(ASSETS_PREFIX) or value.lower().startswith('https://')


def _attr_strings(node: dict) -> Iterator[str]:
    for source in [node.get('attrs')] + [mark.get('attrs') for mark in node.get('marks') or [] if isinstance(mark, dict)]:
        if isinstance(source, dict):
            for value in source.values():
                if isinstance(value, str):
                    yield value


def validate_document(document) -> dict:
    """Shape, size, node types, link targets, and image sources. Raises SendError."""
    if not isinstance(document, dict) or document.get('type') != 'doc':
        raise SendError(400, 'validation_failed', {'document': ['invalid']})
    raw = json.dumps(document, ensure_ascii=False)
    if len(raw.encode('utf-8')) > settings.EMAIL_MAX_DOCUMENT_BYTES:
        raise SendError(400, 'validation_failed', {'document': ['too_large']})
    try:
        reject_template_tags(raw)
    except SubstitutionError as exc:
        raise SendError(400, 'validation_failed', {'document': [exc.code]}) from exc
    for node in nodes(document):
        if node.get('type') not in NODE_TYPES:
            raise SendError(400, 'validation_failed', {'document': ['node_not_allowed']})
        for mark in node.get('marks') or []:
            if not isinstance(mark, dict) or mark.get('type') not in MARK_TYPES:
                raise SendError(400, 'validation_failed', {'document': ['mark_not_allowed']})
        if any(_UNSAFE_ATTR.search(value) for value in _attr_strings(node)):
            raise SendError(400, 'validation_failed', {'document': ['unsafe_attribute']})
    if any(not _href_allowed(href) for href in links(document)):
        raise SendError(400, 'validation_failed', {'document': ['unsafe_link']})
    if any(src and not _src_allowed(src) for src in image_sources(document)):
        raise SendError(400, 'validation_failed', {'document': ['unsafe_image']})
    return document


def _replace_action(value: str, replacement: str) -> str:
    return _ACTION_TOKEN.sub(replacement, value)


def _strip_link(node: dict, keep) -> None:
    attrs = node.get('attrs')
    if node.get('type') in {'button', 'image'} and isinstance(attrs, dict) and attrs.get('href'):
        if not keep(str(attrs['href'])):
            attrs['href'] = None
    marks = node.get('marks')
    if isinstance(marks, list):
        kept = [
            mark
            for mark in marks
            if not (isinstance(mark, dict) and mark.get('type') == 'link' and not keep(str((mark.get('attrs') or {}).get('href') or '')))
        ]
        if kept:
            node['marks'] = kept
        else:
            node.pop('marks', None)


def adapt_to_event(document: dict, definition: dict) -> dict:
    """A copy of ``document`` with ``{{ action_url }}`` pointing at the event's link.

    Auth-lane copies keep only links the auth checks accept, so the design can be
    published as is. Other events without a link get a sample URL to replace.
    """
    link_token = definition.get('link_token') or ''
    replacement = '{{ ' + link_token + ' }}' if link_token else SAMPLE_LINK
    adapted = copy.deepcopy(document)
    for node in nodes(adapted):
        if node.get('type') == 'text' and isinstance(node.get('text'), str):
            node['text'] = _replace_action(node['text'], replacement)
        attrs = node.get('attrs')
        if isinstance(attrs, dict) and isinstance(attrs.get('href'), str):
            attrs['href'] = _replace_action(attrs['href'], replacement)
        for mark in node.get('marks') or []:
            mark_attrs = mark.get('attrs') if isinstance(mark, dict) else None
            if isinstance(mark_attrs, dict) and isinstance(mark_attrs.get('href'), str):
                mark_attrs['href'] = _replace_action(mark_attrs['href'], replacement)
    if definition.get('lane_class') == 'auth':
        from apps.email.auth_templates import auth_link_allowed

        for node in nodes(adapted):
            _strip_link(node, lambda href: auth_link_allowed(definition, href))
    return adapted
