"""Email themes: a library design's colors become roles that a Shellui theme can repaint.

Library designs store colors inline, such as ``color:rgb(20,23,30)``. Tokenizing
rewrites each known color of the design's set to ``var(--email-<role>,<color>)``,
so the editor canvas repaints live by setting CSS variables. Composing always
resolves the variables, to the theme's colors or back to the design's own, so
sent HTML never carries ``var()``.

A theme is ``{"name": "…", "label": "…", "colors": {"<role>": "#rrggbb"}}``,
stored on the version (and on company library templates). ``{}`` keeps the
design's own colors.
"""

from __future__ import annotations

import re

from apps.email.service import SendError

ROLES = (
    'background',
    'foreground',
    'card',
    'muted',
    'muted_foreground',
    'primary',
    'primary_foreground',
    'border',
)

_BG = 'background-color'
_FG = 'color'
_BORDER = 'border-color'

# (property, color) to role, per set. The outer page cell is always ``background``.
SET_ROLES: dict[str, dict[tuple[str, str], str]] = {
    'barebone': {
        (_FG, 'rgb(20,23,30)'): 'foreground',
        (_FG, 'rgb(67,69,75)'): 'foreground',
        (_FG, 'rgb(123,125,129)'): 'muted_foreground',
        (_FG, 'rgb(255,255,255)'): 'primary_foreground',
        (_BG, 'rgb(243,244,246)'): 'muted',
        (_BG, 'rgb(255,255,255)'): 'card',
        (_BG, 'rgb(20,23,30)'): 'primary',
        (_BG, 'rgb(0,0,0)'): 'primary',
        (_BORDER, 'rgb(228,228,231)'): 'border',
    },
    'matte': {
        (_FG, 'rgb(16,59,5)'): 'foreground',
        (_FG, 'rgb(25,74,7)'): 'foreground',
        (_FG, 'rgb(134,156,127)'): 'muted_foreground',
        (_FG, 'rgb(251,255,249)'): 'primary_foreground',
        (_BG, 'rgb(251,252,251)'): 'background',
        (_BG, 'rgb(255,255,255)'): 'card',
        (_BG, 'rgb(16,59,5)'): 'primary',
        (_BG, 'rgb(237,240,233)'): 'muted',
        (_BG, 'rgb(211,217,203)'): 'border',
        (_BORDER, 'rgb(216,225,212)'): 'border',
    },
    'protocol': {
        (_FG, 'rgb(255,255,255)'): 'foreground',
        (_FG, 'rgb(196,196,196)'): 'foreground',
        (_FG, 'rgb(129,129,129)'): 'muted_foreground',
        (_FG, 'rgb(74,74,74)'): 'muted_foreground',
        (_FG, 'rgb(19,19,19)'): 'primary_foreground',
        (_BG, 'rgb(33,33,33)'): 'background',
        (_BG, 'rgb(19,19,19)'): 'card',
        (_BG, 'rgb(255,255,255)'): 'primary',
        (_BORDER, 'rgb(43,43,43)'): 'border',
    },
    'arcane': {
        (_FG, 'rgb(252,243,237)'): 'foreground',
        (_FG, 'rgb(239,225,216)'): 'foreground',
        (_FG, 'rgb(48,6,16)'): 'foreground',
        (_FG, 'rgb(176,153,150)'): 'muted_foreground',
        (_BG, 'rgb(255,255,255)'): 'background',
        (_BG, 'rgb(48,6,16)'): 'card',
        (_BG, 'rgb(67,29,38)'): 'muted',
        (_BG, 'rgb(249,249,237)'): 'muted',
        (_BORDER, 'rgb(61,21,29)'): 'border',
        (_BORDER, 'rgb(48,6,16)'): 'card',
    },
    'studio': {
        (_FG, 'rgb(51,44,44)'): 'foreground',
        (_FG, 'rgb(114,106,106)'): 'foreground',
        (_FG, 'rgb(31,34,34)'): 'foreground',
        (_FG, 'rgb(162,169,170)'): 'muted_foreground',
        (_FG, 'rgb(220,225,228)'): 'primary_foreground',
        (_BG, 'rgb(255,255,255)'): 'card',
        (_BG, 'rgb(246,246,246)'): 'muted',
        (_BG, 'rgb(240,240,240)'): 'muted',
        (_BG, 'rgb(232,232,232)'): 'border',
        (_BG, 'rgb(51,44,44)'): 'primary',
        (_BORDER, 'rgb(232,233,233)'): 'border',
        (_BORDER, 'rgb(240,240,240)'): 'border',
    },
}

