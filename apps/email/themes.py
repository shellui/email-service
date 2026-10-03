"""Official email themes. Layout data lives in ``renderer/themes/themes.json``."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from apps.email.palette import PALETTE_KEYS, resolve_palette

DEFAULT_THEME = 'barebone'
_THEMES_PATH = Path(__file__).resolve().parents[2] / 'renderer' / 'themes' / 'themes.json'


@lru_cache(maxsize=1)
def theme_catalog() -> dict[str, dict]:
    return json.loads(_THEMES_PATH.read_text(encoding='utf-8'))


def theme_keys() -> tuple[str, ...]:
    return tuple(theme_catalog().keys())


def get_theme(key: str | None) -> dict:
    catalog = theme_catalog()
    if not key or key not in catalog:
        raise KeyError(key or '')
    return catalog[key]


def is_theme(key: str | None) -> bool:
    return bool(key) and key in theme_catalog()


def colors_for(theme_key: str | None, palette: dict | None) -> tuple[dict, dict[str, str]]:
    """Theme spec plus the seven palette slots, with an optional full override."""
    key = theme_key if is_theme(theme_key) else DEFAULT_THEME
    spec = theme_catalog()[key]
    colors = {
        'background': spec['card'],
        'foreground': spec['foreground'],
        'muted': spec['page'],
        'mutedForeground': spec['muted'],
        'primary': spec['button_bg'],
        'primaryForeground': spec['button_fg'],
        'border': spec['border'],
        'body': spec['body'],
        'inner': spec['inner'],
    }
    if palette:
        override = resolve_palette(palette)
        for name in PALETTE_KEYS:
            colors[name] = override[name]
        colors['body'] = override['foreground']
        colors['inner'] = override['muted']
    return spec, colors

