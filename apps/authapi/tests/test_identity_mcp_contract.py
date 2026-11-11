"""Parity checks against public Identity ``identity-service`` internal MCP contracts (raw source).

Delegated authority response body (every HTTP 200 uses the same 15 keys):
https://raw.githubusercontent.com/shellui/identity-service/d7fe351/apps/agents/internal_views.py
(``_delegated_authority_fixed_payload`` / ``McpInternalDelegatedAgentAuthorityView``.)
"""

from __future__ import annotations

from django.test import SimpleTestCase

from apps.authapi.mcp_identity import _DELEGATED_AUTHORITY_SUCCESS_KEYS

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
