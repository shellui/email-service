"""email-service Shellui MCP Streamable HTTP endpoint."""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.views import APIView

from apps.authapi.email_mcp_server import EmailMcpServer
from apps.authapi.mcp_agent import authenticate_agent_request
from apps.authapi.mcp_identity import (
    McpAgentCompanyMismatch,
    McpIdentityUnavailable,
    ensure_revocation_snapshot,
    identity_origin,
    verify_agent_company_binding,
)
from apps.authapi.spectacular_hooks import mcp_schema_for_server

_server: EmailMcpServer | None = None


def _schema() -> dict:
    generator = SchemaGenerator()
    schema = generator.get_schema(request=None, public=True)
    return mcp_schema_for_server(schema)


def _revocation_check(agent_id: str, _company_id: int | None) -> bool:
    from apps.authapi.mcp_identity import agent_is_revoked

    return agent_is_revoked(agent_id)


def _approval_handler(payload: dict) -> dict | None:
    from apps.authapi.mcp_identity import create_approval_request

    return create_approval_request(payload)


def _approval_state(payload: dict) -> str | None:
    from apps.authapi.mcp_identity import resolve_approval_state

    return resolve_approval_state(payload)


def _approval_consume(approval_id: str, payload: dict | None = None) -> bool:
    from apps.authapi.mcp_identity import consume_approval

    return consume_approval(approval_id, payload=payload or {})


def _forward_call(entry: dict) -> None:
    from apps.authapi.mcp_identity import forward_call_log

    forward_call_log(entry)


def _get_server() -> EmailMcpServer:
    global _server
    if _server is None:
        _server = EmailMcpServer(
            schema_provider=_schema,
            urlconf=settings.ROOT_URLCONF,
            service_name='email',
            identity_revocation_check=_revocation_check,
            on_call=_forward_call,
            approval_handler=_approval_handler,
            approval_state=_approval_state,
            approval_consume=_approval_consume,
        )
    return _server


class OAuthProtectedResourceMetadataView(APIView):
    authentication_classes = []
    permission_classes = []

    @extend_schema(
        tags=['health'],
        summary='OAuth protected resource metadata for Shellui MCP',
        responses={
            200: inline_serializer(
                name='OAuthProtectedResourceMetadata',
                fields={
                    'resource': serializers.CharField(),
                    'authorization_servers': serializers.ListField(child=serializers.CharField()),
                    'bearer_methods_supported': serializers.ListField(child=serializers.CharField()),
                },
            )
        },
    )
    def get(self, request):
        public_base = (getattr(settings, 'EMAIL_PUBLIC_URL', None) or '').strip().rstrip('/')
        if not public_base:
            public_base = request.build_absolute_uri('/').rstrip('/')
        auth_server = identity_origin() or public_base
        return JsonResponse(
            {
                'resource': f'{public_base}/mcp',
                'authorization_servers': [auth_server],
                'bearer_methods_supported': ['header'],
            }
        )


def reset_mcp_server_cache() -> None:
    """Testing helper: rebuild ``McpServer`` after dependency overrides."""
    global _server
    _server = None


@csrf_exempt
def mcp_endpoint(request: HttpRequest):
    authenticated = authenticate_agent_request(request)
    claims = None
    if authenticated is not None:
        user, claims = authenticated
        request.user = user
    if claims is not None:
        try:
            ensure_revocation_snapshot()
            verify_agent_company_binding(str(claims.get('agent_id') or ''), int(claims['company_id']), claims=claims)
        except McpIdentityUnavailable:
            return JsonResponse({'error': 'identity_unavailable'}, status=503)
        except McpAgentCompanyMismatch as exc:
            code = str(exc.args[0]) if exc.args else 'forbidden'
            if code == 'authority_row_missing':
                return JsonResponse({'error_code': 'authority_row_missing'}, status=403)
            if code == 'authority_binding_missing':
                return JsonResponse({'error_code': 'authority_binding_missing'}, status=403)
            return JsonResponse({'error': 'forbidden'}, status=403)
        except (KeyError, TypeError, ValueError):
            return JsonResponse({'error': 'forbidden'}, status=403)
    try:
        return _get_server().handle(request, agent_claims=claims)
    except McpIdentityUnavailable:
        return JsonResponse({'error': 'identity_unavailable'}, status=503)
