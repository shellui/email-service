"""Contract fixes requested by the hosting-service integration."""

from __future__ import annotations

from pathlib import Path

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.email.catalog import get_definition
from apps.email.crypto import encrypt_json
from apps.email.keys import issue_service_key
from apps.email.models import CompanyProvider, EventSkip, Message
from apps.providers.registry import fake_provider

ROOT = Path(__file__).resolve().parents[3]


@override_settings(
    EMAIL_DELIVER_SYNC=True,
    EMAIL_FALLBACK_PROVIDER='fake',
    EMAIL_ALLOW_FAKE_PROVIDER=True,
    DEBUG=True,
    EMAIL_PLATFORM_COMPANY_IDS=[],
)
class HostingContractTests(TestCase):
    def setUp(self):
        fake_provider().sent.clear()
        fake_provider().fail_code = ''
        fake_provider().raise_on_send = None
        self.client = APIClient()
        raw, _client = issue_service_key(
            service='identity',
            allowed_lanes=['auth', 'transactional'],
            allowed_template_prefixes=['identity.'],
        )
        self.identity_key = raw
        raw, _client = issue_service_key(
            service='hosting',
            allowed_lanes=['transactional'],
            allowed_template_prefixes=['hosting.'],
        )
        self.hosting_key = raw

    def _auth(self, key):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {key}')

    def _provider(self, company_id, from_name=''):
        CompanyProvider.objects.create(
            company_id=company_id,
            provider='fake',
            from_email='ops@acme.com',
            from_name=from_name,
            credentials_ciphertext=encrypt_json({'marker': 'company'}, setting='EMAIL_CREDENTIALS_KEY'),
            credentials_hint='••••test',
            configured=True,
        )

    def _failed(self, company_id, **extra):
        body = {
            'company_id': company_id,
            'service': 'hosting',
            'event_type': 'hosting.deployment.failed',
            'payload': {'display_name': 'Docs', 'error': 'artifact_extract_failed'},
            'recipients': [{'email': 'ops@acme.com'}],
        }
        body.update(extra)
        return body

    def test_catalog_and_docs_use_error(self):
        definition = get_definition('hosting.deployment.failed')
        tokens = {item['token'] for item in definition['variables']}
        self.assertIn('error', tokens)
        self.assertNotIn('error_summary', tokens)
        text = (ROOT / 'docs' / 'integration.md').read_text(encoding='utf-8')
        self.assertNotIn('error_summary', text)
        self.assertIn('"error": "artifact_extract_failed"', text)

    def test_auth_still_requires_company_name_until_one_is_stored(self):
        self._auth(self.identity_key)
        missing = self.client.post(
            '/api/v1/send',
            {
                'company_id': 99,
                'template_key': 'identity.auth.magic_link.requested',
                'to': [{'email': 'ada@acme.com'}],
                'variables': {
                    'magic_link_url': 'https://id.shellui.com/api/v1/magic-link/verify?token=example',
                },
            },
            format='json',
        )
        self.assertEqual(missing.status_code, 400, missing.content)
        self.assertEqual(missing.json()['error_code'], 'validation_failed')
        self.assertEqual(missing.json()['field_errors']['variables.company_name'], ['required'])

    def test_company_name_is_remembered_and_filled(self):
        self._provider(21)
        self._auth(self.identity_key)
        first = self.client.post(
            '/api/v1/send',
            {
                'company_id': 21,
                'template_key': 'identity.auth.magic_link.requested',
                'to': [{'email': 'ada@acme.com'}],
                'variables': {
                    'company_name': 'Acme',
                    'magic_link_url': 'https://id.shellui.com/api/v1/magic-link/verify?token=example',
                },
            },
            format='json',
        )
        self.assertEqual(first.status_code, 202, first.content)
        omitted = self.client.post(
            '/api/v1/send',
            {
                'company_id': 21,
                'template_key': 'identity.auth.magic_link.requested',
                'to': [{'email': 'bea@acme.com'}],
                'variables': {
                    'magic_link_url': 'https://id.shellui.com/api/v1/magic-link/verify?token=example',
                },
            },
            format='json',
        )
        self.assertEqual(omitted.status_code, 202, omitted.content)
        self._auth(self.hosting_key)
        event = self.client.post('/api/v1/events', self._failed(21), format='json')
        self.assertEqual(event.status_code, 202, event.content)
        html = fake_provider().sent[-1].html
        self.assertIn('Acme', html)
        self.assertIn('artifact_extract_failed', html)
        self.assertNotIn('your company', html)

    def test_unknown_company_uses_the_template_language_default(self):
        self._provider(22, from_name='Shellui')
        self._auth(self.hosting_key)
        english = self.client.post('/api/v1/events', self._failed(22, language='en'), format='json')
        self.assertEqual(english.status_code, 202, english.content)
        self.assertIn('your company', fake_provider().sent[-1].html)
        french = self.client.post('/api/v1/events', self._failed(22, language='fr'), format='json')
        self.assertEqual(french.status_code, 202, french.content)
        self.assertIn('votre entreprise', fake_provider().sent[-1].html)

    def test_provider_from_name_fills_company_name(self):
        self._provider(23, from_name='Northwind')
        self._auth(self.hosting_key)
        response = self.client.post('/api/v1/events', self._failed(23), format='json')
        self.assertEqual(response.status_code, 202, response.content)
        self.assertIn('Northwind', fake_provider().sent[-1].html)

    def test_empty_recipients_are_skipped_and_counted(self):
        self._auth(self.hosting_key)
        body = self._failed(40, recipients=[], idempotency_key='deploy-empty')
        first = self.client.post('/api/v1/events', body, format='json')
        self.assertEqual(first.status_code, 202, first.content)
        self.assertEqual(first.json()['skipped_reason'], 'no_recipients')
        self.assertEqual(first.json()['messages'], [])
        self.assertTrue(first.json()['rule_enabled'])
        self.assertEqual(Message.objects.filter(company_id=40).count(), 0)
        replay = self.client.post('/api/v1/events', body, format='json')
        self.assertEqual(replay.status_code, 202, replay.content)
        self.assertTrue(replay.json()['idempotent_replay'])
        self.assertEqual(EventSkip.objects.filter(company_id=40, reason='no_recipients').count(), 1)

        from apps.authapi.principal import EmailPrincipal

        owner = APIClient()
        owner.force_authenticate(
            user=EmailPrincipal(user_id=1, company_id=40, email='owner@acme.com', is_company_owner=True)
        )
        stats = owner.get('/api/v1/stats?company_id=40')
        self.assertEqual(stats.status_code, 200, stats.content)
        self.assertEqual(stats.json()['skipped']['no_recipients'], 1)
        self.assertEqual(stats.json()['skipped']['total'], 1)
        self.assertEqual(stats.json()['totals']['sent'], 0)
