"""Shellui MCP ``POST /mcp`` endpoint."""

from __future__ import annotations

import json
import time
import uuid
from unittest.mock import MagicMock, call, patch

import jwt
from django.test import Client, TestCase, override_settings

from apps.authapi.mcp_identity import reset_revocation_cache_for_tests
from apps.email.models import CompanyProfile
from apps.authapi.mcp_openapi import MCP_EXPLICIT_REGISTRY, MCP_FORBIDDEN_OPERATION_IDS
from apps.authapi.tests.rs256_helpers import generate_test_rsa_jwks, sign_test_revocation_list_assertion
from shellui_django.mcp.internal import (
    REVOCATION_AUDIENCE,
    REVOCATION_CACHE_SECONDS,
    REVOCATION_SCOPE,
    verify_mcp_internal_assertion,
)
from shellui_django.mcp.openapi_tools import tools_from_schema

ISSUER = 'https://id.shellui.test'
_TEST_JWKS, _TEST_PRIVATE_PEM, _TEST_KID = generate_test_rsa_jwks()
# This service's own key for internal MCP assertions to identity (config/service_signing.py).
_SERVICE_JWKS, _SERVICE_PRIVATE_PEM, _SERVICE_KID = generate_test_rsa_jwks()
SERVICE_ISSUER = 'https://email.test'
AUDIENCE = 'shellui-test'
MCP_RESOURCE = 'https://email.test/mcp'
SIGNING = 'mcp-endpoint-test-signing-key-0123456789'

MCP_SETTINGS = {
    'DEBUG': True,
    'JWT_HS256_FALLBACK_SECRET': SIGNING,
    'ALLOW_JWT_HS256_FALLBACK': True,
    'IDENTITY_ISSUER': ISSUER,
    'IDENTITY_AUDIENCE': AUDIENCE,
    'JWT_ISSUER': ISSUER,
    'JWT_AUDIENCE': AUDIENCE,
    'IDENTITY_JWKS_DOCUMENT': _TEST_JWKS,
    'AUTH_RATE_LIMIT_ENABLED': False,
    'SIMPLE_JWT': {
        'ALGORITHM': 'HS256',
        'SIGNING_KEY': SIGNING,
        'ISSUER': ISSUER,
        'AUDIENCE': AUDIENCE,
    },
    'IDENTITY_SERVICE_URL': 'https://identity.test',
    'EMAIL_PUBLIC_URL': 'https://email.test',
    'MCP_SIGNING_KEY_PEM': _SERVICE_PRIVATE_PEM,
    'MCP_SIGNING_ISSUER': SERVICE_ISSUER,
}


def _agent_token(
    *,
    access: str = 'read_only',
    read_level: str = 'safe',
    company_id: int = 42,
    agent_id: str | None = None,
    audience: str | None = None,
    global_company_id: str | None = None,
    company_binding_version: int | None = None,
    grant_version: int | None = None,
    mcp_destructive: bool = False,
) -> tuple[str, str]:
    agent_id = agent_id or str(uuid.uuid4())
    claims = {
        'sub': f'agent:{agent_id}',
        'agent_id': agent_id,
        'company_id': company_id,
        'scope': 'email',
        'mcp_access': access,
        'mcp_read': read_level,
        'mcp_destructive': mcp_destructive,
        'exp': int(time.time()) + 600,
        'iss': ISSUER,
        'aud': audience if audience is not None else MCP_RESOURCE,
    }
    if grant_version is not None:
        claims['grant_version'] = grant_version
    if global_company_id is not None:
        claims['global_company_id'] = global_company_id
    if company_binding_version is not None:
        claims['company_binding_version'] = company_binding_version
    return jwt.encode(claims, SIGNING, algorithm='HS256'), agent_id


def _rpc(client: Client, token: str, method: str, params: dict | None = None, req_id: int = 1):
    body = {'jsonrpc': '2.0', 'id': req_id, 'method': method, 'params': params or {}}
    return client.post(
        '/mcp',
        data=json.dumps(body),
        content_type='application/json',
        HTTP_AUTHORIZATION=f'Bearer {token}',
    )


def _revocation_ok_response(*, agents=None):
    assertion = sign_test_revocation_list_assertion(
        private_pem=_TEST_PRIVATE_PEM,
        kid=_TEST_KID,
        issuer=ISSUER,
        revoked_agent_ids=[],
        agents=agents or [],
        cache_seconds=REVOCATION_CACHE_SECONDS,
    )
    body = {
        'revoked_agent_ids': [],
        'cache_seconds': REVOCATION_CACHE_SECONDS,
        'revocation_assertion': assertion,
        'agents': agents or [],
    }
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=body)
    return response