_DECLARATION = re.compile(r'(?<![\w-])(color|background-color|border-color)(\s*:\s*)(rgb\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\))')
_VARIABLE = re.compile(r'var\(--email-([a-z-]+)\s*,\s*([^()]*(?:\([^()]*\))?[^()]*)\)')
_HEX = re.compile(r'^#[0-9a-f]{6}$')
_NAME = re.compile(r'^[\w.-]{1,64}$')


def css_variable(role: str) -> str:
    return '--email-' + role.replace('_', '-')


def _tokenize_style(style: str, roles: dict, node_type: str | None) -> str:
    def replace(match: re.Match) -> str:
        prop, colon, color = match.group(1), match.group(2), match.group(3)
        role = roles.get((prop, re.sub(r'\s+', '', color)))
        if role is None:
            return match.group(0)
        if node_type == 'tableCell' and prop == _BG:
            role = 'background'
        return f'{prop}{colon}var({css_variable(role)},{color})'

    return _DECLARATION.sub(replace, style)


def tokenize(value, set_key: str):
    """``value`` (a document, translations, or any part of one) with the set's colors as theme roles. Edits in place."""
    roles = SET_ROLES.get(set_key)
    if not roles:
        return value
    stack = [(value, None)]
    while stack:
        item, node_type = stack.pop()
        if isinstance(item, list):
            stack.extend((child, None) for child in item)
        elif isinstance(item, dict):
            attrs = item.get('attrs')
            if isinstance(attrs, dict) and isinstance(attrs.get('style'), str):
                attrs['style'] = _tokenize_style(attrs['style'], roles, item.get('type'))
            stack.extend((child, None) for key, child in item.items() if key != 'attrs')
    return value


def resolve(value, colors: dict | None):
    """A copy of ``value`` with every theme variable replaced by the theme's color, or the design's own."""
    colors = colors or {}

    def replace(match: re.Match) -> str:
        return colors.get(match.group(1).replace('-', '_')) or match.group(2).strip()

    if isinstance(value, str):
        return _VARIABLE.sub(replace, value) if 'var(--email-' in value else value
    if isinstance(value, list):
        return [resolve(child, colors) for child in value]
    if isinstance(value, dict):
        return {key: resolve(child, colors) for key, child in value.items()}
    return value


def theme_colors(theme) -> dict:
    return (theme or {}).get('colors') or {}


def clean_theme(raw) -> dict:
    """``{}`` for the design's own colors, or a theme with a name, a label, and hex colors for known roles."""
    if raw in (None, '', {}):
        return {}
    if not isinstance(raw, dict):
        raise SendError(400, 'validation_failed', {'theme': ['invalid']})
    name = str(raw.get('name') or '')
    colors = raw.get('colors')
    if not _NAME.match(name) or not isinstance(colors, dict) or not colors:
        raise SendError(400, 'validation_failed', {'theme': ['invalid']})
    cleaned = {}
    for role, color in colors.items():
        value = str(color or '').strip().lower()
        if role not in ROLES or not _HEX.match(value):
            raise SendError(400, 'validation_failed', {'theme': ['invalid']})
        cleaned[role] = value
    return {'name': name, 'label': str(raw.get('label') or name).strip()[:80], 'colors': cleaned}
