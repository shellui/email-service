"""Regression tests for the 0.5.0 security review."""

from __future__ import annotations

import logging
import ssl
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.email.broadcasts import apply_broadcast_event
from apps.email.checks import client_ip_header_configured
from apps.email.crypto import email_hmac
from apps.email.models import Broadcast, BroadcastRecipient, EmailTemplate, Message, Suppression
from apps.email.newsletters import _cell, client_ip
from apps.email.service import apply_provider_event
from apps.providers.base import ProviderMessage
from apps.providers.smtp import SmtpProvider, tls_context
from config.gunicorn_logging import RedactingLogger
from config.log_redaction import RedactTokenPathsFilter, redact_path
from config.sentry_scrub import before_send


def _broadcast(company_id: int, lookup: str) -> Broadcast:
    template = EmailTemplate.objects.create(
        template_key=f'company.bc{company_id}{lookup[:4]}', company_id=company_id, language='en', event_type='x'
    )
    broadcast = Broadcast.objects.create(company_id=company_id, name='News', template=template, resend_lookup=lookup)
    BroadcastRecipient.objects.create(broadcast=broadcast, email_hmac=email_hmac('ada@example.org'), language='en')
    return broadcast


class ProviderWebhookScopeTests(TestCase):
    def test_broadcast_events_need_an_exact_id_and_the_same_company(self):
        _broadcast(2, 'bc_victim_en bc_victim_fr')
        data = {'to': ['ada@example.org']}
        self.assertFalse(apply_broadcast_event('email.complained', {**data, 'broadcast_id': '_'}))
        self.assertFalse(apply_broadcast_event('email.complained', {**data, 'broadcast_id': 'bc_victim'}))
        self.assertFalse(apply_broadcast_event('email.complained', {**data, 'broadcast_id': 'bc_victim_fr'}, company_id=1))
        self.assertFalse(Suppression.objects.exists())
        self.assertTrue(apply_broadcast_event('email.complained', {**data, 'broadcast_id': 'bc_victim_fr'}, company_id=2))
        self.assertEqual(Suppression.objects.get().company_id, 2)

    def test_message_events_stay_in_the_webhook_company(self):
        now = timezone.now()
        Message.objects.create(
            company_id=2, service='identity', template_key='t', language='en', lane='transactional',
            status=Message.STATUS_SENT, to_email='ada@example.org', email_hmac='h',
            provider_message_id='re_1', accepted_at=now,
        )
        detail = {'stream': 'transactional', 'bounce_type': 'permanent'}
        self.assertFalse(
            apply_provider_event(provider_message_id='re_1', event_name='email.bounced', provider_event_id='e1', detail=detail, company_id=1)
        )
        self.assertEqual(Message.objects.get().status, Message.STATUS_SENT)
        self.assertTrue(
            apply_provider_event(provider_message_id='re_1', event_name='email.bounced', provider_event_id='e2', detail=detail, company_id=2)
        )


class AccessLogTests(SimpleTestCase):
    def test_tokens_and_referer_are_not_logged(self):
        self.assertEqual(redact_path('GET /n/confirm/abc.DEF-123 HTTP/1.1'), 'GET /n/confirm/[redacted] HTTP/1.1')
        self.assertEqual(redact_path('POST /u/1.news.h.sig?x=1 HTTP/1.1'), 'POST /u/[redacted]?x=1 HTTP/1.1')
        self.assertEqual(redact_path('GET /api/v1/health HTTP/1.1'), 'GET /api/v1/health HTTP/1.1')
        logger = RedactingLogger(SimpleNamespace(**{
            'errorlog': '-', 'loglevel': 'info', 'accesslog': None, 'syslog': False, 'logconfig': None,
            'logconfig_dict': {}, 'logconfig_json': None, 'capture_output': False, 'disable_redirect_access_to_syslog': False,
            'statsd_host': None, 'dogstatsd_tags': '', 'statsd_prefix': '', 'user': 0, 'group': 0, 'umask': 0,
            'access_log_format': '', 'syslog_addr': None, 'syslog_facility': 'user', 'syslog_prefix': None,
        }))
        environ = {
            'REQUEST_METHOD': 'GET', 'RAW_URI': '/n/confirm/secret-token', 'PATH_INFO': '/n/confirm/secret-token',
            'SERVER_PROTOCOL': 'HTTP/1.1', 'HTTP_REFERER': 'https://mail.example/n/confirm/secret-token',
        }
        resp = SimpleNamespace(status='200 OK', sent=10, headers=[])
        req = SimpleNamespace(headers=[('REFERER', 'https://x/secret-token'), ('AUTHORIZATION', 'Bearer secret-token')])
        atoms = logger.atoms(resp, req, environ, SimpleNamespace(seconds=0, microseconds=5))
        self.assertNotIn('secret-token', repr(atoms))
        self.assertEqual(atoms['f'], '-')


    def test_django_request_lines_and_sentry_urls_drop_tokens(self):
        record = logging.LogRecord('django.request', logging.WARNING, '', 0, 'Not Found: %s', ('/u/1.a.b.sig',), None)
        RedactTokenPathsFilter().filter(record)
        self.assertEqual(record.getMessage(), 'Not Found: /u/[redacted]')
        event = before_send({'request': {'url': 'https://mail.example/n/confirm/tok?x=1'}})
        self.assertEqual(event['request']['url'], 'https://mail.example/n/confirm/[redacted]')


