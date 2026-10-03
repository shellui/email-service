"""Render a block document to HTML and text. Placeholders survive until send time."""

from __future__ import annotations

import hashlib
import json
import subprocess
from html import escape
from pathlib import Path

from django.conf import settings

from apps.email.substitution import escape_keeping_tokens, find_tokens, reject_template_tags
from apps.email.themes import DEFAULT_THEME, colors_for, is_theme

RENDERER_VERSION = 'shellui-email-2'


def document_tokens(document: dict, subject: str = '', preheader: str = '') -> set[str]:
    blobs = [subject, preheader, json.dumps(document, ensure_ascii=False)]
    tokens: set[str] = set()
    for blob in blobs:
        reject_template_tags(blob)
        tokens |= find_tokens(blob)
    return tokens


def _font_faces(spec: dict) -> str:
    faces = []
    for font in spec.get('fonts') or []:
        family = escape(str(font.get('family') or ''))
        url = escape(str(font.get('url') or ''))
        fmt = escape(str(font.get('format') or 'woff2'))
        weight = int(font.get('weight') or 400)
        faces.append(
            '@font-face{'
            f"font-family:'{family}';font-style:normal;font-weight:{weight};"
            f"src:url('{url}') format('{fmt}');"
            '}'
        )
    return ''.join(faces)


def _python_html(document: dict, spec: dict, colors: dict[str, str]) -> str:
    preview = escape(str(document.get('preview') or ''))
    font = spec['font']
    heading_font = spec['heading_font']
    align = spec.get('align') or 'left'
    radius = spec.get('card_radius') or '0'
    button_radius = spec.get('button_radius') or '0'
    shadow = spec.get('shadow') or 'none'
    max_width = spec.get('max_width') or '640px'
    layout = spec.get('layout') or 'card'
    border = f'1px solid {colors["border"]}' if layout in {'card', 'inset'} else '0'
    if layout == 'studio':
        border = '0'
    button_border = f'1px solid {colors["border"]}' if spec.get('button_style') == 'outline' else '0'
    button_bg = colors['primary']
    button_fg = colors['primaryForeground']
    if spec.get('button_style') == 'outline' and layout == 'serif':
        button_bg = 'transparent'
        button_fg = colors['foreground']
        button_border = f'1px solid {colors["foreground"]}'
    pad = '40px 32px' if layout != 'inset' else '28px 24px'
    inner_bg = colors['inner'] if layout == 'inset' else colors['background']
    parts = [
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<title>Shellui</title><style>{_font_faces(spec)}</style></head>',
        f'<body style="margin:0;background:{colors["muted"]};color:{colors["foreground"]};font-family:{font};">',
        '<div style="display:none;max-height:0;overflow:hidden;">' + preview + '</div>',
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{colors["muted"]};">',
        '<tr><td align="center" style="padding:32px 16px;">',
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="max-width:{max_width};background:{colors["background"]};border:{border};'
        f'border-radius:{radius};box-shadow:{shadow};">',
    ]
    if layout == 'studio':
        parts.append(
            f'<tr><td style="height:8px;background:{colors["foreground"]};font-size:0;line-height:0;">&nbsp;</td></tr>'
        )
    parts.append(
        f'<tr><td style="padding:{pad};background:{inner_bg};border-radius:{radius};text-align:{align};">'
    )
    for block in document.get('blocks') or []:
        kind = block.get('type')
        text = escape_keeping_tokens(str(block.get('text') or ''))
        if kind == 'heading':
            parts.append(
                f'<div style="font-family:{heading_font};font-size:{spec["heading_size"]};'
                f'font-weight:{spec["heading_weight"]};line-height:1.2;letter-spacing:-0.02em;'
                f'text-transform:{spec.get("heading_transform") or "none"};color:{colors["foreground"]};'
                f'margin:0 0 16px;">{text}</div>'
            )
        elif kind == 'text':
            parts.append(
                f'<div style="font-family:{font};font-size:{spec["text_size"]};line-height:1.5;'
                f'color:{colors["body"]};margin:0 0 16px;">{text}</div>'
            )
        elif kind == 'button':
            href = escape_keeping_tokens(str(block.get('href') or ''))
            parts.append(
                f'<div style="margin:8px 0 4px;"><a href="{href}" '
                f'style="display:inline-block;background:{button_bg};color:{button_fg};'
                f'text-decoration:none;font-family:{font};font-size:15px;font-weight:500;'
                f'padding:{spec.get("button_pad") or "12px 20px"};border-radius:{button_radius};'
                f'border:{button_border};box-shadow:{shadow if spec.get("button_style") == "outline" else "none"};'
                f'">{text}</a></div>'
            )
        elif kind == 'footer':
            parts.append(
                f'<div style="font-family:{font};font-size:12px;line-height:1.5;'
                f'color:{colors["mutedForeground"]};margin:12px 0 0;">{text}</div>'
            )
    parts.append('</td></tr></table></td></tr></table></body></html>')
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


def _node_render(document: dict, spec: dict, colors: dict[str, str], theme: str) -> tuple[str, str]:
    script = Path(settings.BASE_DIR) / 'renderer' / 'render.mjs'
    completed = subprocess.run(
        ['node', str(script)],
        input=json.dumps({'document': document, 'palette': colors, 'theme': theme, 'spec': spec}).encode('utf-8'),
        capture_output=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.decode('utf-8', errors='replace')[:500])
    payload = json.loads(completed.stdout.decode('utf-8'))
    return payload['html'], payload['text']


def render_document(document: dict, palette: dict | None = None, theme: str | None = None) -> tuple[str, str, str]:
    """Return html, text, renderer version. Placeholders are left intact.

    ``theme`` selects one of the five official themes. ``palette`` is an optional
    full accent override. None and {} keep the theme colors.
    """
    theme_key = theme if is_theme(theme) else DEFAULT_THEME
    spec, colors = colors_for(theme_key, palette)
    mode = getattr(settings, 'EMAIL_RENDERER', 'python')
    if mode == 'node':
        html, text = _node_render(document, spec, colors, theme_key)
        return html, text, 'react-email'
    return _python_html(document, spec, colors), _python_text(document), RENDERER_VERSION


def checksum(subject: str, html: str, text: str) -> str:
    raw = f'{subject}\n{html}\n{text}'.encode('utf-8')
    return hashlib.sha256(raw).hexdigest()
