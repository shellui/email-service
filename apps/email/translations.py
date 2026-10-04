"""Other languages of a template version: same layout, different text.

``TemplateVersion.document`` is the layout, written in the copy's language
(``EmailTemplate.language``). ``TemplateVersion.translations`` holds, for each
other language, its subject, preheader, and the content of text blocks matched
by ``attrs.textId``::

    {"fr": {"subject": "…", "preheader": "…",
            "blocks": {"k3v9q2xa": {"content": [{"type": "text", "text": "Bonjour"}], "source": "…"}}}}

A block without a translation keeps the main text. An empty subject or
preheader falls back to the main one. ``source`` is the admin's fingerprint of
the main text it was translated from, kept as is.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterator

from apps.email.catalog import LANGUAGES
from apps.email.document import nodes
from apps.email.service import SendError

INLINE_TYPES = {'text', 'hardBreak'}


def _invalid(field: str = 'translations') -> SendError:
    return SendError(400, 'validation_failed', {field: ['invalid']})


def _inline_only(content: list) -> bool:
    return all(isinstance(node, dict) and node.get('type') in INLINE_TYPES and 'content' not in node for node in content)


def clean_translations(raw, main_language: str) -> dict:
    if raw in (None, ''):
        return {}
    if not isinstance(raw, dict):
        raise _invalid()
    cleaned: dict[str, dict] = {}
    for language, entry in raw.items():
        if language not in LANGUAGES or language == main_language:
            raise SendError(400, 'language_not_available')
        if not isinstance(entry, dict):
            raise _invalid()
        blocks = entry.get('blocks') or {}
        if not isinstance(blocks, dict):
            raise _invalid()
        clean_blocks = {}
        for block_id, block in blocks.items():
            content = block.get('content') if isinstance(block, dict) else None
            if not isinstance(content, list) or not _inline_only(content):
                raise _invalid()
            clean_blocks[str(block_id)[:32]] = {'content': content, 'source': str(block.get('source') or '')[:32]}
        cleaned[language] = {
            'subject': str(entry.get('subject') or '')[:255],
            'preheader': str(entry.get('preheader') or '')[:255],
            'blocks': clean_blocks,
        }
    return cleaned


def translated_inbox(translations) -> dict:
    """Subjects and preheaders only, for a new design whose blocks match nothing."""
    if not isinstance(translations, dict):
        return {}
    return {
        language: {'subject': entry.get('subject') or '', 'preheader': entry.get('preheader') or '', 'blocks': {}}
        for language, entry in translations.items()
        if isinstance(entry, dict)
    }


def localize(document: dict, blocks: dict) -> dict:
    """``document`` with each translated block's content swapped in."""
    localized = copy.deepcopy(document)
    if not blocks:
        return localized
    for node in nodes(localized):
        attrs = node.get('attrs')
        block_id = attrs.get('textId') if isinstance(attrs, dict) else None
        if block_id and block_id in blocks:
            content = copy.deepcopy(blocks[block_id]['content'])
            if content:
                node['content'] = content
            else:
                node.pop('content', None)
    return localized


def variants(
    document: dict,
    translations: dict,
    fallback: Callable[[str], tuple[str, str]],
) -> Iterator[tuple[str, dict, str, str]]:
    """``(language, document, subject, preheader)`` per translation. ``fallback(language)`` fills an empty subject or preheader."""
    for language, entry in (translations or {}).items():
        subject, preheader = fallback(language)
        yield (
            language,
            localize(document, entry.get('blocks') or {}),
            entry.get('subject') or subject,
            entry.get('preheader') or preheader,
        )
