"""Block document helpers shared by the renderers and the auth-lane checks.

A text-like block (heading, text, footer, list item) carries plain ``text`` and,
optionally, ``content``: a list of inline runs
``{"text", "bold"?, "italic"?, "underline"?, "href"?}``. When ``content`` is a
list it is what gets rendered, so every check must read runs, not ``text``.
"""

from __future__ import annotations

from collections.abc import Iterator

TEXT_BLOCKS = {'heading', 'text', 'footer'}
MARKS = ('bold', 'italic', 'underline')
_SAFE_PREFIXES = ('https://', 'http://', 'mailto:', 'tel:')


def block_runs(block: dict) -> list[dict]:
    """Inline runs of a heading, text, footer, or list item."""
    content = block.get('content')
    if isinstance(content, list):
        return [run for run in content if isinstance(run, dict) and isinstance(run.get('text'), str)]
    return [{'text': str(block.get('text') or '')}]


def list_items(block: dict) -> list[list[dict]]:
    items = block.get('items')
    if not isinstance(items, list):
        return []
    return [block_runs(item) for item in items if isinstance(item, dict)]


def document_runs(document: dict) -> Iterator[dict]:
    """Every inline run in the document, list items included."""
    for block in document.get('blocks') or []:
        if not isinstance(block, dict):
            continue
        kind = block.get('type')
        if kind in TEXT_BLOCKS:
            yield from block_runs(block)
        elif kind == 'list':
            for runs in list_items(block):
                yield from runs


def safe_href(value: object) -> str | None:
    """An inline link target, or None when the link should render as plain text."""
    href = str(value or '').strip()
    if not href:
        return None
    if href.startswith('{{'):
        return href
    if href.lower().startswith(_SAFE_PREFIXES):
        return href
    return None


def runs_text(runs: list[dict]) -> str:
    parts = []
    for run in runs:
        text = str(run.get('text') or '')
        href = safe_href(run.get('href'))
        if href and href != text.strip():
            text = f'{text} ({href})'
        parts.append(text)
    return ''.join(parts).strip()
