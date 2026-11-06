"""email-service Shellui MCP server: safe flat audit fields for Identity call logs."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from decimal import Decimal
from typing import Any

from django.http import HttpRequest, JsonResponse
from shellui_django.mcp.masking import redact_mapping
from shellui_django.mcp.openapi_mcp import marker_tree_for_operation_response
from shellui_django.mcp.openapi_tools import tools_from_schema
from shellui_django.mcp.routing import McpToolArgumentError, materialize_openapi_path, validate_mcp_tool_arguments
from shellui_django.mcp.server import McpServer
from shellui_django.mcp.write_gate import decide_write, is_destructive_operation

from apps.authapi.mcp_claims import AgentClaimsValidationError, normalize_mcp_agent_claims, strict_json_nonneg_int
from apps.authapi.mcp_identity import McpIdentityUnavailable, last_approval_state_extras

_BODYLESS_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS', 'TRACE'})

# Identity-aligned categorical outcomes (call log contract).
_OUTCOMES = frozenset({'failed', 'denied', 'pending_approval', 'allowed', 'session_used'})
_ERROR_CODES = frozenset(
    {
        'forbidden',
        'not_found',
        'validation_failed',
        'conflict',
        'server_error',
        'request_failed',
        'identity_unavailable',
        'tool_error',
        'read_only_blocked',
        'approval_pending',
        'approval_denied',
        'approval_expired',
        'rate_limited',
        'authority_ambiguous',
        'authority_mismatch',
        'authority_row_missing',
    }
)

def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    raise TypeError(f'Non-JSON-serializable type in MCP tool arguments: {type(value).__name__}')


def _canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), default=_json_default).encode('utf-8')


def _arguments_fingerprint(arguments: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json_bytes(arguments)).hexdigest()


def _flat_audit_entry(
    *,
    agent_claims: dict[str, Any],
    service_name: str,
    tool_name: str,
    path_template: str,
    method: str,
    operation: dict[str, Any] | None,
    outcome: str,
    duration_ms: int,
    http_status: int | None = None,
    error_code: str | None = None,
    approval_request_id: str | None = None,
    arguments_hash: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    if outcome not in _OUTCOMES:
        raise ValueError('outcome')
    if error_code is not None and error_code not in _ERROR_CODES:
        error_code = 'request_failed'
    access_kind = 'read' if method.upper() in _BODYLESS_METHODS else 'write'
    entry: dict[str, Any] = {
        'agent_id': agent_claims.get('agent_id'),
        'company_id': agent_claims.get('company_id'),
        'service': service_name,
        'tool': tool_name,
        'method': method.upper(),
        'resource_path': path_template,
        'access_kind': access_kind,
        'outcome': outcome,
        'duration_ms': max(0, int(duration_ms)),
    }
    if http_status is not None:
        entry['http_status'] = int(http_status)
    if error_code:
        entry['error_code'] = error_code
    if approval_request_id:
        entry['approval_request_id'] = approval_request_id
    if arguments_hash:
        entry['arguments_hash'] = arguments_hash
    if request_id:
        entry['request_id'] = request_id
    gv = agent_claims.get('grant_version')
    if gv is not None:
        entry['grant_version'] = strict_json_nonneg_int(gv, field='grant_version')
    if agent_claims.get('global_company_id') is not None:
        entry['global_company_id'] = str(agent_claims['global_company_id'])
    cbv = agent_claims.get('company_binding_version')
    if cbv is not None:
        entry['company_binding_version'] = strict_json_nonneg_int(cbv, field='company_binding_version')
    return entry


class EmailMcpServer(McpServer):
    """Adds flat Identity-safe audit entries; never forwards MCP tool ``arguments`` to Identity."""

    def handle(self, request: HttpRequest, *, agent_claims: dict[str, Any] | None) -> JsonResponse:
        if agent_claims is not None:
            try:
                agent_claims = normalize_mcp_agent_claims(agent_claims)
            except AgentClaimsValidationError:
                return JsonResponse({'error': 'forbidden'}, status=403)
        return super().handle(request, agent_claims=agent_claims)

    def _tools_call(
        self,
        request: HttpRequest,
        req_id: Any,
        params: dict[str, Any],
        agent_claims: dict[str, Any],
    ) -> JsonResponse:
        tool_name = str(params.get('name') or '')
        arguments = params.get('arguments') or {}
        if not isinstance(arguments, dict):
            arguments = {}
        req_trace = (getattr(request, 'request_id', None) or '').strip() or None
        schema = self.schema_provider()
        tools = tools_from_schema(schema)
        tool = next((t for t in tools if t['name'] == tool_name), None)
        if tool is None:
            return JsonResponse(self._error(req_id, -32602, 'Unknown tool'))
        meta = tool.get('_meta') or {}
        path_template = str(meta.get('path') or '')
        path = str(arguments.get('path') or path_template)
        meta_method = str(meta.get('method') or 'GET').upper()
        method = str(arguments.get('method') or meta.get('method') or 'GET').upper()
        if path != path_template or method != meta_method:
            return JsonResponse(self._error(req_id, -32602, 'Tool path mismatch'))
        operation = self._operation(schema, path_template, method)
        meta_destructive = bool(meta.get('destructive')) if 'destructive' in meta else None
        op_destructive = is_destructive_operation(operation)
        destructive = meta_destructive if meta_destructive is not None else op_destructive
        try:
            validate_mcp_tool_arguments(arguments, meta)
            path_params = arguments.get('path_params') if isinstance(arguments.get('path_params'), dict) else {}
            resolved_path = materialize_openapi_path(path_template, path_params)
        except McpToolArgumentError as exc:
            if self.on_call:
                try:
                    fp = _arguments_fingerprint(arguments)
                except TypeError:
                    fp = hashlib.sha256(b'').hexdigest()
                self.on_call(
                    _flat_audit_entry(
                        agent_claims=agent_claims,
                        service_name=self.service_name,
                        tool_name=tool_name,
                        path_template=path_template,
                        method=method,
                        operation=operation,
                        outcome='failed',
                        duration_ms=0,
                        http_status=400,
                        error_code='validation_failed',
                        arguments_hash=fp,
                        request_id=req_trace,
                    )
                )
            return JsonResponse(self._error(req_id, -32602, str(exc)))

        read_level = str(agent_claims.get('mcp_read') or 'safe')
        tool_read_only = bool(meta.get('read_only'))
        response_markers = marker_tree_for_operation_response(operation, schema)

        access = str(agent_claims.get('mcp_access') or 'read_only')
        gate_started = time.monotonic()
        decision = decide_write(
            method=method,
            access_mode=access,
            destructive=destructive,
            allow_destructive_without_asking=False,
            tool_read_only=tool_read_only,
        )

        def _after_gate_ms() -> int:
            return int(max(0.0, (time.monotonic() - gate_started) * 1000.0))

        try:
            args_hash = _arguments_fingerprint(arguments)
        except TypeError:
            if self.on_call:
                self.on_call(
                    _flat_audit_entry(
                        agent_claims=agent_claims,
                        service_name=self.service_name,
                        tool_name=tool_name,
                        path_template=path_template,
                        method=method,
                        operation=operation,
                        outcome='failed',
                        duration_ms=0,
                        http_status=400,
                        error_code='validation_failed',
                        request_id=req_trace,
                    )
                )
            return JsonResponse(self._error(req_id, -32602, 'Arguments are not JSON-serializable'))

        approval_payload: dict[str, Any] = {
            'agent_id': agent_claims.get('agent_id'),
            'company_id': agent_claims.get('company_id'),
            'service': self.service_name,
            'action': tool_name,
            'arguments_hash': args_hash,
            'path': resolved_path,
            'method': method,
        }
        gv = agent_claims.get('grant_version')
        if gv is not None:
            approval_payload['grant_version'] = strict_json_nonneg_int(gv, field='grant_version')
        if agent_claims.get('global_company_id') is not None:
            approval_payload['global_company_id'] = str(agent_claims['global_company_id'])
        if agent_claims.get('company_binding_version') is not None:
            approval_payload['company_binding_version'] = strict_json_nonneg_int(
                agent_claims['company_binding_version'], field='company_binding_version'
            )

        if decision.needs_approval:
            granted = False
            approval_req_id: str | None = None
            identity_state_failed = False
            state: str | None = None
            if self.approval_state:
                try:
                    state = self.approval_state(approval_payload)
                except McpIdentityUnavailable:
                    identity_state_failed = True
                    state = None
                if state == 'executed':
                    if self.on_call:
                        self.on_call(
                            _flat_audit_entry(
                                agent_claims=agent_claims,
                                service_name=self.service_name,
                                tool_name=tool_name,
                                path_template=path_template,
                                method=method,
                                operation=operation,
                                outcome='denied',
                                duration_ms=_after_gate_ms(),
                                error_code='approval_expired',
                                arguments_hash=args_hash,
                                request_id=req_trace,
                            )
                        )
                    return JsonResponse(
                        self._result(
                            req_id,
                            {'content': [{'type': 'text', 'text': 'Approval already used.'}], 'isError': True},
                        )
                    )
                if state == 'approved':
                    approval_id = approval_payload.get('approval_id')
                    if approval_id and self.approval_consume:
                        try:
                            consumed = self.approval_consume(str(approval_id), approval_payload)
                        except McpIdentityUnavailable:
                            if self.on_call:
                                self.on_call(
                                    _flat_audit_entry(
                                        agent_claims=agent_claims,
                                        service_name=self.service_name,
                                        tool_name=tool_name,
                                        path_template=path_template,
                                        method=method,
                                        operation=operation,
                                        outcome='failed',
                                        duration_ms=_after_gate_ms(),
                                        error_code='identity_unavailable',
                                        arguments_hash=args_hash,
                                        request_id=req_trace,
                                    )
                                )
                            return JsonResponse({'error': 'identity_unavailable'}, status=503)
                        if consumed:
                            granted = True
                        else:
                            if self.on_call:
                                self.on_call(
                                    _flat_audit_entry(
                                        agent_claims=agent_claims,
                                        service_name=self.service_name,
                                        tool_name=tool_name,
                                        path_template=path_template,
                                        method=method,
                                        operation=operation,
                                        outcome='failed',
                                        duration_ms=_after_gate_ms(),
                                        error_code='identity_unavailable',
                                        arguments_hash=args_hash,
                                        request_id=req_trace,
                                    )
                                )
                            return JsonResponse({'error': 'identity_unavailable'}, status=503)
                    else:
                        if self.on_call:
                            self.on_call(
                                _flat_audit_entry(
                                    agent_claims=agent_claims,
                                    service_name=self.service_name,
                                    tool_name=tool_name,
                                    path_template=path_template,
                                    method=method,
                                    operation=operation,
                                    outcome='failed',
                                    duration_ms=_after_gate_ms(),
                                    error_code='identity_unavailable',
                                    arguments_hash=args_hash,
                                    request_id=req_trace,
                                )
                            )
                        return JsonResponse(
                            self._result(
                                req_id,
                                {'content': [{'type': 'text', 'text': 'Approval unavailable.'}], 'isError': True},
                            )
                        )
                elif state == 'pending':
                    extras = last_approval_state_extras()
                    appr_id = extras.get('approval_request_id')
                    approval_req_id = str(appr_id) if appr_id else None
                    if self.on_call:
                        self.on_call(
                            _flat_audit_entry(
                                agent_claims=agent_claims,
                                service_name=self.service_name,
                                tool_name=tool_name,
                                path_template=path_template,
                                method=method,
                                operation=operation,
                                outcome='pending_approval',
                                duration_ms=_after_gate_ms(),
                                error_code='approval_pending',
                                approval_request_id=approval_req_id,
                                arguments_hash=args_hash,
                                request_id=req_trace,
                            )
                        )
                    meta: dict[str, Any] = {'status': 'pending'}
                    if approval_req_id:
                        meta['approval_request_id'] = approval_req_id
                    return JsonResponse(
                        self._result(
                            req_id,
                            {
                                'content': [{'type': 'text', 'text': 'Approval pending.'}],
                                'isError': True,
                                'meta': meta,
                            },
                        )
                    )
                elif state == 'denied':
                    if self.on_call:
                        self.on_call(
                            _flat_audit_entry(
                                agent_claims=agent_claims,
                                service_name=self.service_name,
                                tool_name=tool_name,
                                path_template=path_template,
                                method=method,
                                operation=operation,
                                outcome='denied',
                                duration_ms=_after_gate_ms(),
                                error_code='approval_denied',
                                arguments_hash=args_hash,
                                request_id=req_trace,
                            )
                        )
                    return JsonResponse(
                        self._result(
                            req_id,
                            {'content': [{'type': 'text', 'text': 'Approval denied.'}], 'isError': True},
                        )
                    )
                elif state in {'invalid', 'unknown'} or (state is None and not identity_state_failed):
                    if self.on_call:
                        self.on_call(
                            _flat_audit_entry(
                                agent_claims=agent_claims,
                                service_name=self.service_name,
                                tool_name=tool_name,
                                path_template=path_template,
                                method=method,
                                operation=operation,
                                outcome='denied',
                                duration_ms=_after_gate_ms(),
                                error_code='approval_denied',
                                arguments_hash=args_hash,
                                request_id=req_trace,
                            )
                        )
                    return JsonResponse(
                        self._result(
                            req_id,
                            {'content': [{'type': 'text', 'text': 'Approval denied.'}], 'isError': True},
                        )
                    )
                elif state == 'not_found':
                    pass
            if not granted and identity_state_failed:
                if self.on_call:
                    self.on_call(
                        _flat_audit_entry(
                            agent_claims=agent_claims,
                            service_name=self.service_name,
                            tool_name=tool_name,
                            path_template=path_template,
                            method=method,
                            operation=operation,
                            outcome='failed',
                            duration_ms=_after_gate_ms(),
                            error_code='identity_unavailable',
                            arguments_hash=args_hash,
                            request_id=req_trace,
                        )
                    )
                return JsonResponse({'error': 'identity_unavailable'}, status=503)
            if not granted and self.approval_handler:
                try:
                    pending = self.approval_handler(approval_payload)
                except McpIdentityUnavailable:
                    if self.on_call:
                        self.on_call(
                            _flat_audit_entry(
                                agent_claims=agent_claims,
                                service_name=self.service_name,
                                tool_name=tool_name,
                                path_template=path_template,
                                method=method,
                                operation=operation,
                                outcome='failed',
                                duration_ms=_after_gate_ms(),
                                error_code='identity_unavailable',
                                arguments_hash=args_hash,
                                request_id=req_trace,
                            )
                        )
                    return JsonResponse({'error': 'identity_unavailable'}, status=503)
                if pending:
                    appr = pending.get('id') or pending.get('approval_request_id')
                    approval_req_id = str(appr) if appr else None
                    if self.on_call:
                        self.on_call(
                            _flat_audit_entry(
                                agent_claims=agent_claims,
                                service_name=self.service_name,
                                tool_name=tool_name,
                                path_template=path_template,
                                method=method,
                                operation=operation,
                                outcome='pending_approval',
                                duration_ms=_after_gate_ms(),
                                error_code='approval_pending',
                                approval_request_id=approval_req_id,
                                arguments_hash=args_hash,
                                request_id=req_trace,
                            )
                        )
                    meta_c: dict[str, Any] = {'status': 'pending'}
                    if approval_req_id:
                        meta_c['approval_request_id'] = approval_req_id
                    return JsonResponse(
                        self._result(
                            req_id,
                            {
                                'content': [{'type': 'text', 'text': 'Approval required.'}],
                                'isError': True,
                                'meta': meta_c,
                            },
                        )
                    )
            if not granted:
                deny_ms = _after_gate_ms()
                oc = 'read_only_blocked' if access == 'read_only' else 'forbidden'
                ou = 'denied' if access == 'read_only' else 'failed'
                if self.on_call:
                    self.on_call(
                        _flat_audit_entry(
                            agent_claims=agent_claims,
                            service_name=self.service_name,
                            tool_name=tool_name,
                            path_template=path_template,
                            method=method,
                            operation=operation,
                            outcome=ou,
                            duration_ms=deny_ms,
                            error_code=oc,
                            arguments_hash=args_hash,
                            request_id=req_trace,
                        )
                    )
                return JsonResponse(
                    self._result(
                        req_id,
                        {'content': [{'type': 'text', 'text': decision.reason or 'Forbidden'}], 'isError': True},
                    )
                )
        if not decision.allowed:
            if self.on_call:
                self.on_call(
                    _flat_audit_entry(
                        agent_claims=agent_claims,
                        service_name=self.service_name,
                        tool_name=tool_name,
                        path_template=path_template,
                        method=method,
                        operation=operation,
                        outcome='denied',
                        duration_ms=0,
                        error_code='read_only_blocked',
                        arguments_hash=args_hash,
                        request_id=req_trace,
                    )
                )
            return JsonResponse(
                self._result(
                    req_id,
                    {'content': [{'type': 'text', 'text': decision.reason or 'Forbidden'}], 'isError': True},
                )
            )

        exec_started = time.monotonic()
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
        duration_ms = int(max(0.0, (time.monotonic() - exec_started) * 1000.0))
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
        if tool_error:
            outcome = 'failed'
            ec: str | None = None
            if isinstance(data, dict) and data.get('error_code'):
                raw_ec = str(data.get('error_code'))
                ec = raw_ec if raw_ec in _ERROR_CODES else 'request_failed'
            elif status_code == 403:
                ec = 'forbidden'
            elif status_code == 404:
                ec = 'not_found'
            elif status_code == 400:
                ec = 'validation_failed'
            elif status_code == 409:
                ec = 'conflict'
            elif status_code == 429:
                ec = 'rate_limited'
            elif status_code >= 500:
                ec = 'server_error'
            else:
                ec = 'request_failed'
        else:
            outcome = 'allowed'
            ec = None
        if self.on_call:
            self.on_call(
                _flat_audit_entry(
                    agent_claims=agent_claims,
                    service_name=self.service_name,
                    tool_name=tool_name,
                    path_template=path_template,
                    method=method,
                    operation=operation,
                    outcome=outcome,
                    duration_ms=duration_ms,
                    http_status=status_code,
                    error_code=ec,
                    arguments_hash=args_hash,
                    request_id=req_trace,
                )
            )
        return JsonResponse(
            self._result(
                req_id,
                {
                    'content': [{'type': 'text', 'text': json.dumps(redacted)}],
                    'isError': tool_error,
                },
            )
        )
