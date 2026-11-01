"""AutoSchema that refuses ``x-mcp`` on internal routes."""

from __future__ import annotations

from drf_spectacular.openapi import AutoSchema

from apps.authapi.mcp_openapi import MCP_EXCLUDED_PATH_PREFIXES, MCP_OPERATION_FLAGS, mcp_tool


class McpAutoSchema(AutoSchema):
    def get_operation(self, path, path_regex, path_prefix, method, registry):
        operation = super().get_operation(path, path_regex, path_prefix, method, registry)
        if not operation:
            return operation
        if operation.get('x-mcp') and any(str(path).startswith(prefix) for prefix in MCP_EXCLUDED_PATH_PREFIXES):
            raise AssertionError(f'x-mcp is not allowed on excluded path: {path}')
        op_id = operation.get('operationId')
        if op_id in MCP_OPERATION_FLAGS and 'x-mcp' not in operation:
            operation['x-mcp'] = dict(MCP_OPERATION_FLAGS[op_id])
        operation.pop('extensions', None)
        return operation


def admin_mcp(*, read_only: bool, destructive: bool = False, sensitive: bool = False) -> dict:
    """Alias for ``mcp_tool`` in view modules."""
    return mcp_tool(read_only=read_only, destructive=destructive, sensitive=sensitive)