@override_settings(**MCP_SETTINGS)
class McpEndpointTests(TestCase):
    def setUp(self):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        reset_mcp_server_cache()
        reset_revocation_cache_for_tests()
        self.client = Client()
        self.revocation_patcher = patch('apps.authapi.mcp_identity.requests.get')
        self.mock_revocation_get = self.revocation_patcher.start()
        self.mock_revocation_get.return_value = _revocation_ok_response()

    def tearDown(self):
        self.mock_revocation_get.side_effect = None
        self.revocation_patcher.stop()

    def test_protected_resource_metadata_points_at_identity(self):
        response = self.client.get('/.well-known/oauth-protected-resource')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['resource'], MCP_RESOURCE)
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

    def test_legacy_identity_audience_rejected(self):
        token, _agent_id = _agent_token(audience=AUDIENCE)
        response = _rpc(self.client, token, 'tools/list')
        self.assertEqual(response.status_code, 401)

    def test_staff_agent_claims_rejected(self):
        token, _agent_id = _agent_token()
        claims = jwt.decode(token, options={'verify_signature': False})
        claims['user_metadata'] = {'is_staff': True}
        staff_token = jwt.encode(claims, SIGNING, algorithm='HS256')
        response = _rpc(self.client, staff_token, 'tools/list')
        self.assertEqual(response.status_code, 401)

    def test_agent_without_email_scope_forbidden(self):
        token, _agent_id = _agent_token()
        claims = jwt.decode(token, options={'verify_signature': False})
        claims['scope'] = 'identity'
        bad = jwt.encode(claims, SIGNING, algorithm='HS256')
        response = _rpc(self.client, bad, 'tools/list')
        self.assertEqual(response.status_code, 401)

    def test_revocation_list_requires_signed_assertion(self):
        token, _agent_id = _agent_token()
        _rpc(self.client, token, 'tools/list')
        self.assertTrue(self.mock_revocation_get.called)
        headers = self.mock_revocation_get.call_args.kwargs.get('headers') or {}
        auth = headers.get('Authorization', '')
        self.assertTrue(auth.startswith('Bearer '))
        assertion = auth[7:]
        payload = jwt.decode(assertion, options={'verify_signature': False})
        self.assertEqual(payload['aud'], REVOCATION_AUDIENCE)
        self.assertEqual(payload['scope'], REVOCATION_SCOPE)
        # RS256 with this service's own key and issuer, never the user JWT secret.
        self.assertEqual(jwt.get_unverified_header(assertion)['alg'], 'RS256')
        claims = verify_mcp_internal_assertion(
            assertion,
            _SERVICE_JWKS,
            expected_issuer=SERVICE_ISSUER,
            expected_audience=REVOCATION_AUDIENCE,
            expected_scope=REVOCATION_SCOPE,
            expected_target=ISSUER,
        )
        self.assertEqual(claims['iss'], SERVICE_ISSUER)

    @override_settings(MCP_SIGNING_KEY_PEM='')
    def test_without_service_signing_key_agents_are_refused(self):
        token, _agent_id = _agent_token()
        _rpc(self.client, token, 'tools/list')
        # Fails closed before any unsigned call reaches identity.
        self.assertFalse(self.mock_revocation_get.called)

    def test_agent_company_id_query_mismatch_forbidden(self):
        token, _agent_id = _agent_token(company_id=42)
        list_response = _rpc(
            self.client,
            token,
            'tools/call',
            {
                'name': 'api_v1_rules_list',
                'arguments': {
                    'path': '/api/v1/rules',
                    'method': 'GET',
                    'query': {'company_id': 99},
                },
            },
        )
        self.assertEqual(list_response.status_code, 200)
        result = list_response.json()['result']
        self.assertTrue(result.get('isError'))
        self.assertIn('company_mismatch', result['content'][0]['text'])

    def test_revocation_fetch_failure_refuses_mcp(self):
        self.mock_revocation_get.side_effect = ConnectionError('refused')
        token, _agent_id = _agent_token()
        response = _rpc(self.client, token, 'tools/list')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error'], 'identity_unavailable')

    def test_revocation_404_refuses_mcp(self):
        response404 = MagicMock()
        response404.status_code = 404
        self.mock_revocation_get.return_value = response404
        token, _agent_id = _agent_token()
        response = _rpc(self.client, token, 'tools/list')
        self.assertEqual(response.status_code, 503)

    def test_revocation_unsigned_payload_refuses_mcp(self):
        bad = MagicMock()
        bad.status_code = 200
        bad.raise_for_status = MagicMock()
        bad.json = MagicMock(return_value={'revoked_agent_ids': [], 'cache_seconds': 30})
        self.mock_revocation_get.return_value = bad
        token, _agent_id = _agent_token()
        response = _rpc(self.client, token, 'tools/list')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error'], 'identity_unavailable')

    @patch('apps.authapi.mcp_identity.agent_is_revoked', return_value=True)
    def test_revoked_agent_refused(self, _revoked):
        from apps.authapi.mcp_identity import _revocation_snapshot
        from apps.authapi.mcp_views import reset_mcp_server_cache

        _revocation_snapshot['trusted'] = True
        reset_mcp_server_cache()
        token, _agent_id = _agent_token()
        response = _rpc(self.client, token, 'tools/list')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'agent_revoked')

    def test_provider_hints_redacted_for_agents(self):
        from apps.email.crypto import encrypt_json
        from apps.email.models import CompanyProvider

        company_id = 42
        CompanyProvider.objects.create(
            company_id=company_id,
            provider='resend',
            from_email='hello@acme.com',
            credentials_ciphertext=encrypt_json({'api_key': 're_live_secret_value'}, setting='EMAIL_CREDENTIALS_KEY'),
            credentials_hint='••••alue',
            configured=True,
        )
        token, _agent_id = _agent_token(company_id=company_id, read_level='safe')
        response = _rpc(
            self.client,
            token,
            'tools/call',
            {
                'name': 'api_v1_provider_retrieve',
                'arguments': {
                    'path': '/api/v1/provider',
                    'method': 'GET',
                    'query': {'company_id': company_id},
                },
            },
            req_id=4,
        )
        self.assertEqual(response.status_code, 200)
        text = response.json()['result']['content'][0]['text']
        self.assertNotIn('••••alue', text)
        self.assertIn('[redacted]', text)

    def test_path_params_materialize_for_template_retrieve(self):
        from apps.email.models import EmailTemplate

        company_id = 42
        template = EmailTemplate.objects.create(
            company_id=company_id,
            template_key='company.42.mcp_path_params',
            name='Copy',
            event_type='email.newsletter.requested',
            kind=EmailTemplate.KIND_EVENT,
            language='en',
            set='barebone',
        )
        token, _agent_id = _agent_token(company_id=company_id)
        response = _rpc(
            self.client,
            token,
            'tools/call',
            {
                'name': 'api_v1_templates_retrieve',
                'arguments': {
                    'path': '/api/v1/templates/{template_id}',
                    'path_params': {'template_id': str(template.pk)},
                    'method': 'GET',
                },
            },
            req_id=5,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn('result', payload, payload)
        self.assertFalse(payload['result'].get('isError'))
        self.assertIn(str(template.pk), payload['result']['content'][0]['text'])

    def test_concrete_path_without_template_is_rejected(self):
        token, _agent_id = _agent_token()
        response = _rpc(
            self.client,
            token,
            'tools/call',
            {
                'name': 'api_v1_templates_retrieve',
                'arguments': {
                    'path': '/api/v1/templates/999999',
                    'method': 'GET',
                },
            },
            req_id=6,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('error', response.json())

    def test_secrets_never_returned_from_sensitive_tool(self):
        from apps.actions.emit import store_rule_secret
        from apps.actions.models import ActionRule

        company_id = 42
        ActionRule.objects.create(
            company_id=company_id,
            name='Hook',
            event_type='email.message.sent',
            config={'url': 'https://example.com/hook', 'secret': store_rule_secret('super-secret-value')},
        )
        token, _agent_id = _agent_token(company_id=company_id)
        list_response = _rpc(self.client, token, 'tools/list', req_id=2)
        self.assertEqual(list_response.status_code, 200)
        call_response = _rpc(
            self.client,
            token,
            'tools/call',
            {
                'name': 'api_v1_actions_rules_list',
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

    @patch('apps.authapi.mcp_identity.resolve_approval_state', return_value=None)
    @patch('apps.authapi.mcp_identity.create_approval_request', return_value={'id': 'approval-123'})
    def test_write_requires_approval_in_ask_mode(self, _create, _state):
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

    @patch('apps.authapi.mcp_identity.requests.post')
    def test_approval_state_transport_failure_refuses_write(self, mock_post):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        mock_post.side_effect = ConnectionError('refused')
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
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error'], 'identity_unavailable')

    @patch('apps.authapi.mcp_identity.requests.post')
    def test_approval_consume_uses_unified_contract(self, mock_post):
        import jwt

        def post_side_effect(url, **kwargs):
            if url.endswith('mcp-approval-state'):
                ok = MagicMock()
                ok.status_code = 200
                ok.raise_for_status = MagicMock()
                ok.json = MagicMock(return_value={'status': 'approved', 'approval_id': 'appr-9'})
                return ok
            ok = MagicMock()
            ok.status_code = 200
            ok.raise_for_status = MagicMock()
            ok.json = MagicMock(return_value={'consumed': True})
            return ok

        mock_post.side_effect = post_side_effect
        from apps.authapi.mcp_views import reset_mcp_server_cache

        reset_mcp_server_cache()
        token, _agent_id = _agent_token(access='ask')
        _rpc(
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
        consume_call = [call for call in mock_post.call_args_list if call.args[0].endswith('mcp-approval-consume')][0]
        self.assertEqual(consume_call.kwargs['json'], {'approval_id': 'appr-9'})
        assertion = consume_call.kwargs['headers']['Authorization'][7:]
        payload = jwt.decode(assertion, options={'verify_signature': False})
        self.assertEqual(payload['aud'], 'shellui-mcp-approval')
        self.assertEqual(payload['scope'], 'mcp.approval.create')
        self.assertIn('arguments_hash', payload)
        self.assertEqual(payload['service'], 'email')

    @patch('apps.authapi.mcp_identity.requests.post')
    def test_approval_consume_failure_refuses_write(self, mock_post):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        def post_side_effect(url, **kwargs):
            if url.endswith('mcp-approval-state'):
                ok = MagicMock()
                ok.status_code = 200
                ok.raise_for_status = MagicMock()
                ok.json = MagicMock(return_value={'status': 'approved', 'id': 'appr-1'})
                return ok
            fail = MagicMock()
            fail.status_code = 404
            return fail

        mock_post.side_effect = post_side_effect
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
        self.assertEqual(response.status_code, 503)

    @patch('apps.authapi.mcp_identity.requests.post')
    def test_approval_consume_200_without_literal_true_refuses_write(self, mock_post):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        def post_side_effect(url, **kwargs):
            if url.endswith('mcp-approval-state'):
                ok = MagicMock()
                ok.status_code = 200
                ok.raise_for_status = MagicMock()
                ok.json = MagicMock(return_value={'status': 'approved', 'id': 'appr-false'})
                return ok
            bad = MagicMock()
            bad.status_code = 200
            bad.raise_for_status = MagicMock()
            bad.json = MagicMock(return_value={'consumed': False})
            return bad

        mock_post.side_effect = post_side_effect
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
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error'], 'identity_unavailable')

    def test_revocation_global_grant_stale_forbidden(self):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        agent_id = str(uuid.uuid4())
        gcid = str(uuid.uuid4())
        self.mock_revocation_get.return_value = _revocation_ok_response(
            agents=[
                {
                    'agent_id': agent_id,
                    'global_company_id': gcid,
                    'company_binding_version': 0,
                    'grant_version': 1,
                }
            ],
        )
        reset_mcp_server_cache()
        reset_revocation_cache_for_tests()
        token, _ = _agent_token(
            agent_id=agent_id,
            company_id=42,
            global_company_id=gcid,
            company_binding_version=0,
            grant_version=2,
        )
        response = _rpc(self.client, token, 'tools/list')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'forbidden')

    def test_revocation_personal_row_global_token_forbidden(self):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        agent_id = str(uuid.uuid4())
        gcid = str(uuid.uuid4())
        self.mock_revocation_get.return_value = _revocation_ok_response(
            agents=[{'agent_id': agent_id, 'company_id': 42}],
        )
        reset_mcp_server_cache()
        reset_revocation_cache_for_tests()
        token, _ = _agent_token(
            agent_id=agent_id,
            company_id=42,
            global_company_id=gcid,
            company_binding_version=0,
            grant_version=0,
        )
        response = _rpc(self.client, token, 'tools/list')
        self.assertEqual(response.status_code, 403)

    def test_revocation_global_row_missing_token_claims_forbidden(self):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        agent_id = str(uuid.uuid4())
        gcid = str(uuid.uuid4())
        self.mock_revocation_get.return_value = _revocation_ok_response(
            agents=[
                {
                    'agent_id': agent_id,
                    'global_company_id': gcid,
                    'company_binding_version': 1,
                    'grant_version': 0,
                }
            ],
        )
        reset_mcp_server_cache()
        reset_revocation_cache_for_tests()
        token, _ = _agent_token(agent_id=agent_id, company_id=42)
        response = _rpc(self.client, token, 'tools/list')
        self.assertEqual(response.status_code, 403)

    def test_global_claims_require_complete_pair_in_token(self):
        agent_id = str(uuid.uuid4())
        claims = {
            'sub': f'agent:{agent_id}',
            'agent_id': agent_id,
            'company_id': 42,
            'scope': 'email',
            'mcp_access': 'read_only',
            'mcp_read': 'safe',
            'mcp_destructive': False,
            'exp': int(time.time()) + 600,
            'iss': ISSUER,
            'aud': MCP_RESOURCE,
            'global_company_id': str(uuid.uuid4()),
        }
        bad = jwt.encode(claims, SIGNING, algorithm='HS256')
        response = _rpc(self.client, bad, 'tools/list')
        self.assertEqual(response.status_code, 401)

    @patch('apps.authapi.mcp_identity.resolve_approval_state', return_value=None)
    @patch('apps.authapi.mcp_identity.create_approval_request', return_value=None)
    def test_destructive_full_mode_still_requires_approval(self, _create, _state):
        from apps.authapi.mcp_views import reset_mcp_server_cache
        from apps.email.models import EmailRule, EmailTemplate

        reset_mcp_server_cache()
        company_id = 42
        template = EmailTemplate.objects.create(
            company_id=company_id,
            template_key=f'company.{company_id}.mcp_del_rule',
            name='Copy',
            event_type='email.newsletter.requested',
            kind=EmailTemplate.KIND_EVENT,
            language='en',
            set='barebone',
        )
        rule = EmailRule.objects.create(
            company_id=company_id,
            service='email',
            event_type='email.newsletter.requested',
            enabled=True,
            language='en',
            template=template,
            recipient_mode='static',
            static_recipients=['owner@example.com'],
            built_in=False,
        )
        token, _agent_id = _agent_token(access='full', company_id=company_id, mcp_destructive=False)
        response = _rpc(
            self.client,
            token,
            'tools/call',
            {
                'name': 'api_v1_rules_destroy',
                'arguments': {
                    'path': '/api/v1/rules/{rule_id}',
                    'path_params': {'rule_id': str(rule.pk)},
                    'method': 'DELETE',
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()['result']
        self.assertTrue(result.get('isError'))
        self.assertIn('Destructive', result['content'][0]['text'])

    @patch('apps.email.views.ensure_builtin_rules')
    @patch('apps.authapi.mcp_identity.forward_call_log')
    def test_call_log_includes_safe_activity_metadata(self, mock_forward, _no_builtin):
        token, _agent_id = _agent_token(company_id=42)
        response = _rpc(
            self.client,
            token,
            'tools/call',
            {
                'name': 'api_v1_rules_list',
                'arguments': {
                    'path': '/api/v1/rules',
                    'method': 'GET',
                },
            },
            req_id=50,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(mock_forward.called)
        entry = mock_forward.call_args[0][0]
        act = entry.get('activity') or {}
        self.assertEqual(act.get('resource_path'), '/api/v1/rules')
        self.assertEqual(act.get('access_kind'), 'read')
        self.assertEqual(act.get('outcome'), 'ok')
        self.assertNotIn('arguments', act)

    @patch('apps.authapi.mcp_identity.requests.post')
    def test_forward_call_log_adds_company_name_from_profile(self, mock_post):
        CompanyProfile.objects.create(company_id=42, name='Audit Co MCP')
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=MagicMock())
        from apps.authapi.mcp_identity import forward_call_log

        forward_call_log(
            {
                'company_id': 42,
                'agent_id': str(uuid.uuid4()),
                'service': 'email',
                'tool': 'api_v1_rules_list',
                'method': 'GET',
                'path': '/api/v1/rules',
                'status': 200,
                'arguments': {},
                'activity': {'resource_path': '/api/v1/rules', 'access_kind': 'read', 'outcome': 'ok', 'duration_ms': 1},
            }
        )
        body = mock_post.call_args.kwargs['json']
        self.assertEqual(body.get('company_name'), 'Audit Co MCP')

    def test_grant_version_zero_forwarded_to_approval_assertion_when_supported(self):
        import inspect

        from apps.authapi.mcp_identity import _approval_assertion_kwargs
        from shellui_django.mcp.internal import build_mcp_approval_assertion

        if 'grant_version' not in inspect.signature(build_mcp_approval_assertion).parameters:
            self.skipTest('shellui-django build_mcp_approval_assertion has no grant_version yet.')
        payload = {
            'company_id': 42,
            'agent_id': str(uuid.uuid4()),
            'service': 'email',
            'action': 'api_v1_rules_create',
            'arguments_hash': 'abc',
            'grant_version': 0,
        }
        kwargs = _approval_assertion_kwargs(payload)
        self.assertEqual(kwargs.get('grant_version'), 0)

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
