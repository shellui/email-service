"""Shellui MCP ``POST /mcp`` endpoint."""

from __future__ import annotations

import json
import time
import uuid
from unittest.mock import MagicMock, call, patch

import jwt
from django.test import Client, TestCase, override_settings

from apps.authapi.mcp_identity import McpAgentCompanyMismatch, reset_revocation_cache_for_tests
from apps.email.models import CompanyProfile
from apps.authapi.mcp_openapi import MCP_EXPLICIT_REGISTRY, MCP_FORBIDDEN_OPERATION_IDS
from apps.authapi.tests.rs256_helpers import generate_test_rsa_jwks, sign_test_revocation_list_assertion
from shellui_django.mcp.internal import (
    REVOCATION_AUDIENCE,
    REVOCATION_CACHE_SECONDS,
    REVOCATION_SCOPE,
    build_mcp_approval_assertion,
    verify_mcp_internal_assertion,
)
from config.service_signing import MCP_DELEGATED_AUTHORITY_AUDIENCE, MCP_DELEGATED_AUTHORITY_SCOPE
from shellui_django.mcp.openapi_tools import tools_from_schema

ISSUER = 'https://id.shellui.test'
_TEST_JWKS, _TEST_PRIVATE_PEM, _TEST_KID = generate_test_rsa_jwks()
# This service's own key for internal MCP assertions to identity (config/service_signing.py).
_SERVICE_JWKS, _SERVICE_PRIVATE_PEM, _SERVICE_KID = generate_test_rsa_jwks()
SERVICE_ISSUER = 'https://email.test'
AUDIENCE = 'shellui-test'
MCP_RESOURCE = 'https://email.test/mcp'
SIGNING = 'mcp-endpoint-test-signing-key-0123456789'

# ``verify_agent_company_binding`` requires a live row for each agent JWT in tests.
_SNAPSHOT_AGENT_ID: str | None = None


def _snapshot_agent_id() -> str:
    global _SNAPSHOT_AGENT_ID
    if _SNAPSHOT_AGENT_ID is None:
        _SNAPSHOT_AGENT_ID = str(uuid.uuid4())
    return _SNAPSHOT_AGENT_ID


def _default_revocation_agents() -> list[dict]:
    return [{'agent_id': _snapshot_agent_id(), 'company_id': 42}]


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
    mcp_authority: str | None = None,
    mcp_destructive: bool = False,
) -> tuple[str, str]:
    agent_id = agent_id or _snapshot_agent_id()
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
    if mcp_authority is not None:
        claims['mcp_authority'] = mcp_authority
    elif global_company_id is not None:
        claims['mcp_authority'] = 'delegated'
    else:
        claims['mcp_authority'] = 'personal'
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
    max_cap = 1_000_000
    assertion = sign_test_revocation_list_assertion(
        private_pem=_TEST_PRIVATE_PEM,
        kid=_TEST_KID,
        issuer=ISSUER,
        revoked_agent_ids=[],
        agents=agents if agents is not None else _default_revocation_agents(),
        cache_seconds=REVOCATION_CACHE_SECONDS,
        max_active_grant_version=max_cap,
    )
    body = {
        'revoked_agent_ids': [],
        'cache_seconds': REVOCATION_CACHE_SECONDS,
        'max_active_grant_version': max_cap,
        'revocation_assertion': assertion,
        'agents': agents if agents is not None else _default_revocation_agents(),
    }
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=body)
    return response


def _live_delegated_authority_response(*, row: dict) -> MagicMock:
    """Identity live delegated authority: flat JSON object (no ``agent`` wrapper)."""
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=row)
    return response


def _delegated_live_row_from_snapshot_row(
    match: dict,
    *,
    agent_id: str,
) -> dict:
    gcid = match.get('global_company_id')
    if not gcid:
        raise ValueError('expected global row')
    aid = str(agent_id)
    gaid = str(match.get('global_agent_id') or '').strip()
    if not gaid:
        gaid = str(uuid.uuid5(uuid.NAMESPACE_URL, f'mcp-delegated-global-agent:{aid}'))
    return {
        'authorized': True,
        'revoked': False,
        'paused': False,
        'agent_id': aid,
        'global_agent_id': gaid,
        'company_id': int(match['company_id']),
        'global_company_id': str(gcid),
        'company_binding_version': int(match['company_binding_version']),
        'grant_version': int(match['grant_version']),
    }


def make_identity_get_side_effect(agents_fn):
    """OAuth redirects and revocation poll (GET only)."""

    def side_effect(url, **kwargs):
        s = str(url)
        if 'oauth-redirects' in s:
            r = MagicMock()
            r.status_code = 200
            r.content = b'[]'
            r.json = MagicMock(return_value=[])
            return r
        if 'mcp-revocations' in s:
            return _revocation_ok_response(agents=agents_fn())
        raise AssertionError(f'unexpected GET url in test mock: {url}')

    return side_effect


