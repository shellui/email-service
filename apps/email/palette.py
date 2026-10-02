"""Theme colors stored on a template version and applied at render time."""

from __future__ import annotations

import re

# Same keys the admin preview sends. Empty means the Shellui palette below.
PALETTE_KEYS = (
    'background',
    'foreground',
    'muted',
    'mutedForeground',
    'primary',
    'primaryForeground',
    'border',
)

DEFAULT_PALETTE = {
    'background': '#ffffff',
    'foreground': '#1a1408',
    'muted': '#f6f4ef',
    'mutedForeground': '#6b645b',
    'primary': '#e3a512',
    'primaryForeground': '#1a1408',
    'border': '#e7e0d4',
}

_HEX = re.compile(r'^#[0-9A-Fa-f]{6}$')


class PaletteError(Exception):
    """theme_palette is not a complete set of #RRGGBB colors."""


def resolve_palette(raw) -> dict[str, str]:
    """Colors for the renderer. None and {} use the Shellui palette."""
    if raw is None or raw == {}:
        return dict(DEFAULT_PALETTE)
    if not isinstance(raw, dict) or set(raw) != set(PALETTE_KEYS):
        raise PaletteError
    cleaned: dict[str, str] = {}
    for key in PALETTE_KEYS:
        value = raw[key]
        if not isinstance(value, str) or _HEX.fullmatch(value.strip()) is None:
            raise PaletteError
        cleaned[key] = value.strip().lower()
    return cleaned


def stored_palette(raw) -> dict[str, str]:
    """Value to persist. None and {} stay empty so the default palette applies later."""
    if raw is None or raw == {}:
        return {}
    return resolve_palette(raw)
