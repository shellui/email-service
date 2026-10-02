"""Send API, rules, provider masking, stats, and catalog defaults."""

from __future__ import annotations

import json
import re
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.email.catalog import all_definitions
from apps.email.crypto import email_hmac
from apps.email.keys import issue_service_key
from apps.email.models import Message, Suppression
from apps.email.substitution import find_tokens
from apps.providers.registry import fake_provider

BANNED = re.compile(
    r'\b(easy|simple|quick|seamless|robust|powerful|just|very|really|simply)\b',
    re.IGNORECASE,
)


def _tokens(pack: dict) -> set[str]:
    blobs = [pack['subject'], pack['preheader'], json.dumps(pack['document'])]
    found = set()
    for blob in blobs:
        found |= find_tokens(blob)
    return {token for token in found if not token.startswith('system.')}


@override_settings(
    EMAIL_DELIVER_SYNC=True,
    EMAIL_FALLBACK_PROVIDER='fake',
    EMAIL_ALLOW_FAKE_PROVIDER=True,
    DEBUG=True,
)
class EmailApiTests(TestCase):
    def setUp(self):
        fake_provider().sent.clear()
        fake_provider().fail_code = ''
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

    def test_health_and_unauthorized_use_error_code(self):
        health = self.client.get('/api/v1/health')
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()['status'], 'ok')
        denied = self.client.post('/api/v1/send', {}, format='json')
        self.assertEqual(denied.status_code, 401)
        body = denied.json()
        self.assertEqual(body['error_code'], 'unauthorized')
        self.assertNotIn('detail', body)
        self.assertNotIn('error', body)

    def test_catalog_covers_sibling_webhook_events(self):
        self._auth(self.identity_key)
        response = self.client.get('/api/v1/catalog')
        self.assertEqual(response.status_code, 200)
        events = {row['event_type']: row for row in response.json()['events']}
        self.assertIn('identity.auth.magic_link.requested', events)
        self.assertTrue(events['identity.auth.magic_link.requested']['default_enabled'])
        self.assertEqual(events['identity.auth.magic_link.requested']['lane_class'], 'auth')
        self.assertIn('identity.user.invited', events)
        self.assertTrue(events['identity.user.invited']['default_enabled'])
        self.assertFalse(events['storage.object.uploaded']['default_enabled'])
        self.assertTrue(events['hosting.deployment.failed']['default_enabled'])
        self.assertFalse(events['hosting.deployment.succeeded']['default_enabled'])
        self.assertNotIn('identity.auth.login.succeeded', events)
        self.assertGreaterEqual(len(events), 23)
        from pathlib import Path

        root = Path(__file__).resolve().parents[3] / 'defaults'
        self.assertTrue((root / 'identity' / 'identity.auth.magic_link.requested' / 'definition.json').is_file())
        exported = {
            path.parent.name
            for path in root.glob('*/*/definition.json')
        }
        self.assertEqual(exported, {row['key'] for row in all_definitions()})

    def test_language_parity_and_writing(self):
        for definition in all_definitions():
            en = _tokens(definition['languages']['en'])
            fr = _tokens(definition['languages']['fr'])
            self.assertEqual(en, fr, definition['key'])
            declared = {item['token'] for item in definition['variables']}
            self.assertTrue(en <= declared, f'{definition["key"]} undeclared {en - declared}')
            for language, pack in definition['languages'].items():
                blob = pack['subject'] + pack['preheader'] + json.dumps(pack['document'])
                self.assertIsNone(BANNED.search(blob), f'{definition["key"]} {language}')
                self.assertNotIn('\u2014', blob)
                self.assertNotIn('\u2013', blob)

    def test_magic_link_send_and_idempotency(self):
        self._auth(self.identity_key)
        payload = {
            'company_id': 1,
            'template_key': 'identity.auth.magic_link.requested',
            'lane': 'auth',
            'language': 'fr',
            'to': [{'email': 'ada@acme.com', 'user_id': 42}],
            'variables': {
                'company_name': 'Acme',
                'magic_link_url': 'https://id.shellui.com/api/v1/magic-link/verify?token=example',
            },
            'ttl_seconds': 120,
            'idempotency_key': 'magic-link-1',
        }
        first = self.client.post('/api/v1/send', payload, format='json')
        self.assertEqual(first.status_code, 202, first.content)
        message = first.json()['messages'][0]
        self.assertEqual(message['status'], 'sent')
        self.assertEqual(message['lane'], 'auth')
        self.assertFalse(first.json()['idempotent_replay'])
        self.assertEqual(len(fake_provider().sent), 1)
        self.assertIn('id.shellui.com', fake_provider().sent[0].html)
        stored = Message.objects.get()
        self.assertEqual(stored.variables_ciphertext, '')
        replay = self.client.post('/api/v1/send', payload, format='json')
        self.assertEqual(replay.status_code, 202)
        self.assertTrue(replay.json()['idempotent_replay'])
        payload['variables']['company_name'] = 'Other'
        conflict = self.client.post('/api/v1/send', payload, format='json')
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()['error_code'], 'idempotency_conflict')

    def test_auth_suppression_is_synchronous(self):
        Suppression.objects.create(
            company_id=1,
            email_hmac=email_hmac('ada@acme.com'),
            email_masked='a***@acme.com',
            reason=Suppression.REASON_HARD_BOUNCE,
            expires_at=timezone.now() + timedelta(days=30),
        )
        self._auth(self.identity_key)
        response = self.client.post(
            '/api/v1/send',
            {
                'company_id': 1,
                'template_key': 'identity.auth.magic_link.requested',
                'to': [{'email': 'ada@acme.com'}],
                'variables': {
                    'company_name': 'Acme',
                    'magic_link_url': 'https://id.shellui.com/api/v1/magic-link/verify?token=example',
                },
            },
            format='json',
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['error_code'], 'recipient_suppressed')

    def test_event_rule_defaults(self):
        self._auth(self.hosting_key)
        skipped = self.client.post(
            '/api/v1/events',
            {
                'company_id': 7,
                'service': 'hosting',
                'event_type': 'hosting.app.created',
                'payload': {'display_name': 'Docs'},
                'recipients': [{'email': 'ops@acme.com'}],
            },
            format='json',
        )
        self.assertEqual(skipped.status_code, 202, skipped.content)
        self.assertFalse(skipped.json()['rule_enabled'])
        self.assertEqual(skipped.json()['skipped_reason'], 'rule_disabled')
        failed = self.client.post(
            '/api/v1/events',
            {
                'company_id': 7,
                'service': 'hosting',
                'event_type': 'hosting.deployment.failed',
                'language': 'en',
                'payload': {'display_name': 'Docs', 'error': 'artifact_extract_failed'},
                'recipients': [{'email': 'ops@acme.com'}],
                'idempotency_key': 'deploy-1',
            },
            format='json',
        )
        self.assertEqual(failed.status_code, 202, failed.content)
        self.assertTrue(failed.json()['rule_enabled'])
        self.assertEqual(failed.json()['messages'][0]['status'], 'sent')

    def test_expired_auth_message_is_not_sent(self):
        self._auth(self.identity_key)
        response = self.client.post(
            '/api/v1/send',
            {
                'company_id': 1,
                'template_key': 'identity.user.invited',
                'to': [{'email': 'ada@acme.com'}],
                'variables': {
                    'company_name': 'Acme',
                    'invitation_url': 'https://app.acme.com/',
                },
            },
            format='json',
        )
        self.assertEqual(response.status_code, 202, response.content)
        message = Message.objects.get()
        message.status = Message.STATUS_QUEUED
        message.sent_at = None
        message.expires_at = timezone.now() - timedelta(seconds=5)
        message.save(update_fields=['status', 'sent_at', 'expires_at'])
        from apps.email.service import deliver_message

        deliver_message(message.pk)
        message.refresh_from_db()
        self.assertEqual(message.status, Message.STATUS_EXPIRED)

    def test_provider_config_masks_secret_and_test_send(self):
        from apps.authapi.principal import EmailPrincipal
        from rest_framework.test import force_authenticate

        owner = EmailPrincipal(
            user_id=3,
            company_id=4,
            email='owner@acme.com',
            is_company_owner=True,
        )
        view_client = APIClient()
        view_client.force_authenticate(user=owner)
        saved = view_client.put(
            '/api/v1/provider?company_id=4',
            {
                'provider': 'resend',
                'from_email': 'hello@acme.com',
                'from_name': 'Acme',
                'sending_domain': 'acme.com',
                'credentials': {'api_key': 're_company_secret_value'},  # gitleaks:allow
            },
            format='json',
        )
        self.assertEqual(saved.status_code, 200, saved.content)
        body = saved.json()
        self.assertTrue(body['configured'])
        self.assertNotIn('re_company_secret_value', json.dumps(body))  # gitleaks:allow
        self.assertTrue(body['credentials_hint'].endswith('alue'))
        other = APIClient()
        other.force_authenticate(
            user=EmailPrincipal(user_id=9, company_id=9, email='x@acme.com', is_company_owner=True)
        )
        denied = other.get('/api/v1/provider?company_id=4')
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()['error_code'], 'company_mismatch')

    def test_stats_and_metrics(self):
        self._auth(self.hosting_key)
        self.client.post(
            '/api/v1/events',
            {
                'company_id': 7,
                'service': 'hosting',
                'event_type': 'hosting.deployment.failed',
                'payload': {'display_name': 'Docs'},
                'recipients': [{'email': 'ops@acme.com'}],
            },
            format='json',
        )
        from apps.authapi.principal import EmailPrincipal

        owner = APIClient()
        owner.force_authenticate(
            user=EmailPrincipal(user_id=1, company_id=7, email='owner@acme.com', is_company_owner=True, is_staff=True)
        )
        stats = owner.get('/api/v1/stats?company_id=7')
        self.assertEqual(stats.status_code, 200, stats.content)
        self.assertGreaterEqual(stats.json()['totals']['sent'], 1)
        metrics = owner.get('/api/v1/metrics?company_id=7')
        self.assertEqual(metrics.status_code, 200)
        self.assertIn(b'shellui_email_auth_ttl_expiries', metrics.content)
        self.assertIn(b'shellui_email_queue_depth', metrics.content)
