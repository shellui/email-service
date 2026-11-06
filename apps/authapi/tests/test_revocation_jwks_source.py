"""Revocation lists are verified with the configured JWKS source."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from apps.authapi import mcp_identity


def _run_refresh():
    response = MagicMock(status_code=200)
    response.json.return_value = {}
    with (
        patch.object(mcp_identity, 'identity_api_base', return_value='http://identity:8000'),
        patch.object(mcp_identity, 'identity_origin', return_value='http://localhost:8001'),
        patch.object(mcp_identity, '_revocation_assertion_headers', return_value={}),
        patch.object(mcp_identity.requests, 'get', return_value=response),
        patch.object(
            mcp_identity, 'parse_trusted_revocation_poll_response', return_value=(frozenset(), 60, [])
        ) as parse,
    ):
        mcp_identity.reset_revocation_cache_for_tests()
        mcp_identity._refresh_revocations()
    mcp_identity.reset_revocation_cache_for_tests()
    return parse.call_args.kwargs


class RevocationJwksSourceTests(SimpleTestCase):
    @override_settings(IDENTITY_JWKS_DOCUMENT=None, IDENTITY_JWKS_URL='http://identity:8000/.well-known/jwks.json')
    def test_uses_identity_jwks_url_without_pinned_document(self):
        kwargs = _run_refresh()
        self.assertEqual(kwargs['expected_issuer'], 'http://localhost:8001')
        self.assertEqual(kwargs['identity_jwks_url'], 'http://identity:8000/.well-known/jwks.json')

    @override_settings(IDENTITY_JWKS_DOCUMENT={'keys': [{'kid': 'k'}]}, IDENTITY_JWKS_URL='http://identity:8000/x')
    def test_pinned_document_wins(self):
        kwargs = _run_refresh()
        self.assertEqual(kwargs['jwks'], {'keys': [{'kid': 'k'}]})
        self.assertIsNone(kwargs['identity_jwks_url'])
