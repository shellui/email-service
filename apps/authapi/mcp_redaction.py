"""Extra MCP response redaction for email-service (without forking ``shellui_mcp``)."""

from __future__ import annotations

from typing import Any

from shellui_mcp import masking as shellui_masking

_INSTALLED = False
_ORIGINAL_REDACT = shellui_masking.redact_mapping


def _redact_hint_keys(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).endswith('_hint'):
                out[key] = '[redacted]'
            else:
                out[key] = _redact_hint_keys(item)
        return out
    if isinstance(value, list):
        return [_redact_hint_keys(item) for item in value]
    return value


def _redact_mapping(data: dict[str, Any] | None, *, read_sensitive: bool) -> dict[str, Any]:
    base = _ORIGINAL_REDACT(data, read_sensitive=read_sensitive)
    return _redact_hint_keys(base)


def install_mcp_redaction_hooks() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    shellui_masking.redact_mapping = _redact_mapping
    _INSTALLED = True
