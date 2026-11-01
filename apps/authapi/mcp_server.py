"""email-service MCP server (OpenAPI path templates need ``path_params``)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from django.http import HttpRequest, JsonResponse
from shellui_mcp.server import McpServer
from shellui_mcp.write_gate import decide_write, is_destructive_operation

from shellui_mcp import masking as shellui_masking
from shellui_mcp import openapi_tools as shellui_openapi_tools

_PATH_PARAMS_PATCHED = False


def materialize_openapi_path(template_path: str, arguments: dict[str, Any]) -> str:
    """Replace ``{param}`` segments using ``arguments['path_params']``."""
    params = arguments.get('path_params') or {}
    if not isinstance(params, dict):
        raise ValueError('path_params must be an object.')
    concrete = template_path
    for key, value in params.items():
        placeholder = '{' + str(key) + '}'
        if placeholder not in concrete:
            raise ValueError(f'Unknown path parameter: {key}')
        concrete = concrete.replace(placeholder, str(value))
    if '{' in concrete or '}' in concrete:
        raise ValueError('Missing path parameters for this tool.')
    return concrete


def install_openapi_path_params_on_tools() -> None:
    global _PATH_PARAMS_PATCHED
    if _PATH_PARAMS_PATCHED:
        return
    original = shellui_openapi_tools.tools_from_schema

    def tools_from_schema_with_path_params(schema: dict[str, Any]) -> list[dict[str, Any]]:
        tools = original(schema)
        for tool in tools:
            meta_path = str((tool.get('_meta') or {}).get('path') or '')
            if '{' not in meta_path:
                continue
            props = tool.setdefault('inputSchema', {}).setdefault('properties', {})
            props['path_params'] = {
                'type': 'object',
                'additionalProperties': {'type': 'string'},
                'description': 'Values for path placeholders such as template_id.',
            }
        return tools

    shellui_openapi_tools.tools_from_schema = tools_from_schema_with_path_params
    # ``McpServer`` imports ``tools_from_schema`` by name; keep that reference in sync.
    import shellui_mcp.server as shellui_server

    shellui_server.tools_from_schema = tools_from_schema_with_path_params
    _PATH_PARAMS_PATCHED = True


class EmailMcpServer(McpServer):
    def _tools_call(
        self,
        request: HttpRequest,
        req_id: Any,
        params: dict[str, Any],
        agent_claims: dict[str, Any],
    ) -> JsonResponse:
        tool_name = str(params.get('name') or '')
        arguments = params.get('arguments') or {}
        schema = self.schema_provider()
        tools = shellui_openapi_tools.tools_from_schema(schema)
        tool = next((t for t in tools if t['name'] == tool_name), None)
        if tool is None:
            return JsonResponse(self._error(req_id, -32602, 'Unknown tool'))
        meta = tool.get('_meta') or {}
        path = str(arguments.get('path') or meta.get('path') or '')
        method = str(arguments.get('method') or meta.get('method') or 'GET').upper()
        if path != meta.get('path') or method != meta.get('method'):
            return JsonResponse(self._error(req_id, -32602, 'Tool path mismatch'))
        try:
            dispatch_path = materialize_openapi_path(path, arguments) if '{' in path else path
        except ValueError as exc:
            return JsonResponse(
                self._result(req_id, {'content': [{'type': 'text', 'text': str(exc)}], 'isError': True})
            )

        access = str(agent_claims.get('mcp_access') or 'read_only')
        read_level = str(agent_claims.get('mcp_read') or 'safe')
        destructive = is_destructive_operation(self._operation(schema, path, method))
        decision = decide_write(
            method=method,
            access_mode=access,
            destructive=destructive,
            allow_destructive_without_asking=bool(agent_claims.get('mcp_destructive')),
        )
        if decision.needs_approval:
            args_hash = hashlib.sha256(json.dumps(arguments, sort_keys=True, default=str).encode()).hexdigest()
            approval_payload = {
                'agent_id': agent_claims.get('agent_id'),
                'company_id': agent_claims.get('company_id'),
                'service': self.service_name,
                'action': tool_name,
                'arguments': arguments,
                'arguments_hash': args_hash,
                'path': path,
                'method': method,
            }
            granted = False
            if self.approval_state:
                state = self.approval_state(approval_payload)
                if state == 'executed':
                    return JsonResponse(
                        self._result(
                            req_id,
                            {'content': [{'type': 'text', 'text': 'Approval already used.'}], 'isError': True},
                        )
                    )
                if state == 'approved':
                    approval_id = approval_payload.get('approval_id')
                    if approval_id and self.approval_consume and self.approval_consume(str(approval_id)):
                        granted = True
                    else:
                        return JsonResponse(
                            self._result(
                                req_id,
                                {'content': [{'type': 'text', 'text': 'Approval unavailable.'}], 'isError': True},
                            )
                        )
                elif state == 'pending':
                    return JsonResponse(
                        self._result(
                            req_id,
                            {
                                'content': [{'type': 'text', 'text': 'Approval pending.'}],
                                'isError': True,
                                'meta': {'status': 'pending'},
                            },
                        )
                    )
            if not granted and self.approval_handler:
                pending = self.approval_handler(approval_payload)
                if pending:
                    return JsonResponse(
                        self._result(
                            req_id,
                            {
                                'content': [{'type': 'text', 'text': 'Approval required.'}],
                                'isError': True,
                                'meta': {'approval_id': pending.get('id'), 'status': 'pending'},
                            },
                        )
                    )
            if not granted:
                return JsonResponse(
                    self._result(
                        req_id,
                        {'content': [{'type': 'text', 'text': decision.reason or 'Forbidden'}], 'isError': True},
                    )
                )
        if not decision.allowed:
            return JsonResponse(
                self._result(
                    req_id,
                    {'content': [{'type': 'text', 'text': decision.reason or 'Forbidden'}], 'isError': True},
                )
            )

        drf_request = self._factory.generic(
            method,
            dispatch_path,
            data=arguments.get('body') or {},
            query_params=arguments.get('query') or {},
            HTTP_AUTHORIZATION=request.META.get('HTTP_AUTHORIZATION', ''),
        )
        drf_request.user = getattr(request, 'user', None)
        from django.urls import resolve

        match = resolve(dispatch_path, urlconf=self.urlconf)
        response = match.func(drf_request, *match.args, **match.kwargs)
        status_code = getattr(response, 'status_code', 500)
        try:
            data = response.data  # type: ignore[attr-defined]
        except AttributeError:
            data = {'detail': 'ok'}
        read_sensitive = read_level == 'sensitive'
        redacted = shellui_masking.redact_mapping(
            data if isinstance(data, dict) else {'result': data}, read_sensitive=read_sensitive
        )
        if self.on_call:
            self.on_call(
                {
                    'agent_id': agent_claims.get('agent_id'),
                    'company_id': agent_claims.get('company_id'),
                    'service': self.service_name,
                    'tool': tool_name,
                    'method': method,
                    'path': dispatch_path,
                    'status': status_code,
                    'arguments': shellui_masking.redact_mapping(arguments, read_sensitive=False),
                }
            )
        return JsonResponse(
            self._result(
                req_id,
                {
                    'content': [{'type': 'text', 'text': json.dumps(redacted)}],
                    'isError': status_code >= 400,
                },
            )
        )
