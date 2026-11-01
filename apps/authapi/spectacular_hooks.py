"""drf-spectacular hooks for Shellui MCP."""

from __future__ import annotations

from apps.authapi.mcp_openapi import MCP_EXCLUDED_PATH_PREFIXES


def preprocessing_exclude_internal_endpoints(endpoints):
    """Drop staff-only and internal routes from the published schema."""
    filtered = []
    for path, path_regex, method, callback in endpoints:
        if any(str(path).startswith(prefix) for prefix in MCP_EXCLUDED_PATH_PREFIXES):
            continue
        filtered.append((path, path_regex, method, callback))
    return filtered
