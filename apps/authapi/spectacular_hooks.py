"""drf-spectacular hooks for Shellui MCP."""

from __future__ import annotations

from typing import Any

from apps.authapi.mcp_openapi import MCP_EXCLUDED_PATH_PREFIXES, mcp_extension
from shellui_django.mcp.decorators import MCP_TOOL_TAG

MCP_DESTRUCTIVE_TAG = 'mcp-destructive'


def preprocessing_exclude_internal_endpoints(endpoints):
    """Drop staff-only and internal routes from the published schema."""
    filtered = []
    for path, path_regex, method, callback in endpoints:
        if any(str(path).startswith(prefix) for prefix in MCP_EXCLUDED_PATH_PREFIXES):
            continue
        filtered.append((path, path_regex, method, callback))
    return filtered


def postprocess_mcp_tool_tags(result: dict[str, Any], **kwargs) -> dict[str, Any]:
    """Expose only ``mcp_tool``-registered operations as MCP tools (``mcp-tools`` tag)."""
    inline_parameter_descriptions(result)
    return mcp_schema_for_server(result)


# Path parameter descriptions keyed by the URL converter name. ``rule_id`` differs by operation.
_PATH_PARAMETER_DESCRIPTIONS: dict[str, str] = {
    'message_id': 'Public message id (for example msg_01h2x3y4z5).',
    'template_id': 'Template copy id.',
    'library_id': 'Library design id.',
    'version_number': 'Template version number.',
    'broadcast_id': 'Broadcast id.',
    'newsletter_id': 'Newsletter list id.',
    'subscriber_id': 'Newsletter subscriber id.',
    'suppression_id': 'Suppression id.',
    'delivery_id': 'Shellui Actions webhook delivery id.',
    'event_id': 'Domain event log id.',
}


def _path_parameter_description(operation_id: str, name: str) -> str:
    if name == 'rule_id' and 'actions' in operation_id:
        return 'Shellui Actions webhook rule id.'
    if name == 'rule_id':
        return 'Email rule id.'
    return _PATH_PARAMETER_DESCRIPTIONS.get(name, '')


def inline_parameter_descriptions(result: dict[str, Any]) -> dict[str, Any]:
    """Copy parameter descriptions into ``schema.description``.

    shellui-django 0.5.1 builds tool arguments from the parameter schema only, so a
    description that lives on the parameter object never reaches the agent.
    """
    for path_item in (result.get('paths') or {}).values():
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            operation_id = str(operation.get('operationId') or '')
            for param in operation.get('parameters') or []:
                if not isinstance(param, dict):
                    continue
                description = str(param.get('description') or '').strip()
                if param.get('in') == 'path' and not description:
                    description = _path_parameter_description(operation_id, str(param.get('name') or ''))
                    if description:
                        param['description'] = description
                schema = param.get('schema')
                if description and isinstance(schema, dict) and not schema.get('description'):
                    schema['description'] = description
    return result


def mcp_schema_for_server(schema: dict[str, Any]) -> dict[str, Any]:
    paths = schema.get('paths') or {}
    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            ext = mcp_extension(operation)
            if ext is None:
                continue
            tags = list(operation.get('tags') or [])
            if MCP_TOOL_TAG not in tags:
                tags.append(MCP_TOOL_TAG)
            if ext.get('destructive') and MCP_DESTRUCTIVE_TAG not in tags:
                tags.append(MCP_DESTRUCTIVE_TAG)
            operation['tags'] = tags
    return schema
