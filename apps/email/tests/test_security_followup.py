"""Follow-up checks from the 2d8ca95 security re-review."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.authapi.principal import EmailPrincipal
from apps.email.crypto import decrypt_json, email_hmac, encrypt_json
from apps.email.keys import issue_service_key
from apps.email.models import CompanyProfile, CompanyProvider, Message, Unsubscribe
from apps.email.service import _system_variables
from apps.email.tests.helpers import library_content, paragraphs
from apps.providers.base import ProviderMessage
from apps.providers.registry import fake_provider
from apps.providers.smtp import SmtpProvider

LITERAL_LINK_DOC = paragraphs('Sign in at https://evil.example/phish', ('Sign in', '{{ magic_link_url }}'))


def _owner(company_id):
    client = APIClient()
    client.force_authenticate(
        user=EmailPrincipal(
            user_id=3,
            company_id=company_id,
            email='owner@acme.com',
            is_company_owner=True,
        )
    )
    return client


@override_settings(
    EMAIL_DELIVER_SYNC=True,
    EMAIL_FALLBACK_PROVIDER='fake',
    EMAIL_ALLOW_FAKE_PROVIDER=True,
    DEBUG=True,
)
class SecurityFollowupTests(TestCase):
    def setUp(self):
        fake_provider().sent.clear()
        fake_provider().fail_code = ''
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
        self.api = APIClient()
        CompanyProvider.objects.create(
            company_id=42,
            provider='fake',
            from_email='ops@acme.com',
            from_name='Shellui',
            bulk_from_email='ops@acme.com',
            credentials_ciphertext=encrypt_json({'marker': 'company'}, setting='EMAIL_CREDENTIALS_KEY'),
            credentials_hint='••••test',
            configured=True,
        )

    def _auth(self, key):
        self.api.credentials(HTTP_AUTHORIZATION=f'Bearer {key}')

    def test_unscoped_hosting_key_does_not_store_company_name(self):
        from apps.email.rules import create_rule

        create_rule(42, {'event_type': 'hosting.deployment.failed', 'content': library_content()})
        self._auth(self.hosting_key)
        poisoned = self.api.post(
            '/api/v1/events',
            {
                'company_id': 42,
                'service': 'hosting',
                'event_type': 'hosting.deployment.failed',
                'payload': {'company_name': 'Poisoned', 'display_name': 'Docs', 'error': 'artifact_extract_failed'},
                'recipients': [{'email': 'ops@acme.com'}],
            },
            format='json',
        )
        self.assertEqual(poisoned.status_code, 202, poisoned.content)
        self.assertIn('Poisoned', fake_provider().sent[-1].html)
        self.assertFalse(CompanyProfile.objects.filter(company_id=42).exists())
        self._auth(self.identity_key)
        later = self.api.post(
            '/api/v1/send',
            {
                'company_id': 42,
                'template_key': 'identity.auth.magic_link.requested',
                'to': [{'email': 'ada@acme.com'}],
                'variables': {
                    'magic_link_url': 'https://id.shellui.com/api/v1/magic-link/verify?token=example',
                },
            },
            format='json',
        )
        self.assertEqual(later.status_code, 400, later.content)
        self.assertEqual(later.json()['field_errors']['variables.company_name'], ['required'])

    def test_identity_and_a_single_company_key_store_the_name(self):
        self._auth(self.identity_key)
        stored = self.api.post(
            '/api/v1/send',
            {
                'company_id': 42,
                'template_key': 'identity.auth.magic_link.requested',
                'to': [{'email': 'ada@acme.com'}],
                'variables': {
                    'company_name': 'Acme',
                    'magic_link_url': 'https://id.shellui.com/api/v1/magic-link/verify?token=example',
                },
            },
            format='json',
        )
        self.assertEqual(stored.status_code, 202, stored.content)
        self.assertEqual(CompanyProfile.objects.get(company_id=42).name, 'Acme')
        raw, _client = issue_service_key(
            service='hosting',
            allowed_lanes=['transactional'],
            allowed_template_prefixes=['hosting.'],
            allowed_company_ids=[42],
        )
        self._auth(raw)
        from apps.email.rules import create_rule

        create_rule(42, {'event_type': 'hosting.deployment.failed', 'content': library_content()})
        scoped = self.api.post(
            '/api/v1/events',
            {
                'company_id': 42,
                'service': 'hosting',
                'event_type': 'hosting.deployment.failed',
                'payload': {'company_name': 'Scoped', 'display_name': 'Docs', 'error': 'boom'},
                'recipients': [{'email': 'ops@acme.com'}],
            },
            format='json',
        )
        self.assertEqual(scoped.status_code, 202, scoped.content)
        self.assertEqual(CompanyProfile.objects.get(company_id=42).name, 'Scoped')

    def test_trusted_platform_flag_does_not_skip_the_pin(self):
        message = ProviderMessage(
            message_id='msg_test',
            to_email='ada@acme.com',
            from_email='hello@acme.com',
            from_name='Acme',
            subject='Hello',
            html='<p>Hi</p>',
            text='Hi',
            idempotency_key='msg_test',
        )
        with patch('apps.providers.smtp._open_client') as open_client:
            refused = SmtpProvider().send(
                message,
                {'host': '127.0.0.1', 'port': 2525, 'password': 'pw', 'trusted_platform': True},
            )
        self.assertEqual(refused.error_code, 'company_smtp_disabled')
        open_client.assert_not_called()
        with override_settings(EMAIL_ALLOW_COMPANY_SMTP=True):
            with patch('apps.providers.smtp._open_client') as open_client:
                private = SmtpProvider().send(
                    message,
                    {'host': '10.1.1.1', 'port': 587, 'password': 'pw', 'trusted_platform': True},
                )
        self.assertEqual(private.error_code, 'provider_host_not_public')
        open_client.assert_not_called()

        owner = _owner(4)
        with override_settings(EMAIL_ALLOW_COMPANY_SMTP=True):
            saved = owner.put(
                '/api/v1/provider?company_id=4',
                {
                    'provider': 'smtp',
                    'from_email': 'hello@acme.com',
                    'credentials': {
                        'host': '1.1.1.1',
                        'port': 587,
                        'password': 'pw',
                        'trusted_platform': True,
                        '_shellui_platform_relay': True,
                    },
                },
                format='json',
            )
        self.assertEqual(saved.status_code, 200, saved.content)
        stored = decrypt_json(
            CompanyProvider.objects.get(company_id=4).credentials_ciphertext,
            setting='EMAIL_CREDENTIALS_KEY',
        )
        self.assertNotIn('trusted_platform', stored)
        self.assertNotIn('_shellui_platform_relay', stored)
        self.assertEqual(stored['host'], '1.1.1.1')

    @override_settings(EMAIL_HOST='smtp.shellui.test', EMAIL_PORT=2525, EMAIL_HOST_USER='mailer', EMAIL_HOST_PASSWORD='secret')
    def test_settings_relay_stays_trusted_when_company_smtp_is_off(self):
        message = ProviderMessage(
            message_id='msg_platform',
            to_email='ada@acme.com',
            from_email='no-reply@shellui.com',
            from_name='Shellui',
            subject='Hello',
            html='<p>Hi</p>',
            text='Hi',
            idempotency_key='msg_platform',
        )

        class Dummy:
            def ehlo(self):
                return None

            def starttls(self):
                return None

            def login(self, username, password):
                return None

            def quit(self):
                return None

            def close(self):
                return None

            def send_message(self, email):
                return None

        with patch('apps.providers.smtp._open_client', return_value=Dummy()) as open_client:
            sent = SmtpProvider().send(
                message,
                {
                    'host': 'smtp.shellui.test',
                    'port': 2525,
                    'username': 'mailer',
                    'password': 'secret',
                    'use_tls': True,
                    'use_ssl': False,
                },
            )
        self.assertTrue(sent.ok)
        open_client.assert_called_once()

    def test_rule_config_cannot_allow_a_private_webhook(self):
        from apps.actions.delivery import deliver_one
        from apps.actions.models import ActionOutbox, ActionRule

        rule = ActionRule.objects.create(
            company_id=4,
            name='internal',
            event_type='email.message.sent',
            config={'url': 'http://127.0.0.1/hook', 'allow_private_urls': True},
        )
        row = ActionOutbox.objects.create(
            company_id=4,
            action_rule=rule,
            event_type='email.message.sent',
            envelope={'id': 'evt', 'type': 'email.message.sent', 'data': {}},
            status=ActionOutbox.STATUS_PENDING,
            next_attempt_at=timezone.now(),
            webhook_id='msgwh_private',
            target_url='http://127.0.0.1/hook',
        )
        with patch('apps.actions.delivery.requests.Session') as session:
            deliver_one(row.pk)
        session.assert_not_called()
        row.refresh_from_db()
        self.assertEqual(row.status, ActionOutbox.STATUS_DEAD)
        self.assertEqual(row.last_error, 'ssrf_blocked')

    def test_non_auth_mail_uses_a_signed_unsubscribe_url(self):
        message = Message.objects.create(
            company_id=42,
            service='hosting',
            template_key='hosting.deployment.failed',
            language='en',
            lane='transactional',
            status=Message.STATUS_QUEUED,
            to_email='ada@acme.com',
            email_hmac=email_hmac('ada@acme.com'),
            variables_ciphertext=encrypt_json({}, setting='EMAIL_VARIABLES_KEY'),
            accepted_at=timezone.now(),
        )
        values = _system_variables(message)
        url = values['system.unsubscribe_url']
        self.assertNotIn('/u/preview', url)
        self.assertEqual(values['system.preferences_url'], url)
        token = url.rsplit('/', 1)[-1]
        recorded = self.client.post(f'/u/{token}')
        self.assertEqual(recorded.status_code, 200, recorded.content)
        self.assertEqual(Unsubscribe.objects.filter(company_id=42, category='transactional').count(), 1)
        auth = Message.objects.create(
            company_id=42,
            service='identity',
            template_key='identity.auth.magic_link.requested',
            language='en',
            lane='auth',
            status=Message.STATUS_QUEUED,
            to_email='ada@acme.com',
            email_hmac=email_hmac('ada@acme.com'),
            accepted_at=timezone.now(),
        )
        self.assertNotIn('system.unsubscribe_url', _system_variables(auth))

        bulk = Message.objects.create(
            company_id=42,
            service='hosting',
            template_key='hosting.deployment.failed',
            language='en',
            lane='bulk',
            status=Message.STATUS_QUEUED,
            to_email='ada@acme.com',
            email_hmac=email_hmac('ada@acme.com'),
            variables_ciphertext=encrypt_json({'display_name': 'Docs'}, setting='EMAIL_VARIABLES_KEY'),
            accepted_at=timezone.now(),
        )
        from apps.email.service import deliver_message

        deliver_message(bulk.pk)
        headers = fake_provider().sent[-1].headers
        self.assertTrue(headers['List-Unsubscribe'].startswith('<https://'))
        self.assertNotIn('/u/preview', headers['List-Unsubscribe'])
        self.assertEqual(headers['List-Unsubscribe-Post'], 'List-Unsubscribe=One-Click')

    def test_auth_prose_rejects_a_literal_url(self):
        owner = _owner(42)
        rules = owner.get('/api/v1/rules?company_id=42&service=identity').json()['rules']
        template_id = next(row for row in rules if row['event_type'] == 'identity.auth.magic_link.requested')['template_id']
        draft = owner.post(
            f'/api/v1/templates/{template_id}/versions',
            {'subject': 'Sign in', 'preheader': '', 'document': LITERAL_LINK_DOC},
            format='json',
        )
        published = owner.post(f'/api/v1/templates/{template_id}/versions/{draft.json()["number"]}/publish')
        self.assertEqual(published.status_code, 400, published.content)
        self.assertEqual(published.json()['error_code'], 'auth_literal_link')
        text = (Path(__file__).resolve().parents[3] / 'docs' / 'security.md').read_text(encoding='utf-8')
        self.assertIn('SMTP has no equivalent', text)
        self.assertIn('auth_literal_link', text)
