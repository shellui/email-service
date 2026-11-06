"""MCP agents must not read or mutate another company's resources."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from unittest.mock import patch

import jwt
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from apps.authapi.mcp_identity import reset_revocation_cache_for_tests
from apps.email.crypto import email_hmac, encrypt_json
from apps.email.models import (
    Broadcast,
    EmailRule,
    EmailTemplate,
    LibraryTemplate,
    Message,
    NewsletterList,
    Suppression,
    TemplateVersion,
)

from apps.authapi.tests.test_mcp_endpoint import MCP_SETTINGS, _agent_token, _revocation_ok_response, _rpc

COMPANY_A = 10
COMPANY_B = 20
LEAK = 'TENANT_ISOLATION_LEAK_B_20'


@override_settings(**MCP_SETTINGS)
class McpTenantIsolationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.msg_b = Message.objects.create(
            company_id=COMPANY_B,
            service='hosting',
            template_key='hosting.deployment.failed',
            language='en',
            lane='transactional',
            status=Message.STATUS_QUEUED,
            to_email=f'victim-b-leak@{LEAK}.example',
            email_hmac=email_hmac(f'victim-b-leak@{LEAK}.example'),
            variables_ciphertext=encrypt_json({'note': LEAK}, setting='EMAIL_VARIABLES_KEY'),
            accepted_at=timezone.now(),
        )
        cls.template_b = EmailTemplate.objects.create(
            company_id=COMPANY_B,
            template_key=f'company.{COMPANY_B}.{LEAK}',
            name=LEAK,
            event_type='email.newsletter.requested',
            kind=EmailTemplate.KIND_EVENT,
            language='en',
            set='barebone',
            active_version=1,
        )
        TemplateVersion.objects.create(
            template=cls.template_b,
            number=1,
            state=TemplateVersion.STATE_PUBLISHED,
            subject=LEAK,
            html=f'<p>{LEAK}</p>',
            text=LEAK,
            document={'type': 'doc', 'content': []},
        )
        cls.rule_b = EmailRule.objects.create(
            company_id=COMPANY_B,
            service='identity',
            event_type='identity.auth.magic_link.requested',
            enabled=True,
            language='en',
            template=cls.template_b,
            recipient_mode='static',
            static_recipients=[f'owner-b@{LEAK}.example'],
        )
        broadcast_template = EmailTemplate.objects.create(
            company_id=COMPANY_B,
            template_key=f'company.{COMPANY_B}.broadcast.{LEAK}',
            name=LEAK,
            kind=EmailTemplate.KIND_BROADCAST,
            language='en',
            set='barebone',
        )
        cls.broadcast_b = Broadcast.objects.create(
            company_id=COMPANY_B,
            name=LEAK,
            template=broadcast_template,
            audience={'mode': 'static', 'emails': [f'audience-b@{LEAK}.example']},
        )
        cls.suppression_b = Suppression.objects.create(
            company_id=COMPANY_B,
            email_hmac=email_hmac(f'suppressed-b@{LEAK}.example'),
            email_masked=f's***@{LEAK}.example',
            reason='manual',
        )
        confirm_template = EmailTemplate.objects.create(
            company_id=COMPANY_B,
            template_key=f'company.{COMPANY_B}.newsletter.{LEAK}',
            name=LEAK,
            kind=EmailTemplate.KIND_NEWSLETTER_CONFIRMATION,
            language='en',
            set='barebone',
        )
        cls.newsletter_b = NewsletterList.objects.create(
            company_id=COMPANY_B,
            name=LEAK,
            public_key='pktenantb20isolationtestkey01',
            confirmation_template=confirm_template,
        )
        cls.library_b = LibraryTemplate.objects.create(
            key=f'company.{COMPANY_B}.library.{LEAK}',
            set='barebone',
            name=LEAK,
            company_id=COMPANY_B,
            built_in=False,
            subject=LEAK,
            html=f'<p>{LEAK}</p>',
            text=LEAK,
            document={'type': 'doc', 'content': []},
        )

    def setUp(self):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        reset_mcp_server_cache()
        reset_revocation_cache_for_tests()
        self.client = Client()
        self.token, self.tenant_agent_id = _agent_token(company_id=COMPANY_A)
        self.revocation_patcher = patch('apps.authapi.mcp_identity.requests.get')
        self.mock_revocation_get = self.revocation_patcher.start()
        self.mock_revocation_get.return_value = _revocation_ok_response(
            agents=[{'agent_id': self.tenant_agent_id, 'company_id': COMPANY_A}],
        )

    def tearDown(self):
        self.revocation_patcher.stop()

    def _deny(self, response, *, req_id: int):
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        if 'error' in payload:
            return
        result = payload.get('result') or {}
        self.assertTrue(result.get('isError'), payload)
        text = str(result.get('content', [{}])[0].get('text', ''))
        self.assertNotIn(LEAK, text)
        if text.startswith('{'):
            body = json.loads(text)
            allowed = {
                'forbidden',
                'company_mismatch',
                'not_found',
                'message_not_found',
                'template_not_found',
                'library_not_found',
                'broadcast_not_found',
                'newsletter_not_found',
                'subscriber_not_found',
                'rule_not_found',
            }
            self.assertIn(body.get('error_code'), allowed, text)

    def _call(self, name: str, arguments: dict, req_id: int):
        return _rpc(self.client, self.token, 'tools/call', {'name': name, 'arguments': arguments}, req_id=req_id)

    def test_message_from_company_b_denied(self):
        msg_id = self.msg_b.public_id
        self._deny(
            self._call(
                'api_v1_messages_retrieve',
                {
                    'path': '/api/v1/messages/{message_id}',
                    'path_params': {'message_id': msg_id},
                    'method': 'GET',
                },
                req_id=1,
            ),
            req_id=1,
        )

    def test_template_from_company_b_denied(self):
        self._deny(
            self._call(
                'api_v1_templates_retrieve',
                {
                    'path': '/api/v1/templates/{template_id}',
                    'path_params': {'template_id': str(self.template_b.pk)},
                    'method': 'GET',
                },
                req_id=2,
            ),
            req_id=2,
        )

    def test_rule_from_company_b_denied(self):
        self._deny(
            self._call(
                'api_v1_rules_retrieve',
                {
                    'path': '/api/v1/rules/{rule_id}',
                    'path_params': {'rule_id': str(self.rule_b.pk)},
                    'method': 'GET',
                },
                req_id=3,
            ),
            req_id=3,
        )

    def test_broadcast_from_company_b_denied(self):
        self._deny(
            self._call(
                'api_v1_broadcasts_retrieve',
                {
                    'path': '/api/v1/broadcasts/{broadcast_id}',
                    'path_params': {'broadcast_id': str(self.broadcast_b.pk)},
                    'method': 'GET',
                },
                req_id=4,
            ),
            req_id=4,
        )

    def test_newsletter_from_company_b_denied(self):
        self._deny(
            self._call(
                'api_v1_newsletters_retrieve',
                {
                    'path': '/api/v1/newsletters/{newsletter_id}',
                    'path_params': {'newsletter_id': str(self.newsletter_b.pk)},
                    'method': 'GET',
                },
                req_id=5,
            ),
            req_id=5,
        )

    def test_suppression_list_with_company_b_query_denied(self):
        self._deny(
            self._call(
                'api_v1_suppressions_list',
                {
                    'path': '/api/v1/suppressions',
                    'method': 'GET',
                    'query': {'company_id': COMPANY_B},
                },
                req_id=6,
            ),
            req_id=6,
        )

    def test_library_from_company_b_denied(self):
        self._deny(
            self._call(
                'api_v1_library_retrieve',
                {
                    'path': '/api/v1/library/{library_id}',
                    'path_params': {'library_id': str(self.library_b.pk)},
                    'method': 'GET',
                },
                req_id=7,
            ),
            req_id=7,
        )

    def test_company_b_query_on_rules_list_denied(self):
        self._deny(
            self._call(
                'api_v1_rules_list',
                {
                    'path': '/api/v1/rules',
                    'method': 'GET',
                    'query': {'company_id': COMPANY_B},
                },
                req_id=8,
            ),
            req_id=8,
        )

    def test_revocation_company_binding_mismatch_forbidden(self):
        from apps.authapi.mcp_views import reset_mcp_server_cache

        agent_id = str(uuid.uuid4())
        self.mock_revocation_get.return_value = _revocation_ok_response(
            agents=[{'agent_id': agent_id, 'company_id': COMPANY_B}],
        )
        reset_mcp_server_cache()
        reset_revocation_cache_for_tests()
        token = jwt.encode(
            {
                'sub': f'agent:{agent_id}',
                'agent_id': agent_id,
                'company_id': COMPANY_A,
                'scope': 'email',
                'mcp_access': 'read_only',
                'mcp_read': 'safe',
                'mcp_destructive': False,
                'exp': int(timezone.now().timestamp()) + 600,
                'iss': MCP_SETTINGS['IDENTITY_ISSUER'],
                'aud': 'https://email.test/mcp',
            },
            MCP_SETTINGS['JWT_HS256_FALLBACK_SECRET'],
            algorithm='HS256',
        )
        response = _rpc(self.client, token, 'tools/list')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'forbidden')