class SmtpTlsTests(SimpleTestCase):
    def test_company_relays_always_verify_certificates(self):
        with override_settings(EMAIL_SMTP_VERIFY_CERT=False):
            context = tls_context(trusted_platform=False)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertEqual(tls_context(trusted_platform=True).verify_mode, ssl.CERT_REQUIRED)
        with override_settings(EMAIL_SMTP_VERIFY_CERT=False), self.assertLogs('apps.providers.smtp', 'WARNING'):
            self.assertEqual(tls_context(trusted_platform=True).verify_mode, ssl.CERT_NONE)

    @override_settings(EMAIL_ALLOW_COMPANY_SMTP=True)
    def test_starttls_gets_the_verified_context_and_bad_certificates_do_not_retry(self):
        calls = {}

        class Client:
            def ehlo(self):
                return None

            def starttls(self, context=None):
                calls['context'] = context
                raise ssl.SSLCertVerificationError('bad cert')

            def quit(self):
                return None

            def close(self):
                return None

        message = ProviderMessage(
            message_id='m', to_email='ada@acme.com', from_email='hi@acme.com', from_name='', subject='s',
            html='', text='t', idempotency_key='m',
        )
        with patch('apps.providers.smtp._open_client', return_value=Client()):
            result = SmtpProvider().send(message, {'host': '1.1.1.1', 'port': 587})
        self.assertEqual(calls['context'].verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual((result.ok, result.retryable, result.error_code), (False, False, 'provider_tls_unverified'))


class ClientIpTests(SimpleTestCase):
    def _request(self, **headers):
        return RequestFactory().post('/', REMOTE_ADDR='10.0.0.2', **headers)

    def test_forwarded_for_is_read_from_the_right(self):
        request = self._request(HTTP_X_FORWARDED_FOR='6.6.6.6, 203.0.113.9')
        with override_settings(EMAIL_CLIENT_IP_HEADER='X-Forwarded-For', EMAIL_CLIENT_IP_TRUSTED_PROXIES=1):
            self.assertEqual(client_ip(request), '203.0.113.9')
        request = self._request(HTTP_X_FORWARDED_FOR='6.6.6.6, 203.0.113.9, 172.16.0.1')
        with override_settings(EMAIL_CLIENT_IP_HEADER='X-Forwarded-For', EMAIL_CLIENT_IP_TRUSTED_PROXIES=2):
            self.assertEqual(client_ip(request), '203.0.113.9')

    def test_single_value_headers_and_the_default_are_unchanged(self):
        with override_settings(EMAIL_CLIENT_IP_HEADER='CF-Connecting-IP'):
            self.assertEqual(client_ip(self._request(HTTP_CF_CONNECTING_IP='198.51.100.4')), '198.51.100.4')
        with override_settings(EMAIL_CLIENT_IP_HEADER=''):
            self.assertEqual(client_ip(self._request(HTTP_X_FORWARDED_FOR='6.6.6.6')), '10.0.0.2')

    def test_production_warns_when_the_header_is_empty(self):
        with override_settings(DEBUG=False, EMAIL_CLIENT_IP_HEADER=''):
            self.assertEqual([w.id for w in client_ip_header_configured(None)], ['email.W003'])
        with override_settings(DEBUG=False, EMAIL_CLIENT_IP_HEADER='X-Real-IP'):
            self.assertEqual(client_ip_header_configured(None), [])


class CsvGuardTests(SimpleTestCase):
    def test_formulas_behind_spaces_and_newlines_are_neutralised(self):
        self.assertEqual(_cell('  =HYPERLINK("x")'), '\'  =HYPERLINK("x")')
        self.assertEqual(_cell('\n=1+1'), "'\n=1+1")
        self.assertEqual(_cell('Ada'), 'Ada')
