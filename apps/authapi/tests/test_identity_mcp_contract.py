"""Parity checks against public Identity ``identity-service`` internal MCP contracts (raw source).

Delegated authority response body (every HTTP 200 uses the same 15 keys):
https://raw.githubusercontent.com/shellui/identity-service/d7fe351/apps/agents/internal_views.py
(``_delegated_authority_fixed_payload`` / ``McpInternalDelegatedAgentAuthorityView``.)

Call log ingest body shape exercised via ``forward_call_log``:
https://raw.githubusercontent.com/shellui/identity-service/d7fe351/apps/agents/internal_views.py
(``McpInternalCallLogView``); path must canonicalize to ``POST /mcp`` for email (``call_log_internal.fixed_resource_label_for_service``).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from apps.authapi.mcp_identity import _DELEGATED_AUTHORITY_SUCCESS_KEYS, forward_call_log
from apps.authapi.tests.test_mcp_endpoint import MCP_SETTINGS

# Copied key set from Identity ``_delegated_authority_fixed_payload`` (15 keys, every 200).
_IDENTITY_DELEGATED_AGENT_AUTHORITY_200_KEYS = frozenset(
    {
        'authorized',
        'active',
        'revoked',
        'paused',
        'agent_id',
        'company_id',
        'global_agent_id',
        'global_company_id',
        'grant_version',
        'company_binding_version',
        'access_mode',
        'read_level',
        'allow_destructive',
        'services_allowed',
        'revoked_at',
    }
)


class IdentityDelegatedAuthorityContractTests(SimpleTestCase):
    def test_email_parser_expects_same_keys_as_identity_success_payload(self):
        self.assertEqual(_DELEGATED_AUTHORITY_SUCCESS_KEYS, _IDENTITY_DELEGATED_AGENT_AUTHORITY_200_KEYS)


class IdentityCallLogContractTests(SimpleTestCase):
    @override_settings(**MCP_SETTINGS)
    @patch('apps.authapi.mcp_identity.requests.post')
    def test_forward_call_log_posts_identity_compatible_body(self, mock_post):
        mock_post.return_value = MagicMock(status_code=201, raise_for_status=MagicMock())
        forward_call_log(
            {
                'company_id': 42,
                'agent_id': str(uuid.uuid4()),
                'grant_version': 0,
                'service': 'email',
                'tool': 'api_v1_rules_list',
                'method': 'GET',
                'resource_path': '/api/v1/rules',
                'access_kind': 'read',
                'outcome': 'allowed',
                'duration_ms': 12,
                'http_status': 200,
                'error_code': 'forbidden',
                'approval_request_id': str(uuid.uuid4()),
            }
        )
        body = mock_post.call_args.kwargs['json']
        self.assertEqual(body['method'], 'POST')
        self.assertEqual(body['path'], '/mcp')
        self.assertEqual(body['status'], 200)
        self.assertEqual(body['result'], '')
        self.assertEqual(body['outcome'], 'allowed')
        self.assertEqual(body['access_kind'], 'read')
        self.assertIn('approval_id', body)
        self.assertEqual(body['error_code'], 'forbidden')
        self.assertNotIn('operation_title', body)

    @override_settings(**MCP_SETTINGS)
    @patch('apps.authapi.mcp_identity.requests.post')
    def test_forward_call_log_tool_destructive_keeps_destructive_when_entry_access_kind_write(self, mock_post):
        """Allowed or session_used destructive rows use ``access_kind`` destructive (Identity #135)."""
        mock_post.return_value = MagicMock(status_code=201, raise_for_status=MagicMock())
        forward_call_log(
            {
                'company_id': 42,
                'agent_id': str(uuid.uuid4()),
                'grant_version': 0,
                'service': 'email',
                'tool': 'api_v1_rules_destroy',
                'method': 'DELETE',
                'resource_path': '/api/v1/rules/{rule_id}',
                'access_kind': 'write',
                'tool_destructive': True,
                'outcome': 'session_used',
                'duration_ms': 1,
                'http_status': 200,
            }
        )
        body = mock_post.call_args.kwargs['json']
        self.assertEqual(body['access_kind'], 'destructive')
        self.assertNotIn('resource_path', body)
        self.assertNotIn('http_status', body)
        self.assertNotIn('company_name', body)
        self.assertNotIn('arguments_hash', body)
        self.assertEqual(body.get('grant_version'), 0)

    @override_settings(**MCP_SETTINGS)
    @patch('apps.authapi.mcp_identity.requests.post')
    def test_forward_call_log_destructive_allowed_with_approval_id(self, mock_post):
        appr = str(uuid.uuid4())
        mock_post.return_value = MagicMock(status_code=201, raise_for_status=MagicMock())
        forward_call_log(
            {
                'company_id': 42,
                'agent_id': str(uuid.uuid4()),
                'grant_version': 0,
                'service': 'email',
                'tool': 'api_v1_rules_destroy',
                'access_kind': 'destructive',
                'tool_destructive': True,
                'outcome': 'allowed',
                'duration_ms': 5,
                'http_status': 204,
                'approval_request_id': appr,
            }
        )
        body = mock_post.call_args.kwargs['json']
        self.assertEqual(body['access_kind'], 'destructive')
        self.assertEqual(body['outcome'], 'allowed')
        self.assertEqual(body['approval_id'], appr)
        self.assertNotIn('error_code', body)

    @override_settings(**MCP_SETTINGS)
    @patch('apps.authapi.mcp_identity.requests.post')
    def test_forward_call_log_destructive_failed_maps_write_and_error_code(self, mock_post):
        """Identity #135: destructive + non-allowed outcome uses ``write``; local codes map to allowlist."""
        appr = str(uuid.uuid4())
        mock_post.return_value = MagicMock(status_code=201, raise_for_status=MagicMock())
        forward_call_log(
            {
                'company_id': 42,
                'agent_id': str(uuid.uuid4()),
                'grant_version': 0,
                'service': 'email',
                'tool': 'api_v1_rules_destroy',
                'access_kind': 'destructive',
                'tool_destructive': True,
                'outcome': 'failed',
                'duration_ms': 2,
                'http_status': 400,
                'error_code': 'validation_failed',
                'approval_request_id': appr,
            }
        )
        body = mock_post.call_args.kwargs['json']
        self.assertEqual(body['access_kind'], 'write')
        self.assertEqual(body['outcome'], 'failed')
        self.assertEqual(body['error_code'], 'bad_request')
        self.assertEqual(body['approval_id'], appr)

    @override_settings(**MCP_SETTINGS)
    @patch('apps.authapi.mcp_identity.requests.post')
    def test_forward_call_log_pending_approval_omits_error_code(self, mock_post):
        mock_post.return_value = MagicMock(status_code=201, raise_for_status=MagicMock())
        forward_call_log(
            {
                'company_id': 42,
                'agent_id': str(uuid.uuid4()),
                'grant_version': 0,
                'service': 'email',
                'tool': 'api_v1_rules_create',
                'access_kind': 'write',
                'tool_destructive': False,
                'outcome': 'waiting_approval',
                'duration_ms': 1,
                'http_status': 200,
                'error_code': 'approval_pending',
                'approval_request_id': str(uuid.uuid4()),
            }
        )
        body = mock_post.call_args.kwargs['json']
        self.assertEqual(body['outcome'], 'waiting_approval')
        self.assertNotIn('error_code', body)

    @override_settings(**MCP_SETTINGS)
    @patch('apps.authapi.mcp_identity.requests.post')
    def test_forward_call_log_maps_approval_expired_to_identity_expired(self, mock_post):
        mock_post.return_value = MagicMock(status_code=201, raise_for_status=MagicMock())
        forward_call_log(
            {
                'company_id': 42,
                'agent_id': str(uuid.uuid4()),
                'grant_version': 0,
                'service': 'email',
                'tool': 'api_v1_rules_create',
                'access_kind': 'write',
                'tool_destructive': False,
                'outcome': 'denied',
                'duration_ms': 1,
                'http_status': 200,
                'error_code': 'approval_expired',
            }
        )
        body = mock_post.call_args.kwargs['json']
        self.assertEqual(body['error_code'], 'expired')

    @override_settings(**MCP_SETTINGS)
    @patch('apps.authapi.mcp_identity.requests.post')
    def test_forward_call_log_normalizes_pending_approval_outcome(self, mock_post):
        mock_post.return_value = MagicMock(status_code=201, raise_for_status=MagicMock())
        forward_call_log(
            {
                'company_id': 42,
                'agent_id': str(uuid.uuid4()),
                'grant_version': 0,
                'service': 'email',
                'tool': 'api_v1_rules_list',
                'access_kind': 'read',
                'outcome': 'pending_approval',
                'duration_ms': 1,
                'http_status': 200,
            }
        )
        body = mock_post.call_args.kwargs['json']
        self.assertEqual(body['outcome'], 'waiting_approval')
