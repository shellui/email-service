"""email-service Shellui MCP server with authority-aware approvals and safe audit metadata."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from django.http import HttpRequest, JsonResponse
from shellui_django.mcp.masking import redact_mapping
from shellui_django.mcp.openapi_mcp import marker_tree_for_operation_request, marker_tree_for_operation_response
from shellui_django.mcp.openapi_tools import tools_from_schema
from shellui_django.mcp.rate_limit import check_agent_rate_limit
from shellui_django.mcp.routing import McpToolArgumentError, materialize_openapi_path, validate_mcp_tool_arguments
from shellui_django.mcp.server import McpServer
from shellui_django.mcp.write_gate import decide_write, is_destructive_operation

_BODYLESS_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS', 'TRACE'})


def _http_activity_metadata(
    *,
    schema: dict[str, Any],
    path_template: str,
    method: str,
    status_code: int,
    is_error: bool,
) -> dict[str, Any]:
    """Stable, safe fields for Identity call logs (no tool args, response bodies, or provider text)."""
    operation = McpServer._operation(schema, path_template, method)
    summary = ''
    if operation:
        summary = str(operation.get('summary') or operation.get('operationId') or '').strip()
    m = (method or 'GET').upper()
    if m in _BODYLESS_METHODS:
        access_kind = 'read'
    else:
        access_kind = 'write'
    outcome = 'ok'
    error_code: str | None = None
    if is_error or status_code >= 400:
        if status_code in {401, 403}:
            outcome = 'denied'
            error_code = 'forbidden'
        elif status_code == 404:
            outcome = 'not_found'
            error_code = 'not_found'
        elif status_code == 400:
            outcome = 'rejected'
            error_code = 'validation_failed'
        elif status_code == 409:
            outcome = 'conflict'
            error_code = 'conflict'
        elif status_code >= 500:
            outcome = 'error'
            error_code = 'server_error'
        else:
            outcome = 'error'
            error_code = 'request_failed'
    meta: dict[str, Any] = {
        'resource_path': path_template,
        'access_kind': access_kind,
        'outcome': outcome,
        'duration_ms': 0,
    }
    if summary:
        meta['operation_title'] = summary
    if error_code:
        meta['error_code'] = error_code
    return meta


class EmailMcpServer(McpServer):
    """Adds regional or global authority fields to approval JWTs and tightens call-log metadata."""

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
        tools = tools_from_schema(schema)
        tool = next((t for t in tools if t['name'] == tool_name), None)
        if tool is None:
            return JsonResponse(self._error(req_id, -32602, 'Unknown tool'))
        meta = tool.get('_meta') or {}
        path_template = str(meta.get('path') or '')
        path = str(arguments.get('path') or path_template)
        method = str(arguments.get('method') or meta.get('method') or 'GET').upper()
        if path != path_template or method != meta.get('method'):
            return JsonResponse(self._error(req_id, -32602, 'Tool path mismatch'))
        try:
            validate_mcp_tool_arguments(arguments if isinstance(arguments, dict) else {}, meta)
            path_params = arguments.get('path_params') if isinstance(arguments.get('path_params'), dict) else {}
            resolved_path = materialize_openapi_path(path_template, path_params)
        except McpToolArgumentError as exc:
            return JsonResponse(self._error(req_id, -32602, str(exc)))

        access = str(agent_claims.get('mcp_access') or 'read_only')
        read_level = str(agent_claims.get('mcp_read') or 'safe')
        operation = self._operation(schema, path_template, method)
        destructive = bool(meta.get('destructive')) or is_destructive_operation(operation)
        tool_read_only = bool(meta.get('read_only'))
        request_markers = marker_tree_for_operation_request(operation, schema)
        response_markers = marker_tree_for_operation_response(operation, schema)
        decision = decide_write(
            method=method,
            access_mode=access,
            destructive=destructive,
            allow_destructive_without_asking=bool(agent_claims.get('mcp_destructive')),
            tool_read_only=tool_read_only,
        )
        if decision.needs_approval:
            args_hash = hashlib.sha256(json.dumps(arguments, sort_keys=True, default=str).encode()).hexdigest()
            approval_payload: dict[str, Any] = {
                'agent_id': agent_claims.get('agent_id'),
                'company_id': agent_claims.get('company_id'),
                'service': self.service_name,
                'action': tool_name,
                'arguments': arguments,
                'arguments_hash': args_hash,
                'path': resolved_path,
                'method': method,
            }
            gv = agent_claims.get('grant_version', None)
            if gv is not None:
                approval_payload['grant_version'] = int(gv)
            gc = agent_claims.get('global_company_id', None)
            if gc is not None:
                approval_payload['global_company_id'] = str(gc)
            cbv = agent_claims.get('company_binding_version', None)
            if cbv is not None:
                approval_payload['company_binding_version'] = int(cbv)
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
                    if approval_id and self.approval_consume:
                        consumed = self.approval_consume(str(approval_id), approval_payload)
                        if consumed:
                            granted = True
                        else:
                            return JsonResponse({'error': 'identity_unavailable'}, status=503)
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

        started = time.monotonic()
        inner_kwargs: dict[str, Any] = {
            'query_params': arguments.get('query') or {},
            'HTTP_AUTHORIZATION': request.META.get('HTTP_AUTHORIZATION', ''),
        }
        if method not in _BODYLESS_METHODS:
            body = arguments.get('body')
            inner_kwargs['data'] = body if body is not None else {}
        drf_request = self._factory.generic(method, resolved_path, **inner_kwargs)
        self._attach_request_principal(drf_request, request, agent_claims)
        from django.urls import resolve

        match = resolve(resolved_path, urlconf=self.urlconf)
        response = match.func(drf_request, *match.args, **match.kwargs)
        status_code = getattr(response, 'status_code', 500)
        duration_ms = int(max(0.0, (time.monotonic() - started) * 1000.0))
        try:
            data = response.data  # type: ignore[attr-defined]
        except AttributeError:
            data = {'detail': 'ok'}
        read_sensitive = read_level == 'sensitive'
        redacted = redact_mapping(
            data if isinstance(data, dict) else {'result': data},
            read_sensitive=read_sensitive,
            markers=response_markers,
        )
        is_json_error = isinstance(data, dict) and 'error_code' in data
        tool_error = status_code >= 400 or is_json_error
        activity = _http_activity_metadata(
            schema=schema,
            path_template=path_template,
            method=method,
            status_code=status_code,
            is_error=tool_error,
        )
        activity['duration_ms'] = duration_ms
        if self.on_call:
            entry: dict[str, Any] = {
                'agent_id': agent_claims.get('agent_id'),
                'company_id': agent_claims.get('company_id'),
                'service': self.service_name,
                'tool': tool_name,
                'method': method,
                'path': resolved_path,
                'status': status_code,
                'arguments': redact_mapping(arguments, read_sensitive=False, markers=request_markers),
                'activity': activity,
            }
            gv = agent_claims.get('grant_version', None)
            if gv is not None:
                entry['grant_version'] = int(gv)
            if agent_claims.get('global_company_id') is not None:
                entry['global_company_id'] = str(agent_claims['global_company_id'])
            if agent_claims.get('company_binding_version') is not None:
                entry['company_binding_version'] = int(agent_claims['company_binding_version'])
            self.on_call(entry)
        return JsonResponse(
            self._result(
                req_id,
                {
                    'content': [{'type': 'text', 'text': json.dumps(redacted)}],
                    'isError': tool_error,
                },
            )
        )

    def handle(self, request: HttpRequest, *, agent_claims: dict[str, Any] | None) -> JsonResponse:
        if request.method != 'POST':
            return JsonResponse({'error': 'method_not_allowed'}, status=405)
        if agent_claims is None:
            return JsonResponse({'error': 'unauthorized'}, status=401)
        agent_id = str(agent_claims.get('agent_id') or '')
        if not agent_id:
            return JsonResponse({'error': 'unauthorized'}, status=401)
        if check_agent_rate_limit(agent_id):
            return JsonResponse({'error': 'rate_limited'}, status=429)
        company_id = agent_claims.get('company_id')
        try:
            company_id_int = int(company_id) if company_id is not None else None
        except (TypeError, ValueError):
            company_id_int = None
        if self.identity_revocation_check and self.identity_revocation_check(agent_id, company_id_int):
            return JsonResponse({'error': 'agent_revoked'}, status=403)
        if not self._service_allowed(agent_claims, self.service_name):
            return JsonResponse({'error': 'forbidden'}, status=403)
        try:
            payload = json.loads(request.body.decode('utf-8') or '{}')
        except json.JSONDecodeError:
            return JsonResponse({'error': 'invalid_json'}, status=400)
        method = payload.get('method')
        req_id = payload.get('id')
        params = payload.get('params') or {}
        if method == 'initialize':
            return JsonResponse(self._result(req_id, {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}}}))
        if method == 'tools/list':
            schema = self.schema_provider()
            tools = [{k: v for k, v in t.items() if not k.startswith('_')} for t in tools_from_schema(schema)]
            return JsonResponse(self._result(req_id, {'tools': tools}))
        if method == 'tools/call':
            return self._tools_call(request, req_id, params, agent_claims)
        return JsonResponse(self._error(req_id, -32601, 'Method not found'))
