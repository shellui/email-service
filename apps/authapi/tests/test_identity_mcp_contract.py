"""Parity checks against public Identity ``identity-service`` internal MCP contracts (raw source).

Delegated authority success body:
https://raw.githubusercontent.com/shellui/identity-service/d79d433278ac18695a6165448bf0b8cfe56c88a5/apps/agents/internal_views.py
(``McpInternalDelegatedAgentAuthorityView`` HTTP 200 keys only.)

Call log ingest body shape exercised via ``forward_call_log``:
https://raw.githubusercontent.com/shellui/identity-service/d79d433278ac18695a6165448bf0b8cfe56c88a5/apps/agents/internal_views.py
(``McpInternalCallLogView``); path must canonicalize to ``POST /mcp`` for email (``call_log_internal.fixed_resource_label_for_service``).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from apps.authapi.mcp_identity import _DELEGATED_AUTHORITY_SUCCESS_KEYS, forward_call_log
from apps.authapi.tests.test_mcp_endpoint import MCP_SETTINGS

# Copied key set from Identity ``McpInternalDelegatedAgentAuthorityView`` success ``Response`` dict.
_IDENTITY_DELEGATED_AGENT_AUTHORITY_200_KEYS = frozenset(
    {
        'authorized',
        'revoked',
        'paused',
        'agent_id',
        'company_id',
        'global_agent_id',
        'global_company_id',
        'grant_version',
        'company_binding_version',
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
        self.assertNotIn('resource_path', body)
        self.assertNotIn('http_status', body)
        self.assertNotIn('company_name', body)
        self.assertNotIn('arguments_hash', body)