def make_identity_post_side_effect(agents_fn):
    """Live delegated authority lookup (POST)."""

    def side_effect(url, **kwargs):
        s = str(url)
        if 'mcp-agent-delegated-authority' in s:
            headers = kwargs.get('headers') or {}
            auth = headers.get('Authorization', '')
            if not auth.startswith('Bearer '):
                raise AssertionError('delegated authority request missing bearer')
            delegation_tok = auth[7:]
            delegation_claims = jwt.decode(delegation_tok, options={'verify_signature': False})
            from apps.authapi.mcp_identity import identity_origin

            if delegation_claims.get('aud') != MCP_DELEGATED_AUTHORITY_AUDIENCE:
                raise AssertionError('wrong aud on delegated authority assertion')
            if delegation_claims.get('scope') != MCP_DELEGATED_AUTHORITY_SCOPE:
                raise AssertionError('wrong scope on delegated authority assertion')
            if delegation_claims.get('target') != identity_origin().rstrip('/'):
                raise AssertionError('wrong target on delegated authority assertion')
            body = kwargs.get('json') or {}
            aid = str(body.get('agent_id') or '')
            cc = body.get('company_id')
            ggc = body.get('global_company_id')
            cbv = body.get('company_binding_version')
            gv = body.get('grant_version')
            if not isinstance(body, dict):
                raise AssertionError('delegated authority json must be an object')
            allowed_keys = {'agent_id', 'company_id', 'global_company_id', 'company_binding_version', 'grant_version'}
            if set(body.keys()) - allowed_keys:
                raise AssertionError('delegated authority POST must not echo unknown keys')
            if cc is None or ggc is None or cbv is None or gv is None:
                raise AssertionError('delegated authority POST must include full binding json')
            rows = agents_fn()
            match = next(
                (r for r in rows if isinstance(r, dict) and str(r.get('agent_id') or '') == aid),
                None,
            )
            if match is None:
                nf = MagicMock()
                nf.status_code = 404
                return nf
            try:
                row = _delegated_live_row_from_snapshot_row(match, agent_id=aid)
            except ValueError:
                nf = MagicMock()
                nf.status_code = 404
                return nf
            if (
                int(cc) != int(match['company_id'])
                or str(ggc) != str(match['global_company_id'])
                or int(cbv) != int(match['company_binding_version'])
                or int(gv) != int(match['grant_version'])
            ):
                nf = MagicMock()
                nf.status_code = 404
                return nf
            return _live_delegated_authority_response(row=row)
        if any(
            part in s
            for part in (
                'mcp-call-logs',
                'mcp-approval-state',
                'mcp-approval-consume',
                'mcp-approval-requests',
            )
        ):
            ok = MagicMock()
            ok.status_code = 200
            ok.raise_for_status = MagicMock()
            ok.json = MagicMock(return_value={})
            return ok
        raise AssertionError(f'unexpected POST url in test mock: {url}')

    return side_effect


