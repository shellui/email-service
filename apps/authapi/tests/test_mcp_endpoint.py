"""Shellui MCP ``POST /mcp`` endpoint."""

from __future__ import annotations

import json
import time
import uuid
from unittest.mock import patch

import jwt
from django.test import Client, TestCase, override_settings

from apps.authapi.mcp_openapi import MCP_FORBIDDEN_OPERATION_IDS, MCP_EXPLICIT_REGISTRY
from shellui_mcp.openapi_tools import tools_from_schema

ISSUER = 'https://id.shellui.test'
AUDIENCE = 'shellui-test'
SIGNING = 'mcp-endpoint-test-signing-key-0123456789'

MCP_SETTINGS = {
    'DEBUG': True,
    'JWT_HS256_FALLBACK_SECRET': SIGNING,
    'ALLOW_JWT_HS256_FALLBACK': True,
    'IDENTITY_ISSUER': ISSUER,
    'IDENTITY_AUDIENCE': AUDIENCE,
    'JWT_ISSUER': ISSUER,
    'JWT_AUDIENCE': AUDIENCE,
    'IDENTITY_JWKS_DOCUMENT': None,
    'AUTH_RATE_LIMIT_ENABLED': False,
    'SIMPLE_JWT': {
        'ALGORITHM': 'HS256',
        'SIGNING_KEY': SIGNING,
        'ISSUER': ISSUER,
        'AUDIENCE': AUDIENCE,
    },
    'IDENTITY_SERVICE_URL': 'https://identity.test',
    'EMAIL_PUBLIC_URL': 'https://email.test',
}


def _agent_token(
    *,
    access: str = 'read_only',
    read_level: str = 'safe',
    company_id: int = 42,
    agent_id: str | None = None,
) -> tuple[str, str]:
    agent_id = agent_id or str(uuid.uuid4())
    claims = {
        'sub': f'agent:{agent_id}',
        'agent_id': agent_id,
        'company_id': company_id,
        'scope': 'email',
        'mcp_access': access,
        'mcp_read': read_level,
        'mcp_destructive': False,
        'exp': int(time.time()) + 600,
        'iss': ISSUER,
        'aud': AUDIENCE,
    }
    return jwt.encode(claims, SIGNING, algorithm='HS256'), agent_id


def _rpc(client: Client, token: str, method: str, params: dict | None = None, req_id: int = 1):
    body = {'jsonrpc': '2.0', 'id': req_id, 'method': method, 'params': params or {}}
    return client.post(
        '/mcp',
        data=json.dumps(body),
        content_type='application/json',
        HTTP_AUTHORIZATION=f'Bearer {token}',
    )


@override_settings(**MCP_SETTINGS)
class McpEndpointTests(TestCase):
    def setUp(self):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        reset_mcp_server_cache()
        self.client = Client()

    def test_protected_resource_metadata_points_at_identity(self):
        response = self.client.get('/.well-known/oauth-protected-resource')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(str(data['resource']).endswith('/mcp'))
        self.assertEqual(data['authorization_servers'], [ISSUER])

    def test_untagged_operations_are_not_mcp_tools(self):
        from apps.authapi.mcp_views import _schema

        tools = tools_from_schema(_schema())
        names = {tool['name'] for tool in tools}
        for forbidden in MCP_FORBIDDEN_OPERATION_IDS:
            self.assertNotIn(forbidden, names)
        self.assertNotIn('api_v1_send_create', names)
        self.assertIn('api_v1_rules_list', names)

    def test_tools_list_requires_agent_token(self):
        response = self.client.post('/mcp', data='{}', content_type='application/json')
        self.assertEqual(response.status_code, 401)

    def test_agent_without_email_scope_forbidden(self):
        token, _agent_id = _agent_token()
        import jwt

        claims = jwt.decode(token, options={'verify_signature': False})
        claims['scope'] = 'identity'
        bad = jwt.encode(claims, SIGNING, algorithm='HS256')
        response = _rpc(self.client, bad, 'tools/list')
        self.assertEqual(response.status_code, 401)

    @patch('apps.authapi.mcp_identity.agent_is_revoked', return_value=True)
    def test_revoked_agent_refused(self, _revoked):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        reset_mcp_server_cache()
        token, _agent_id = _agent_token()
        response = _rpc(self.client, token, 'tools/list')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'agent_revoked')

    def test_secrets_never_returned_from_sensitive_tool(self):
        from apps.actions.emit import store_rule_secret
        from apps.actions.models import ActionRule

        company_id = 42
        rule = ActionRule.objects.create(
            company_id=company_id,
            name='Hook',
            event_type='email.message.sent',
            config={'url': 'https://example.com/hook', 'secret': store_rule_secret('super-secret-value')},
        )
        token, _agent_id = _agent_token(company_id=company_id)
        list_response = _rpc(self.client, token, 'tools/list', req_id=2)
        self.assertEqual(list_response.status_code, 200)
        tool_name = 'api_v1_actions_rules_list'
        call_response = _rpc(
            self.client,
            token,
            'tools/call',
            {
                'name': tool_name,
                'arguments': {
                    'path': '/api/v1/actions/rules',
                    'method': 'GET',
                },
            },
            req_id=3,
        )
        self.assertEqual(call_response.status_code, 200)
        result = call_response.json()['result']
        text = result['content'][0]['text']
        self.assertNotIn('super-secret-value', text)

    def test_write_blocked_in_read_only_mode(self):
        token, _agent_id = _agent_token(access='read_only')
        response = _rpc(
            self.client,
            token,
            'tools/call',
            {
                'name': 'api_v1_rules_create',
                'arguments': {
                    'path': '/api/v1/rules',
                    'method': 'POST',
                    'body': {'event_key': 'identity.auth.magic_link', 'enabled': True},
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()['result']
        self.assertTrue(result.get('isError'))
        self.assertIn('read only', result['content'][0]['text'].lower())

    @patch('apps.authapi.mcp_identity.create_approval_request', return_value={'id': 'approval-123'})
    def test_write_requires_approval_in_ask_mode(self, _approval):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        reset_mcp_server_cache()
        token, _agent_id = _agent_token(access='ask')
        response = _rpc(
            self.client,
            token,
            'tools/call',
            {
                'name': 'api_v1_rules_create',
                'arguments': {
                    'path': '/api/v1/rules',
                    'method': 'POST',
                    'body': {'event_key': 'identity.auth.magic_link', 'enabled': True},
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()['result']
        self.assertTrue(result.get('isError'))
        self.assertEqual(result.get('meta', {}).get('status'), 'pending')
        self.assertEqual(result.get('meta', {}).get('approval_id'), 'approval-123')

    def test_registry_covers_every_mcp_tool(self):
        from apps.authapi.mcp_views import _schema

        tools = tools_from_schema(_schema())
        tool_ops = set()
        for tool in tools:
            meta = tool.get('_meta') or {}
            op_id = meta.get('operationId')
            if op_id:
                tool_ops.add(op_id)
        self.assertEqual(tool_ops, set(MCP_EXPLICIT_REGISTRY))
