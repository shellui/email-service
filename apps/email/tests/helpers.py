from __future__ import annotations

from apps.email.models import LibraryTemplate


def library_content(key: str = 'barebone.text-only') -> dict:
    return {'library_id': LibraryTemplate.objects.get(key=key).pk}


def paragraphs(*items) -> dict:
    """An editor document with one paragraph per item. An item is text or ``(text, href)``."""
    content = []
    for item in items:
        text, href = item if isinstance(item, tuple) else (item, None)
        node = {'type': 'text', 'text': text}
        if href:
            node['marks'] = [{'type': 'link', 'attrs': {'href': href}}]
        content.append({'type': 'paragraph', 'content': [node]})
    return {'type': 'doc', 'content': content}