@override_settings(**MCP_SETTINGS)
class McpEndpointTests(TestCase):
    def setUp(self):
        global _SNAPSHOT_AGENT_ID
        from apps.authapi.mcp_views import reset_mcp_server_cache

        _SNAPSHOT_AGENT_ID = str(uuid.uuid4())
        reset_mcp_server_cache()
        reset_revocation_cache_for_tests()
        self.client = Client()
        self._revocation_agents_override: list[dict] | None = None

        def _agents_fn():
            return self._revocation_agents_override if self._revocation_agents_override is not None else _default_revocation_agents()

        self.revocation_patcher = patch(
            'apps.authapi.mcp_identity.requests.get',
            side_effect=make_identity_get_side_effect(_agents_fn),
        )
        self.mock_revocation_get = self.revocation_patcher.start()
        self.identity_post_patcher = patch(
            'apps.authapi.mcp_identity.requests.post',
            side_effect=make_identity_post_side_effect(_agents_fn),
        )
        self.mock_identity_post = self.identity_post_patcher.start()

    def tearDown(self):
        self._revocation_agents_override = None
        self.revocation_patcher.stop()
        self.identity_post_patcher.stop()

    def _set_revocation_agents(self, agents: list[dict] | None):
        """Point revocation + delegated-authority mocks at a fixed ``agents[]`` snapshot."""

        self._revocation_agents_override = agents

        def _agents_fn():
            return self._revocation_agents_override if self._revocation_agents_override is not None else _default_revocation_agents()

        self.mock_revocation_get.side_effect = make_identity_get_side_effect(_agents_fn)
        self.mock_identity_post.side_effect = make_identity_post_side_effect(_agents_fn)

    def _default_identity_post_chain(self):
        """Delegated authority, approvals, call logs (one patched ``requests.post``)."""

        def _agents_fn():
            return self._revocation_agents_override if self._revocation_agents_override is not None else _default_revocation_agents()

        delegated = make_identity_post_side_effect(_agents_fn)

        def chain(url, **kwargs):
            s = str(url)
            if 'mcp-agent-delegated-authority' in s:
                return delegated(url, **kwargs)
            if 'mcp-approval-state' in s or 'mcp-approval-consume' in s or 'mcp-approval-requests' in s or 'mcp-call-logs' in s:
                ok = MagicMock()
                ok.status_code = 200
                ok.raise_for_status = MagicMock()
                ok.json = MagicMock(return_value={})
                return ok
            raise AssertionError(f'unexpected POST in default chain: {url}')

        return chain

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
        self._set_revocation_agents(None)

    def test_revocation_404_refuses_mcp(self):
        response404 = MagicMock()
        response404.status_code = 404

        def _404_side_effect(url, **kwargs):
            s = str(url)
            if 'oauth-redirects' in s:
                r = MagicMock()
                r.status_code = 200
                r.content = b'[]'
                r.json = MagicMock(return_value=[])
                return r
            if 'mcp-revocations' in s:
                return response404
            raise AssertionError(url)

        self.mock_revocation_get.side_effect = _404_side_effect
        token, _agent_id = _agent_token()
        response = _rpc(self.client, token, 'tools/list')
        self.assertEqual(response.status_code, 503)
        self._set_revocation_agents(None)

    def test_revocation_unsigned_payload_refuses_mcp(self):
        bad = MagicMock()
        bad.status_code = 200
        bad.raise_for_status = MagicMock()
        bad.json = MagicMock(return_value={'revoked_agent_ids': [], 'cache_seconds': 30})

        def _bad_side_effect(url, **kwargs):
            s = str(url)
            if 'oauth-redirects' in s:
                r = MagicMock()
                r.status_code = 200
                r.content = b'[]'
                r.json = MagicMock(return_value=[])
                return r
            if 'mcp-revocations' in s:
                return bad
            raise AssertionError(url)

        self.mock_revocation_get.side_effect = _bad_side_effect
        token, _agent_id = _agent_token()
        response = _rpc(self.client, token, 'tools/list')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error'], 'identity_unavailable')
        self._set_revocation_agents(None)

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

    @patch('apps.authapi.mcp_identity.resolve_approval_state', return_value='not_found')
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
        self.assertEqual(result.get('meta', {}).get('approval_request_id'), 'approval-123')

    @patch('apps.authapi.mcp_identity.requests.post')
    def test_approval_state_transport_failure_refuses_write(self, mock_post):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        delegated = self._default_identity_post_chain()

        def post_side_effect(url, **kwargs):
            s = str(url)
            if 'mcp-approval-state' in s:
                raise ConnectionError('refused')
            return delegated(url, **kwargs)

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
        mock_post.side_effect = self._default_identity_post_chain()

    @patch('apps.authapi.mcp_identity.requests.post')
    def test_approval_consume_uses_unified_contract(self, mock_post):
        import jwt

        delegated = self._default_identity_post_chain()

        def post_side_effect(url, **kwargs):
            s = str(url)
            if 'mcp-approval-state' in s:
                ok = MagicMock()
                ok.status_code = 200
                ok.raise_for_status = MagicMock()
                ok.json = MagicMock(return_value={'status': 'approved', 'approval_id': 'appr-9'})
                return ok
            if 'mcp-approval-consume' in s:
                ok = MagicMock()
                ok.status_code = 200
                ok.raise_for_status = MagicMock()
                ok.json = MagicMock(return_value={'consumed': True})
                return ok
            return delegated(url, **kwargs)

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
        mock_post.side_effect = self._default_identity_post_chain()

    @patch('apps.authapi.mcp_identity.requests.post')
    def test_approval_consume_failure_refuses_write(self, mock_post):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        delegated = self._default_identity_post_chain()

        def post_side_effect(url, **kwargs):
            s = str(url)
            if 'mcp-approval-state' in s:
                ok = MagicMock()
                ok.status_code = 200
                ok.raise_for_status = MagicMock()
                ok.json = MagicMock(return_value={'status': 'approved', 'id': 'appr-1'})
                return ok
            if 'mcp-approval-consume' in s:
                fail = MagicMock()
                fail.status_code = 404
                return fail
            return delegated(url, **kwargs)

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
        mock_post.side_effect = self._default_identity_post_chain()

    @patch('apps.authapi.mcp_identity.requests.post')
    def test_approval_consume_200_without_literal_true_refuses_write(self, mock_post):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        delegated = self._default_identity_post_chain()

        def post_side_effect(url, **kwargs):
            s = str(url)
            if 'mcp-approval-state' in s:
                ok = MagicMock()
                ok.status_code = 200
                ok.raise_for_status = MagicMock()
                ok.json = MagicMock(return_value={'status': 'approved', 'id': 'appr-false'})
                return ok
            if 'mcp-approval-consume' in s:
                bad = MagicMock()
                bad.status_code = 200
                bad.raise_for_status = MagicMock()
                bad.json = MagicMock(return_value={'consumed': False})
                return bad
            return delegated(url, **kwargs)

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
        mock_post.side_effect = self._default_identity_post_chain()

    def test_delegated_live_authority_grant_mismatch_forbidden(self):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        agent_id = str(uuid.uuid4())
        gcid = str(uuid.uuid4())
        agents = [
            {
                'agent_id': agent_id,
                'company_id': 42,
                'global_company_id': gcid,
                'company_binding_version': 0,
                'grant_version': 1,
            }
        ]
        self._set_revocation_agents(agents)

        delegated_handler = make_identity_post_side_effect(lambda: agents)

        def post_split(url, **kwargs):
            s = str(url)
            if 'mcp-agent-delegated-authority' in s:
                row = _delegated_live_row_from_snapshot_row(agents[0], agent_id=agent_id)
                row['grant_version'] = 99
                r = MagicMock()
                r.status_code = 200
                r.raise_for_status = MagicMock()
                r.json = MagicMock(return_value=row)
                return r
            return delegated_handler(url, **kwargs)

        self.mock_identity_post.side_effect = post_split
        reset_mcp_server_cache()
        reset_revocation_cache_for_tests()
        token, _ = _agent_token(
            agent_id=agent_id,
            company_id=42,
            global_company_id=gcid,
            company_binding_version=0,
            grant_version=1,
        )
        response = _rpc(self.client, token, 'tools/list')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'forbidden')
        self._set_revocation_agents(None)

    def test_delegated_authority_binding_missing_404(self):
        """Identity ``404`` for a signed delegated-authority request means no binding for this agent."""

        from apps.authapi.mcp_views import reset_mcp_server_cache

        agent_id = str(uuid.uuid4())
        gcid = str(uuid.uuid4())
        agents = [
            {
                'agent_id': agent_id,
                'company_id': 42,
                'global_company_id': gcid,
                'company_binding_version': 0,
                'grant_version': 0,
            }
        ]
        self._set_revocation_agents(agents)

        def post_404(url, **kwargs):
            s = str(url)
            if 'mcp-agent-delegated-authority' in s:
                nf = MagicMock()
                nf.status_code = 404
                return nf
            if any(
                part in s
                for part in (
                    'mcp-call-logs',
                    'mcp-approval-state',
                    'mcp-approval-consume',
                    'mcp-approval-requests',
                )
            ):
                ok = MagicMock()
                ok.status_code = 200
                ok.raise_for_status = MagicMock()
                ok.json = MagicMock(return_value={})
                return ok
            raise AssertionError(f'unexpected POST: {url}')

        self.mock_identity_post.side_effect = post_404
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
        self.assertEqual(response.json().get('error_code'), 'authority_binding_missing')
        self._set_revocation_agents(None)

    def test_delegated_authority_service_assertion_401_identity_unavailable(self):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        agent_id = str(uuid.uuid4())
        gcid = str(uuid.uuid4())
        agents = [
            {
                'agent_id': agent_id,
                'company_id': 42,
                'global_company_id': gcid,
                'company_binding_version': 0,
                'grant_version': 0,
            }
        ]
        self._set_revocation_agents(agents)

        def post_401(url, **kwargs):
            s = str(url)
            if 'mcp-agent-delegated-authority' in s:
                nf = MagicMock()
                nf.status_code = 401
                return nf
            if any(
                part in s
                for part in (
                    'mcp-call-logs',
                    'mcp-approval-state',
                    'mcp-approval-consume',
                    'mcp-approval-requests',
                )
            ):
                ok = MagicMock()
                ok.status_code = 200
                ok.raise_for_status = MagicMock()
                ok.json = MagicMock(return_value={})
                return ok
            raise AssertionError(f'unexpected POST: {url}')

        self.mock_identity_post.side_effect = post_401
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
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json().get('error'), 'identity_unavailable')
        self._set_revocation_agents(None)

    def test_live_delegated_authority_nested_agent_wrapper_rejected(self):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        agent_id = str(uuid.uuid4())
        gcid = str(uuid.uuid4())
        agents = [
            {
                'agent_id': agent_id,
                'company_id': 42,
                'global_company_id': gcid,
                'company_binding_version': 0,
                'grant_version': 0,
            }
        ]
        self._set_revocation_agents(agents)

        base_delegated = make_identity_post_side_effect(lambda: agents)

        def post_nested(url, **kwargs):
            s = str(url)
            if 'mcp-agent-delegated-authority' in s:
                inner = _delegated_live_row_from_snapshot_row(agents[0], agent_id=agent_id)
                r = MagicMock()
                r.status_code = 200
                r.raise_for_status = MagicMock()
                r.json = MagicMock(return_value={'agent': inner})
                return r
            return base_delegated(url, **kwargs)

        self.mock_identity_post.side_effect = post_nested
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
        self._set_revocation_agents(None)

    def test_live_delegated_extra_keys_rejected(self):
        """Identity #135 success responses use a fixed key set; unknown keys are refused."""
        from apps.authapi.mcp_views import reset_mcp_server_cache

        agent_id = str(uuid.uuid4())
        gcid = str(uuid.uuid4())
        agents = [
            {
                'agent_id': agent_id,
                'company_id': 42,
                'global_company_id': gcid,
                'company_binding_version': 0,
                'grant_version': 0,
            }
        ]
        self._set_revocation_agents(agents)
        base_delegated = make_identity_post_side_effect(lambda: agents)

        def post_extra(url, **kwargs):
            s = str(url)
            if 'mcp-agent-delegated-authority' in s:
                row = _delegated_live_row_from_snapshot_row(agents[0], agent_id=agent_id)
                row['legacy_active'] = True
                r = MagicMock()
                r.status_code = 200
                r.raise_for_status = MagicMock()
                r.json = MagicMock(return_value=row)
                return r
            return base_delegated(url, **kwargs)

        self.mock_identity_post.side_effect = post_extra
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
        self._set_revocation_agents(None)

    def test_live_delegated_missing_authorized_invalid(self):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        agent_id = str(uuid.uuid4())
        gcid = str(uuid.uuid4())
        agents = [
            {
                'agent_id': agent_id,
                'company_id': 42,
                'global_company_id': gcid,
                'company_binding_version': 0,
                'grant_version': 0,
            }
        ]
        self._set_revocation_agents(agents)
        base_delegated = make_identity_post_side_effect(lambda: agents)

        def post_missing_auth(url, **kwargs):
            s = str(url)
            if 'mcp-agent-delegated-authority' in s:
                row = _delegated_live_row_from_snapshot_row(agents[0], agent_id=agent_id)
                del row['authorized']
                r = MagicMock()
                r.status_code = 200
                r.raise_for_status = MagicMock()
                r.json = MagicMock(return_value=row)
                return r
            return base_delegated(url, **kwargs)

        self.mock_identity_post.side_effect = post_missing_auth
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
        self._set_revocation_agents(None)

    def test_live_delegated_identity_http_403_forbidden(self):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        agent_id = str(uuid.uuid4())
        gcid = str(uuid.uuid4())
        agents = [
            {
                'agent_id': agent_id,
                'company_id': 42,
                'global_company_id': gcid,
                'company_binding_version': 0,
                'grant_version': 0,
            }
        ]
        self._set_revocation_agents(agents)

        def post_403(url, **kwargs):
            s = str(url)
            if 'mcp-agent-delegated-authority' in s:
                nf = MagicMock()
                nf.status_code = 403
                return nf
            if any(
                part in s
                for part in (
                    'mcp-call-logs',
                    'mcp-approval-state',
                    'mcp-approval-consume',
                    'mcp-approval-requests',
                )
            ):
                ok = MagicMock()
                ok.status_code = 200
                ok.raise_for_status = MagicMock()
                ok.json = MagicMock(return_value={})
                return ok
            raise AssertionError(f'unexpected POST: {url}')

        self.mock_identity_post.side_effect = post_403
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
        self.assertEqual(response.json().get('error'), 'forbidden')
        self._set_revocation_agents(None)

    def test_live_delegated_bool_flags_must_be_json_booleans(self):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        agent_id = str(uuid.uuid4())
        gcid = str(uuid.uuid4())
        agents = [
            {
                'agent_id': agent_id,
                'company_id': 42,
                'global_company_id': gcid,
                'company_binding_version': 0,
                'grant_version': 0,
            }
        ]
        self._set_revocation_agents(agents)
        base_delegated = make_identity_post_side_effect(lambda: agents)

        def post_strings(url, **kwargs):
            s = str(url)
            if 'mcp-agent-delegated-authority' in s:
                row = _delegated_live_row_from_snapshot_row(agents[0], agent_id=agent_id)
                row['authorized'] = 'true'
                r = MagicMock()
                r.status_code = 200
                r.raise_for_status = MagicMock()
                r.json = MagicMock(return_value=row)
                return r
            return base_delegated(url, **kwargs)

        self.mock_identity_post.side_effect = post_strings
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
        self._set_revocation_agents(None)

    def test_live_delegated_global_agent_id_must_differ_from_regional(self):
        """SaaS catalog ``global_agent_id`` must not equal the regional ``agent_id``."""
        from apps.authapi.mcp_views import reset_mcp_server_cache

        agent_id = str(uuid.uuid4())
        gcid = str(uuid.uuid4())
        agents = [
            {
                'agent_id': agent_id,
                'global_agent_id': agent_id,
                'company_id': 42,
                'global_company_id': gcid,
                'company_binding_version': 0,
                'grant_version': 0,
            }
        ]
        self._set_revocation_agents(agents)
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
        self._set_revocation_agents(None)

    def test_live_delegated_global_agent_id_catalog_uuid_only_not_token_matched(self):
        """Identity returns SaaS ``global_agent_id``; the agent JWT has no matching claim to check."""
        from apps.authapi.mcp_views import reset_mcp_server_cache

        agent_id = str(uuid.uuid4())
        gcid = str(uuid.uuid4())
        agents = [
            {
                'agent_id': agent_id,
                'company_id': 42,
                'global_company_id': gcid,
                'company_binding_version': 0,
                'grant_version': 0,
            }
        ]
        self._set_revocation_agents(agents)
        base_delegated = make_identity_post_side_effect(lambda: agents)

        def post_bad_global(url, **kwargs):
            s = str(url)
            if 'mcp-agent-delegated-authority' in s:
                row = _delegated_live_row_from_snapshot_row(agents[0], agent_id=agent_id)
                row['global_agent_id'] = str(uuid.uuid4())
                r = MagicMock()
                r.status_code = 200
                r.raise_for_status = MagicMock()
                r.json = MagicMock(return_value=row)
                return r
            return base_delegated(url, **kwargs)

        self.mock_identity_post.side_effect = post_bad_global
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
        self.assertEqual(response.status_code, 200)
        self._set_revocation_agents(None)

    def test_revocation_global_grant_stale_forbidden(self):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        agent_id = str(uuid.uuid4())
        gcid = str(uuid.uuid4())
        agents = [
            {
                'agent_id': agent_id,
                'global_company_id': gcid,
                'company_binding_version': 0,
                'grant_version': 1,
            }
        ]
        self._set_revocation_agents(agents)
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
        self._set_revocation_agents(None)

    def test_revocation_personal_row_global_token_forbidden(self):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        agent_id = str(uuid.uuid4())
        gcid = str(uuid.uuid4())
        self._set_revocation_agents([{'agent_id': agent_id, 'company_id': 42}])
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
        self._set_revocation_agents(None)

    def test_revocation_global_row_missing_token_claims_forbidden(self):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        agent_id = str(uuid.uuid4())
        gcid = str(uuid.uuid4())
        self._set_revocation_agents(
            [
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
        self._set_revocation_agents(None)

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
            'mcp_authority': 'delegated',
            'exp': int(time.time()) + 600,
            'iss': ISSUER,
            'aud': MCP_RESOURCE,
            'global_company_id': str(uuid.uuid4()),
        }
        bad = jwt.encode(claims, SIGNING, algorithm='HS256')
        response = _rpc(self.client, bad, 'tools/list')
        self.assertEqual(response.status_code, 401)

    def test_agent_token_invalid_mcp_access_rejected(self):
        claims = jwt.decode(_agent_token()[0], options={'verify_signature': False})
        claims['mcp_access'] = 'superuser'
        bad = jwt.encode(claims, SIGNING, algorithm='HS256')
        response = _rpc(self.client, bad, 'tools/list')
        self.assertEqual(response.status_code, 401)

    def test_agent_token_mcp_destructive_string_rejected(self):
        claims = jwt.decode(_agent_token()[0], options={'verify_signature': False})
        claims['mcp_destructive'] = 'true'
        bad = jwt.encode(claims, SIGNING, algorithm='HS256')
        response = _rpc(self.client, bad, 'tools/list')
        self.assertEqual(response.status_code, 401)

    def test_agent_token_company_id_string_rejected(self):
        claims = jwt.decode(_agent_token()[0], options={'verify_signature': False})
        claims['company_id'] = '42'
        bad = jwt.encode(claims, SIGNING, algorithm='HS256')
        response = _rpc(self.client, bad, 'tools/list')
        self.assertEqual(response.status_code, 401)

    def test_agent_token_company_id_bool_rejected(self):
        claims = jwt.decode(_agent_token()[0], options={'verify_signature': False})
        claims['company_id'] = True
        bad = jwt.encode(claims, SIGNING, algorithm='HS256')
        response = _rpc(self.client, bad, 'tools/list')
        self.assertEqual(response.status_code, 401)

    def test_agent_token_company_id_float_rejected(self):
        claims = jwt.decode(_agent_token()[0], options={'verify_signature': False})
        claims['company_id'] = 42.0
        bad = jwt.encode(claims, SIGNING, algorithm='HS256')
        response = _rpc(self.client, bad, 'tools/list')
        self.assertEqual(response.status_code, 401)

    def test_mcp_authority_personal_with_global_triplet_rejected(self):
        gcid = str(uuid.uuid4())
        token, _ = _agent_token()
        claims = jwt.decode(token, options={'verify_signature': False})
        claims['mcp_authority'] = 'personal'
        claims['global_company_id'] = gcid
        claims['company_binding_version'] = 0
        claims['grant_version'] = 0
        bad = jwt.encode(claims, SIGNING, algorithm='HS256')
        response = _rpc(self.client, bad, 'tools/list')
        self.assertEqual(response.status_code, 401)

    def test_agent_token_grant_version_negative_rejected(self):
        token, _ = _agent_token(grant_version=-1)
        response = _rpc(self.client, token, 'tools/list')
        self.assertEqual(response.status_code, 401)

    def test_agent_token_global_company_id_invalid_uuid_rejected(self):
        claims = jwt.decode(_agent_token()[0], options={'verify_signature': False})
        claims['global_company_id'] = 'not-a-uuid'
        claims['company_binding_version'] = 1
        claims['grant_version'] = 1
        bad = jwt.encode(claims, SIGNING, algorithm='HS256')
        response = _rpc(self.client, bad, 'tools/list')
        self.assertEqual(response.status_code, 401)

    def test_agent_token_nil_global_uuid_rejected(self):
        claims = jwt.decode(_agent_token()[0], options={'verify_signature': False})
        claims['global_company_id'] = '00000000-0000-0000-0000-000000000000'
        claims['company_binding_version'] = 0
        claims['grant_version'] = 0
        bad = jwt.encode(claims, SIGNING, algorithm='HS256')
        response = _rpc(self.client, bad, 'tools/list')
        self.assertEqual(response.status_code, 401)

    def test_no_live_snapshot_row_for_agent_denied(self):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        agent_id = str(uuid.uuid4())
        gcid = str(uuid.uuid4())
        self._set_revocation_agents([])
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
        self.assertEqual(response.json().get('error_code'), 'authority_row_missing')
        self._set_revocation_agents(None)

    def test_revocation_duplicate_agent_rows_forbidden(self):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        agent_id = str(uuid.uuid4())
        self._set_revocation_agents(
            [
                {'agent_id': agent_id, 'company_id': 42},
                {'agent_id': agent_id, 'company_id': 42},
            ],
        )
        reset_mcp_server_cache()
        reset_revocation_cache_for_tests()
        token, _ = _agent_token(agent_id=agent_id, company_id=42)
        response = _rpc(self.client, token, 'tools/list')
        self.assertEqual(response.status_code, 403)
        self._set_revocation_agents(None)

    def test_revocation_regional_company_mismatch_forbidden(self):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        agent_id = str(uuid.uuid4())
        self._set_revocation_agents([{'agent_id': agent_id, 'company_id': 99}])
        reset_mcp_server_cache()
        reset_revocation_cache_for_tests()
        token, _ = _agent_token(agent_id=agent_id, company_id=42)
        response = _rpc(self.client, token, 'tools/list')
        self.assertEqual(response.status_code, 403)
        self._set_revocation_agents(None)

    @patch('apps.authapi.mcp_identity.resolve_approval_state', return_value='unknown')
    @patch('apps.authapi.mcp_identity.create_approval_request')
    def test_approval_unknown_state_short_circuits_no_create(self, mock_create, _state):
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
        self.assertIn('denied', result['content'][0]['text'].lower())
        mock_create.assert_not_called()

    @patch('apps.authapi.mcp_identity.requests.post')
    def test_create_approval_request_posts_empty_json(self, mock_post):
        from apps.authapi.mcp_identity import create_approval_request

        mock_post.return_value = MagicMock(status_code=200, raise_for_status=MagicMock(), json=MagicMock(return_value={'id': str(uuid.uuid4())}))
        create_approval_request(
            {
                'company_id': 42,
                'agent_id': str(uuid.uuid4()),
                'service': 'email',
                'action': 'api_v1_rules_create',
                'arguments_hash': 'abc',
            }
        )
        body = mock_post.call_args.kwargs.get('json')
        self.assertEqual(body, {})

    @patch('apps.authapi.mcp_identity.resolve_approval_state', return_value='not_found')
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
        self.assertEqual(entry.get('resource_path'), '/api/v1/rules')
        self.assertEqual(entry.get('access_kind'), 'read')
        self.assertEqual(entry.get('outcome'), 'allowed')
        self.assertNotIn('arguments', entry)
        self.assertNotIn('activity', entry)

    @patch('apps.authapi.mcp_identity.requests.post')
    def test_forward_call_log_identity_payload_shape(self, mock_post):
        mock_post.return_value = MagicMock(status_code=201, raise_for_status=MagicMock())
        from apps.authapi.mcp_identity import forward_call_log

        forward_call_log(
            {
                'company_id': 42,
                'agent_id': str(uuid.uuid4()),
                'service': 'email',
                'tool': 'api_v1_rules_list',
                'method': 'GET',
                'resource_path': '/api/v1/rules',
                'access_kind': 'read',
                'outcome': 'allowed',
                'duration_ms': 1,
                'http_status': 200,
            }
        )
        body = mock_post.call_args.kwargs['json']
        self.assertNotIn('arguments', body)
        self.assertNotIn('company_name', body)
        self.assertEqual(body.get('path'), '/mcp')
        self.assertEqual(body.get('method'), 'POST')
        self.assertEqual(body.get('status'), 200)
        self.assertNotIn('resource_path', body)
        self.assertNotIn('http_status', body)
        self.assertEqual(body.get('result'), '')

    @patch('apps.authapi.mcp_identity.verify_live_delegated_authority_against_token')
    @patch('apps.authapi.mcp_identity.requests.post')
    def test_forward_call_log_rewrites_outcome_when_live_delegated_check_fails(
        self, mock_post, mock_verify_live
    ):
        mock_verify_live.side_effect = McpAgentCompanyMismatch('authority_snapshot_mismatch')
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=MagicMock())
        from apps.authapi.mcp_identity import forward_call_log

        forward_call_log(
            {
                'company_id': 42,
                'agent_id': str(uuid.uuid4()),
                'global_company_id': str(uuid.uuid4()),
                'company_binding_version': 0,
                'grant_version': 1,
                'service': 'email',
                'tool': 'api_v1_rules_list',
                'method': 'GET',
                'resource_path': '/api/v1/rules',
                'access_kind': 'read',
                'outcome': 'allowed',
                'duration_ms': 1,
            }
        )
        body = mock_post.call_args.kwargs['json']
        self.assertEqual(body.get('outcome'), 'denied')
        self.assertEqual(body.get('error_code'), 'authority_mismatch')

    def test_approval_assertion_personal_optional_grant_version_zero(self):
        import jwt

        from apps.authapi.mcp_identity import _approval_assertion_kwargs

        payload = {
            'company_id': 42,
            'agent_id': str(uuid.uuid4()),
            'service': 'email',
            'action': 'api_v1_rules_create',
            'arguments_hash': 'abc',
            'grant_version': 0,
        }
        kwargs = _approval_assertion_kwargs(payload)
        token = build_mcp_approval_assertion(**kwargs)
        decoded = jwt.decode(token, options={'verify_signature': False})
        self.assertEqual(decoded.get('grant_version'), 0)
        self.assertNotIn('global_company_id', decoded)

    def test_approval_assertion_delegation_full_triple(self):
        import jwt

        from apps.authapi.mcp_identity import _approval_assertion_kwargs

        gcid = str(uuid.uuid4())
        payload = {
            'company_id': 42,
            'agent_id': str(uuid.uuid4()),
            'service': 'email',
            'action': 'api_v1_rules_create',
            'arguments_hash': 'abc',
            'global_company_id': gcid,
            'company_binding_version': 0,
            'grant_version': 0,
        }
        kwargs = _approval_assertion_kwargs(payload)
        token = build_mcp_approval_assertion(**kwargs)
        decoded = jwt.decode(token, options={'verify_signature': False})
        self.assertEqual(decoded['global_company_id'], gcid)
        self.assertEqual(decoded['company_binding_version'], 0)
        self.assertEqual(decoded['grant_version'], 0)

    def test_approval_assertion_delegation_incomplete_raises(self):
        from apps.authapi.mcp_identity import McpIdentityUnavailable, _approval_assertion_extra_kwargs

        gcid = str(uuid.uuid4())
        with self.assertRaises(McpIdentityUnavailable):
            _approval_assertion_extra_kwargs(
                {
                    'global_company_id': gcid,
                }
            )
        with self.assertRaises(McpIdentityUnavailable):
            _approval_assertion_extra_kwargs(
                {'global_company_id': gcid, 'company_binding_version': 0},
            )

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
