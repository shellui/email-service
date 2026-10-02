"""Render a block document to HTML and text. Placeholders survive until send time."""

from __future__ import annotations

import hashlib
import json
import subprocess
from html import escape
from pathlib import Path

from django.conf import settings

from apps.email.palette import resolve_palette
from apps.email.substitution import escape_keeping_tokens, find_tokens, reject_template_tags

RENDERER_VERSION = 'shellui-email-1'


def document_tokens(document: dict, subject: str = '', preheader: str = '') -> set[str]:
    blobs = [subject, preheader, json.dumps(document, ensure_ascii=False)]
    tokens: set[str] = set()
    for blob in blobs:
        reject_template_tags(blob)
        tokens |= find_tokens(blob)
    return tokens


def _python_html(document: dict, colors: dict[str, str]) -> str:
    preview = escape(str(document.get('preview') or ''))
    parts = [
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<title>Shellui</title></head>',
        f'<body style="margin:0;background:{colors["muted"]};color:{colors["foreground"]};font-family:Georgia,serif;">',
        '<div style="display:none;max-height:0;overflow:hidden;">' + preview + '</div>',
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{colors["muted"]};">',
        '<tr><td align="center" style="padding:32px 16px;">',
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="max-width:560px;background:{colors["background"]};border:1px solid {colors["border"]};border-radius:12px;">',
        '<tr><td style="padding:28px 32px 8px;font-family:Georgia,serif;font-size:14px;letter-spacing:0.08em;'
        f'text-transform:uppercase;color:{colors["primary"]};">Shellui</td></tr>',
    ]
    for block in document.get('blocks') or []:
        kind = block.get('type')
        text = escape_keeping_tokens(str(block.get('text') or ''))
        if kind == 'heading':
            parts.append(
                '<tr><td style="padding:8px 32px 12px;font-family:Georgia,serif;font-size:26px;'
                f'line-height:1.3;color:{colors["foreground"]};">{text}</td></tr>'
            )
        elif kind == 'text':
            parts.append(
                '<tr><td style="padding:0 32px 14px;font-family:Georgia,serif;font-size:16px;'
                f'line-height:1.55;color:{colors["foreground"]};">{text}</td></tr>'
            )
        elif kind == 'button':
            href = escape_keeping_tokens(str(block.get('href') or ''))
            parts.append(
                '<tr><td style="padding:8px 32px 20px;">'
                f'<a href="{href}" style="display:inline-block;background:{colors["primary"]};color:{colors["primaryForeground"]};'
                'text-decoration:none;font-family:Georgia,serif;font-size:16px;font-weight:700;'
                f'padding:12px 22px;border-radius:8px;">{text}</a></td></tr>'
            )
        elif kind == 'footer':
            parts.append(
                '<tr><td style="padding:8px 32px 28px;font-family:Georgia,serif;font-size:12px;'
                f'line-height:1.5;color:{colors["mutedForeground"]};">{text}</td></tr>'
            )
    parts.append('</table></td></tr></table></body></html>')
    return ''.join(parts)


def _python_text(document: dict) -> str:
    lines = []
    preview = str(document.get('preview') or '').strip()
    if preview:
        lines.append(preview)
        lines.append('')
    for block in document.get('blocks') or []:
        kind = block.get('type')
        text = str(block.get('text') or '').strip()
        if kind == 'heading':
            lines.append(text)
            lines.append('')
        elif kind in {'text', 'footer'}:
            lines.append(text)
            lines.append('')
        elif kind == 'button':
            href = str(block.get('href') or '').strip()
            lines.append(f'{text}: {href}'.strip())
            lines.append('')
    return '\n'.join(lines).strip() + '\n'


def _node_render(document: dict, colors: dict[str, str]) -> tuple[str, str]:
    script = Path(settings.BASE_DIR) / 'renderer' / 'render.mjs'
    completed = subprocess.run(
        ['node', str(script)],
        input=json.dumps({'document': document, 'palette': colors}).encode('utf-8'),
        capture_output=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.decode('utf-8', errors='replace')[:500])
    payload = json.loads(completed.stdout.decode('utf-8'))
    return payload['html'], payload['text']


def render_document(document: dict, palette: dict | None = None) -> tuple[str, str, str]:
    """Return html, text, renderer version. Placeholders are left intact.

    ``palette`` is the stored theme palette. None and {} use the Shellui colors.
    """
    colors = resolve_palette(palette)
    mode = getattr(settings, 'EMAIL_RENDERER', 'python')
    if mode == 'node':
        html, text = _node_render(document, colors)
        return html, text, 'react-email'
    return _python_html(document, colors), _python_text(document), RENDERER_VERSION


def checksum(subject: str, html: str, text: str) -> str:
    raw = f'{subject}\n{html}\n{text}'.encode('utf-8')
    return hashlib.sha256(raw).hexdigest()
