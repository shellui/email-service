"""Regression tests for the 37e55d2 security review."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.authapi.principal import EmailPrincipal
from apps.email.keys import issue_service_key
from apps.email.models import LaneState, Message
from apps.email.views import _unsubscribe_token
from apps.providers.base import ProviderMessage
from apps.providers.registry import fake_provider
from apps.providers.smtp import SMTP_TIMEOUT_SECONDS, SmtpProvider

ROOT = Path(__file__).resolve().parents[3]


def _svix_headers(secret: str, body: bytes) -> dict[str, str]:
    msg_id = 'msg_security_test'
    timestamp = str(int(time.time()))
    signed = f'{msg_id}.{timestamp}.'.encode('utf-8') + body
    digest = base64.b64encode(hmac.new(secret.encode('utf-8'), signed, hashlib.sha256).digest()).decode('ascii')
    return {
        'HTTP_SVIX_ID': msg_id,
        'HTTP_SVIX_TIMESTAMP': timestamp,
        'HTTP_SVIX_SIGNATURE': f'v1,{digest}',
    }


@override_settings(
    EMAIL_DELIVER_SYNC=True,
    EMAIL_FALLBACK_PROVIDER='fake',
    EMAIL_ALLOW_FAKE_PROVIDER=True,
    DEBUG=True,
    EMAIL_PLATFORM_COMPANY_IDS=[],
)
class SecurityRegressionTests(TestCase):
    def setUp(self):
        cache.clear()
        provider = fake_provider()
        provider.sent.clear()
        provider.fail_code = ''
        provider.retryable = False
        provider.raise_on_send = None
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

    def _service(self, key):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {key}')

    def _owner(self, company_id, *, email='owner@acme.com', staff=False):
        client = APIClient()
        client.force_authenticate(
            user=EmailPrincipal(
                user_id=company_id or 1,
                company_id=company_id,
                email=email,
                is_company_owner=True,
                is_staff=staff,
            )
        )
        return client

    def _magic(self, company_id, email):
        return {
            'company_id': company_id,
            'template_key': 'identity.auth.magic_link.requested',
            'to': [{'email': email}],
            'variables': {
                'company_name': 'Acme',
                'magic_link_url': 'https://id.shellui.com/api/v1/magic-link/verify?token=example',
            },
        }

    def test_provider_unauthorized_pauses_only_that_company(self):
        self._service(self.identity_key)
        fake_provider().fail_code = 'provider_unauthorized'
        failed = self.client.post('/api/v1/send', self._magic(1, 'ada@acme.com'), format='json')
        self.assertEqual(failed.status_code, 202, failed.content)
        self.assertEqual(failed.json()['messages'][0]['status'], 'failed')
        self.assertTrue(LaneState.objects.filter(lane='auth', company_id=1, paused=True).exists())
        self.assertFalse(LaneState.objects.filter(lane='auth', company_id__isnull=True, paused=True).exists())
        fake_provider().fail_code = ''
        blocked = self.client.post('/api/v1/send', self._magic(1, 'grace@acme.com'), format='json')
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()['error_code'], 'lane_paused')
        other = self.client.post('/api/v1/send', self._magic(2, 'grace@other.com'), format='json')
        self.assertEqual(other.status_code, 202, other.content)
        self.assertEqual(other.json()['messages'][0]['status'], 'sent')

    def test_company_smtp_disabled_and_private_host_rejected(self):
        owner = self._owner(4)
        disabled = owner.put(
            '/api/v1/provider?company_id=4',
            {
                'provider': 'smtp',
                'from_email': 'hello@acme.com',
                'credentials': {'host': '1.1.1.1', 'port': 587, 'password': 'pw'},
            },
            format='json',
        )
        self.assertEqual(disabled.status_code, 400, disabled.content)
        self.assertEqual(disabled.json()['error_code'], 'company_smtp_disabled')

        with override_settings(EMAIL_ALLOW_COMPANY_SMTP=True):
            private = owner.put(
                '/api/v1/provider?company_id=4',
                {
                    'provider': 'smtp',
                    'from_email': 'hello@acme.com',
                    'credentials': {'host': '127.0.0.1', 'port': 2525, 'password': 'pw'},
                },
                format='json',
            )
            self.assertEqual(private.status_code, 400, private.content)
            self.assertEqual(private.json()['error_code'], 'provider_host_not_public')
            public = owner.put(
                '/api/v1/provider?company_id=4',
                {
                    'provider': 'smtp',
                    'from_email': 'hello@acme.com',
                    'credentials': {'host': '1.1.1.1', 'port': 587, 'password': 'pw'},
                },
                format='json',
            )
            self.assertEqual(public.status_code, 200, public.content)

        message = ProviderMessage(
            message_id='msg_test',
            to_email='ada@acme.com',
            from_email='hello@acme.com',
            from_name='Acme\r\nBcc: evil@example.com',
            subject='Hello',
            html='<p>Hi</p>',
            text='Hi',
            idempotency_key='msg_test',
        )
        captured = {}

        class Dummy:
            def ehlo(self):
                return None

            def starttls(self):
                return None

            def quit(self):
                return None

            def close(self):
                return None

            def send_message(self, email):
                captured['from'] = str(email['From'])

        with override_settings(EMAIL_ALLOW_COMPANY_SMTP=True):
            with patch('apps.providers.smtp._open_client', return_value=Dummy()):
                refused = SmtpProvider().send(
                    message,
                    {'host': '10.1.1.1', 'port': 587},
                )
                self.assertEqual(refused.error_code, 'provider_host_not_public')
                sent = SmtpProvider().send(
                    message,
                    {'host': '1.1.1.1', 'port': 587},
                )
        self.assertTrue(sent.ok)
        self.assertNotIn('\n', captured['from'])
        self.assertNotIn('\r', captured['from'])

    def test_webhook_save_and_delivery_pin_redirects(self):
        owner = self._owner(4)
        blocked = owner.post(
            '/api/v1/actions/rules?company_id=4',
            {
                'name': 'internal',
                'event_type': 'email.message.sent',
                'url': 'http://127.0.0.1/hook',
            },
            format='json',
        )
        self.assertEqual(blocked.status_code, 400, blocked.content)
        self.assertEqual(blocked.json()['field_errors']['url'], ['not_public'])

        from apps.actions.delivery import deliver_one
        from apps.actions.models import ActionOutbox

        row = ActionOutbox.objects.create(
            company_id=4,
            event_type='email.message.sent',
            envelope={'id': 'evt', 'type': 'email.message.sent', 'data': {}},
            status=ActionOutbox.STATUS_PENDING,
            next_attempt_at=timezone.now(),
            webhook_id='msgwh_pin',
            target_url='http://1.1.1.1/hook',
        )
        calls = []

        class Session:
            def mount(self, *args, **kwargs):
                return None

            def post(self, url, **kwargs):
                calls.append((url, kwargs))

                class Response:
                    status_code = 302
                    headers = {'Location': 'http://127.0.0.1/secret'}

                return Response()

            def close(self):
                return None

        with patch('apps.actions.delivery.requests.Session', return_value=Session()):
            deliver_one(row.pk)
        row.refresh_from_db()
        self.assertEqual(calls[0][0], 'http://1.1.1.1:80/hook')
        self.assertFalse(calls[0][1]['allow_redirects'])
        self.assertEqual(row.status, ActionOutbox.STATUS_DEAD)
        self.assertEqual(row.last_error, 'redirect_blocked')

    def test_auth_override_keeps_link_and_platform_from_is_limited(self):
        owner = self._owner(4)
        created = owner.post(
            '/api/v1/templates?company_id=4',
            {'template_key': 'identity.auth.magic_link.requested', 'language': 'en'},
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.content)
        template_id = created.json()['id']
        evil = owner.post(
            f'/api/v1/templates/{template_id}/versions',
            {
                'subject': 'Sign in',
                'document': {
                    'preview': 'Sign in',
                    'blocks': [
                        {'type': 'text', 'text': 'Use {{ magic_link_url }}'},
                        {'type': 'button', 'text': 'Continue', 'href': 'https://evil.example/login'},
                    ],
                },
            },
            format='json',
        )
        self.assertEqual(evil.status_code, 201, evil.content)
        published = owner.post(f'/api/v1/templates/{template_id}/versions/{evil.json()["number"]}/publish')
        self.assertEqual(published.status_code, 400, published.content)
        self.assertEqual(published.json()['error_code'], 'auth_link_host_not_allowed')
        missing = owner.post(
            f'/api/v1/templates/{template_id}/versions',
            {
                'subject': 'Sign in',
                'document': {
                    'preview': 'Sign in',
                    'blocks': [{'type': 'text', 'text': 'Hello'}],
                },
            },
            format='json',
        )
        refused = owner.post(f'/api/v1/templates/{template_id}/versions/{missing.json()["number"]}/publish')
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.json()['error_code'], 'auth_link_missing')
        good = owner.post(
            f'/api/v1/templates/{template_id}/versions',
            {
                'subject': 'Sign in',
                'document': {
                    'preview': 'Sign in',
                    'blocks': [
                        {'type': 'button', 'text': 'Sign in', 'href': '{{ magic_link_url }}'},
                    ],
                },
            },
            format='json',
        )
        ok = owner.post(f'/api/v1/templates/{template_id}/versions/{good.json()["number"]}/publish')
        self.assertEqual(ok.status_code, 200, ok.content)

        impersonate = owner.put(
            '/api/v1/provider?company_id=4',
            {
                'provider': 'resend',
                'from_email': 'no-reply@shellui.com',
                'credentials': {'api_key': 're_company_secret_value'},  # gitleaks:allow
            },
            format='json',
        )
        self.assertEqual(impersonate.status_code, 403, impersonate.content)
        self.assertEqual(impersonate.json()['error_code'], 'platform_sender_not_allowed')

        from apps.email.rules import create_rule

        create_rule(8, {'event_type': 'hosting.deployment.failed', 'content': {'mode': 'suggested'}})
        self._service(self.hosting_key)
        denied = self.client.post(
            '/api/v1/events',
            {
                'company_id': 8,
                'service': 'hosting',
                'event_type': 'hosting.deployment.failed',
                'payload': {'display_name': 'Docs'},
                'recipients': [{'email': 'ops@acme.com'}],
            },
            format='json',
        )
        self.assertEqual(denied.status_code, 409, denied.content)
        self.assertEqual(denied.json()['error_code'], 'platform_sender_not_allowed')
        self._service(self.identity_key)
        before = len(fake_provider().sent)
        auth = self.client.post('/api/v1/send', self._magic(8, 'ada@acme.com'), format='json')
        self.assertEqual(auth.status_code, 202, auth.content)
        self.assertEqual(auth.json()['messages'][0]['status'], 'sent')
        self.assertEqual(fake_provider().sent[-1].from_email, 'no-reply@shellui.com')
        self.assertGreater(len(fake_provider().sent), before)

    def test_provider_exception_does_not_escape_and_lease_covers_timeout(self):
        from django.conf import settings

        self.assertGreaterEqual(settings.EMAIL_SEND_LEASE_SECONDS, 120)
        self.assertGreater(settings.EMAIL_SEND_LEASE_SECONDS, SMTP_TIMEOUT_SECONDS)
        seen = {}
        provider = fake_provider()
        original = provider.send

        def wrapped(message, credentials):
            row = Message.objects.filter(status=Message.STATUS_SENDING).order_by('-accepted_at').first()
            seen['lease'] = (row.locked_until - timezone.now()).total_seconds()
            return original(message, credentials)

        provider.send = wrapped
        self._service(self.identity_key)
        try:
            sent = self.client.post('/api/v1/send', self._magic(3, 'ada@acme.com'), format='json')
        finally:
            provider.send = original
        self.assertEqual(sent.status_code, 202, sent.content)
        self.assertGreater(seen['lease'], SMTP_TIMEOUT_SECONDS)

        provider.raise_on_send = RuntimeError('boom')
        crashed = self.client.post('/api/v1/send', self._magic(3, 'bea@acme.com'), format='json')
        provider.raise_on_send = None
        self.assertEqual(crashed.status_code, 202, crashed.content)
        self.assertEqual(crashed.json()['messages'][0]['status'], 'failed')
        row = Message.objects.get(to_email='bea@acme.com')
        self.assertEqual(row.last_error_code, 'provider_error')

    def test_sweep_does_not_resend_after_provider_accepts(self):
        from apps.email.service import sweep

        now = timezone.now()
        accepted = Message.objects.create(
            company_id=1,
            service='identity',
            template_key='identity.auth.magic_link.requested',
            language='en',
            lane='auth',
            status=Message.STATUS_SENDING,
            to_email='ada@acme.com',
            email_hmac='hmac',
            provider_message_id='already-accepted',
            accepted_at=now,
            locked_until=now - timedelta(seconds=1),
        )
        fresh = Message.objects.create(
            company_id=1,
            service='identity',
            template_key='identity.auth.magic_link.requested',
            language='en',
            lane='auth',
            status=Message.STATUS_SENDING,
            to_email='bea@acme.com',
            email_hmac='hmac2',
            provider_message_id='',
            accepted_at=now,
            locked_until=now - timedelta(seconds=1),
        )
        sweep()
        accepted.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual(accepted.status, Message.STATUS_SENT)
        self.assertEqual(fresh.status, Message.STATUS_RETRYING)

    def test_owner_without_company_id_cannot_cross_companies(self):
        owner = self._owner(None)
        response = owner.get('/api/v1/provider?company_id=4')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error_code'], 'forbidden')

    def test_unsubscribe_requires_a_valid_signature(self):
        from apps.actions.models import EventLog
        from apps.email.models import Unsubscribe

        token = _unsubscribe_token(42, 'ada@acme.com', 'news')
        ok = self.client.post(f'/u/{token}')
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(Unsubscribe.objects.filter(company_id=42).count(), 1)
        self.assertEqual(EventLog.objects.filter(event_type='email.unsubscribe.created').count(), 1)
        forged = self.client.post('/u/42.news.anything')
        self.assertEqual(forged.status_code, 404)
        self.assertEqual(Unsubscribe.objects.count(), 1)
        self.assertEqual(EventLog.objects.filter(event_type='email.unsubscribe.created').count(), 1)

    def test_company_webhook_does_not_accept_the_platform_secret(self):
        body = b'{"type":"email.delivered","data":{"email_id":"missing"}}'
        headers = _svix_headers('platform-secret', body)
        with override_settings(RESEND_WEBHOOK_SECRET='platform-secret'):
            company = self.client.post(
                '/api/v1/provider-webhooks/resend/transactional?company_id=5',
                data=body,
                content_type='application/json',
                **headers,
            )
            platform = self.client.post(
                '/api/v1/provider-webhooks/resend/transactional',
                data=body,
                content_type='application/json',
                **headers,
            )
        self.assertEqual(company.status_code, 401)
        self.assertEqual(company.json()['error_code'], 'unauthorized')
        self.assertEqual(platform.status_code, 200, platform.content)

    @override_settings(EMAIL_COMPANY_AUTH_LIMIT=2, EMAIL_COMPANY_AUTH_WINDOW_SECONDS=60)
    def test_auth_company_budget_does_not_block_another_company(self):
        self._service(self.identity_key)
        for index, address in enumerate(('one@acme.com', 'two@acme.com')):
            response = self.client.post('/api/v1/send', self._magic(11, address), format='json')
            self.assertEqual(response.status_code, 202, response.content)
        limited = self.client.post('/api/v1/send', self._magic(11, 'three@acme.com'), format='json')
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.json()['error_code'], 'company_rate_limited')
        other = self.client.post('/api/v1/send', self._magic(12, 'one@other.com'), format='json')
        self.assertEqual(other.status_code, 202, other.content)
        self.assertEqual(other.json()['messages'][0]['status'], 'sent')


class ImageUserTests(TestCase):
    def test_container_runs_as_appuser(self):
        dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
        entry = (ROOT / 'tools' / 'docker-entrypoint.sh').read_text(encoding='utf-8')
        self.assertIn('\nUSER appuser\n', dockerfile)
        self.assertNotIn('runuser', entry)
        self.assertIn('exec gunicorn', entry)
