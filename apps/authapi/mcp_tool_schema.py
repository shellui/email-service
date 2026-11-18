"""Flat MCP tool arguments for agents.

shellui-django 0.5.1 wraps every tool in ``method``, ``path``, ``query``, ``body``,
and ``path_params``. Those transport fields are not arguments an agent should fill.
This module publishes the same operations as one flat object and maps a flat call
back to that envelope before the pinned library validates it.
"""

from __future__ import annotations

from typing import Any

from shellui_django.mcp.openapi_tools import _client_prop_schema, tools_from_schema

# HTTP envelope fields. A domain field named ``url`` (webhook target) is not one of these.
TRANSPORT_ARGUMENT_NAMES = frozenset({'method', 'path', 'query', 'body', 'path_params'})

_HTTP_METHODS = frozenset({'get', 'post', 'put', 'patch', 'delete'})


def _operations(schema: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for path, path_item in (schema.get('paths') or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            found[(str(path), method.upper())] = operation
    return found


def _path_property(operation: dict[str, Any], name: str, root: dict[str, Any]) -> dict[str, Any]:
    for param in operation.get('parameters') or []:
        if not isinstance(param, dict) or param.get('in') != 'path' or str(param.get('name') or '') != name:
            continue
        raw = param.get('schema') if isinstance(param.get('schema'), dict) else {'type': 'string'}
        prop = _client_prop_schema(raw, root)
        description = str(param.get('description') or raw.get('description') or '').strip()
        if description and 'description' not in prop:
            prop['description'] = description
        return prop
    return {'type': 'string', 'description': name.replace('_', ' ')}


def flatten_mcp_tool(tool: dict[str, Any], operation: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    """One tool: flat arguments, no transport fields, MCP risk annotations."""
    meta = tool.get('_meta') or {}
    wrapped = (tool.get('inputSchema') or {}).get('properties') or {}
    flat: dict[str, Any] = {}
    required: list[str] = []

    def _take(source: dict[str, Any] | None, *, include_required: bool) -> None:
        if not isinstance(source, dict):
            return
        for key, value in (source.get('properties') or {}).items():
            name = str(key)
            if name in flat:
                raise ValueError(f'{tool.get("name")} repeats argument {name}')
            if name in TRANSPORT_ARGUMENT_NAMES:
                raise ValueError(f'{tool.get("name")} uses transport argument {name}')
            flat[name] = value
        if include_required:
            for key in source.get('required') or []:
                name = str(key)
                if name not in required:
                    required.append(name)

    for name in meta.get('path_params') or []:
        key = str(name)
        if key in TRANSPORT_ARGUMENT_NAMES:
            raise ValueError(f'{tool.get("name")} uses transport argument {key}')
        flat[key] = _path_property(operation, key, root)
        required.append(key)
    _take(wrapped.get('query'), include_required=True)
    _take(wrapped.get('body'), include_required=True)

    description = str(operation.get('description') or tool.get('description') or '').strip()
    return {
        'name': tool['name'],
        'description': description,
        'inputSchema': {
            'type': 'object',
            'properties': flat,
            'required': required,
            'additionalProperties': False,
        },
        'annotations': {
            'readOnlyHint': bool(meta.get('read_only')),
            'destructiveHint': bool(meta.get('destructive')),
        },
        '_meta': meta,
    }


def published_mcp_tools(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Tools an agent sees on ``tools/list``, including ``_meta`` for the call handler."""
    operations = _operations(schema)
    published: list[dict[str, Any]] = []
    for tool in tools_from_schema(schema):
        meta = tool.get('_meta') or {}
        operation = operations.get((str(meta.get('path') or ''), str(meta.get('method') or '').upper()))
        if operation is None:
            raise ValueError(f'MCP tool {tool.get("name")} has no OpenAPI operation')
        published.append(flatten_mcp_tool(tool, operation, schema))
    return published


def public_mcp_tools(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """``tools/list`` payload. Keeps registry ``_meta`` for the AI gateway; drops other ``_`` keys."""
    rows: list[dict[str, Any]] = []
    for tool in published_mcp_tools(schema):
        row = {key: value for key, value in tool.items() if not str(key).startswith('_')}
        meta = tool.get('_meta')
        if isinstance(meta, dict):
            row['_meta'] = meta
        rows.append(row)
    return rows


def coerce_mcp_arguments(arguments: dict[str, Any], tool: dict[str, Any]) -> dict[str, Any]:
    """Accept flat arguments, and the legacy method/path envelope the pinned library expects."""
    meta = tool.get('_meta') or {}
    if not isinstance(arguments, dict):
        return {}
    legacy = {'path', 'method', 'path_params', 'query', 'body'}
    if legacy & set(arguments):
        coerced = dict(arguments)
        coerced.setdefault('path', meta.get('path'))
        coerced.setdefault('method', meta.get('method'))
        return coerced

    path_names = [str(name) for name in (meta.get('path_params') or [])]
    query_names = {str(name) for name in (meta.get('query_fields') or [])}
    body_names = {str(name) for name in (meta.get('body_fields') or [])}
    known = set(path_names) | query_names | body_names
    path_params = {name: arguments[name] for name in path_names if name in arguments}
    query = {key: value for key, value in arguments.items() if key in query_names}
    body = {key: value for key, value in arguments.items() if key in body_names}
    for key, value in arguments.items():
        if key not in known:
            body[key] = value
    coerced: dict[str, Any] = {'path': meta.get('path'), 'method': meta.get('method')}
    if path_names:
        coerced['path_params'] = path_params
    if query:
        coerced['query'] = query
    if body or meta.get('body_required'):
        coerced['body'] = body
    return coerced
