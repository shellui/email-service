"""The Shellui fallback provider: auth mail only, Shellui wording, fixed sender, capped, with a kill switch."""

from __future__ import annotations

from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.authapi.principal import EmailPrincipal
from apps.email.checks import fallback_provider_configured
from apps.email.crypto import encrypt_json
from apps.email.keys import issue_service_key
from apps.email.metrics import metrics_http_body
from apps.email.models import CompanyProfile, CompanyProvider, FallbackUsage
from apps.email.service import _platform_credentials
from apps.providers.registry import fake_provider

MAGIC = 'identity.auth.magic_link.requested'
LINK = 'https://id.shellui.com/api/v1/magic-link/verify?token=secret-token-value'
COMPANY_COPY = {
    'subject': 'COMPANY COPY',
    'preheader': '',
    'html': '<html><body><p>COMPANY BODY {{ magic_link_url }}</p></body></html>',
    'text': 'COMPANY BODY {{ magic_link_url }}',
    'version': None,
    'language': 'en',
}


@override_settings(
    EMAIL_DELIVER_SYNC=True,
    EMAIL_FALLBACK_PROVIDER='fake',
    EMAIL_ALLOW_FAKE_PROVIDER=True,
    EMAIL_AUTH_LINK_HOSTS=['id.shellui.com'],
    EMAIL_PLATFORM_COMPANY_IDS=[],
    EMAIL_RECIPIENT_AUTH_LIMIT=100,
    EMAIL_COMPANY_AUTH_LIMIT=100,
    EMAIL_FALLBACK_COMPANY_DAILY_LIMIT=50,
    EMAIL_FALLBACK_GLOBAL_DAILY_LIMIT=500,
    EMAIL_FALLBACK_RECIPIENT_HOURLY_LIMIT=5,
    DEFAULT_FROM_EMAIL='no-reply@shellui.com',
    DEBUG=True,
)
class FallbackProviderTests(TestCase):
    def setUp(self):
        cache.clear()
        fake_provider().sent.clear()
        self.key, _client = issue_service_key(
            service='identity', allowed_lanes=['auth', 'transactional'], allowed_template_prefixes=['identity.']
        )

    def _send(self, email='ada@example.com', company_id=40, template_key=MAGIC, variables=None):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.key}')
        return client.post(
            '/api/v1/send',
            {
                'company_id': company_id,
                'template_key': template_key,
                'language': 'en',
                'to': [{'email': email}],
                'variables': variables or {'company_name': 'Acme <b>', 'magic_link_url': LINK},
            },
            format='json',
        )

    def test_sends_shellui_wording_from_fixed_sender_with_project_footer(self):
        with mock.patch('apps.email.rules.content_for_event', return_value=dict(COMPANY_COPY)):
            response = self._send()
        self.assertEqual(response.status_code, 202, response.content)
        sent = fake_provider().sent[-1]
        self.assertEqual(sent.from_email, 'no-reply@shellui.com')
        self.assertNotIn('COMPANY', sent.subject + sent.html + sent.text)
        self.assertNotIn('reply-to', {key.lower() for key in sent.headers})
        self.assertIn('Sent by Shellui on behalf of Acme &lt;b&gt;.', sent.html)
        self.assertIn('Sent by Shellui on behalf of Acme <b>.', sent.text)
        self.assertEqual(FallbackUsage.objects.get(company_id=40).sent, 1)
        self.assertEqual(FallbackUsage.objects.get(company_id=0).sent, 1)

    def test_company_provider_keeps_company_copy_and_is_not_counted(self):
        CompanyProvider.objects.create(
            company_id=40,
            provider='fake',
            from_email='ops@acme.com',
            credentials_ciphertext=encrypt_json({'marker': 'company'}, setting='EMAIL_CREDENTIALS_KEY'),
            configured=True,
        )
        with mock.patch('apps.email.rules.content_for_event', return_value=dict(COMPANY_COPY)):
            self.assertEqual(self._send().status_code, 202)
        sent = fake_provider().sent[-1]
        self.assertEqual(sent.subject, 'COMPANY COPY')
        self.assertNotIn('Sent by Shellui', sent.text)
        self.assertFalse(FallbackUsage.objects.exists())

    def test_non_auth_mail_is_refused(self):
        response = self._send(template_key='identity.group.created', variables={'company_name': 'Acme'})
        self.assertIn(response.status_code, {403, 409})
        self.assertEqual(fake_provider().sent, [])

    def test_single_recipient_only(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.key}')
        response = client.post(
            '/api/v1/send',
            {
                'company_id': 40,
                'template_key': MAGIC,
                'to': [{'email': 'a@example.com'}, {'email': 'b@example.com'}],
                'variables': {'company_name': 'Acme', 'magic_link_url': LINK},
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error_code'], 'auth_single_recipient')

    @override_settings(EMAIL_FALLBACK_RECIPIENT_HOURLY_LIMIT=2)
    def test_recipient_hourly_limit(self):
        self.assertEqual(self._send().status_code, 202)
        self.assertEqual(self._send().status_code, 202)
        with self.assertLogs('apps.email.fallback', 'WARNING') as logs:
            response = self._send()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()['error_code'], 'fallback_recipient_limit')
        self.assertNotIn('secret-token-value', ''.join(logs.output))
        self.assertNotIn('ada@example.com', ''.join(logs.output))
        self.assertEqual(self._send(email='bob@example.com').status_code, 202)

    @override_settings(EMAIL_FALLBACK_COMPANY_DAILY_LIMIT=2)
    def test_company_daily_limit(self):
        for index in range(2):
            self.assertEqual(self._send(email=f'u{index}@example.com').status_code, 202)
        response = self._send(email='u9@example.com')
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()['error_code'], 'fallback_company_limit')
        self.assertEqual(FallbackUsage.objects.get(company_id=40).refused, 1)
        self.assertEqual(self._send(email='u9@example.com', company_id=41).status_code, 202)

    @override_settings(EMAIL_FALLBACK_GLOBAL_DAILY_LIMIT=2)
    def test_global_daily_limit(self):
        self.assertEqual(self._send(company_id=40).status_code, 202)
        self.assertEqual(self._send(company_id=41).status_code, 202)
        response = self._send(company_id=42)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()['error_code'], 'fallback_global_limit')
        body = metrics_http_body().decode()
        self.assertIn('shellui_email_fallback_sent_today 2.0', body)
        self.assertIn('shellui_email_fallback_refused_today 1.0', body)

    @override_settings(EMAIL_PLATFORM_COMPANY_IDS=[40], EMAIL_FALLBACK_GLOBAL_DAILY_LIMIT=1)
    def test_platform_companies_are_not_capped(self):
        self.assertEqual(self._send(email='a@example.com').status_code, 202)
        self.assertEqual(self._send(email='b@example.com').status_code, 202)
        self.assertFalse(FallbackUsage.objects.exists())

    def test_kill_switch_per_company(self):
        CompanyProfile.objects.create(company_id=40, name='Acme', fallback_disabled=True)
        response = self._send()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error_code'], 'fallback_disabled')
        self.assertEqual(fake_provider().sent, [])

    @override_settings(EMAIL_FALLBACK_PROVIDER='none')
    def test_global_kill_switch(self):
        self._send()
        self.assertEqual(fake_provider().sent, [])

    def test_company_api_reports_usage(self):
        self._send()
        owner = APIClient()
        owner.force_authenticate(
            user=EmailPrincipal(user_id=3, company_id=40, email='owner@acme.com', is_company_owner=True)
        )
        response = owner.get('/api/v1/provider?company_id=40')
        self.assertEqual(response.status_code, 200, response.content)
        usage = response.json()['fallback_usage']
        self.assertEqual((usage['used'], usage['limit'], usage['applies']), (1, 50, True))


class FallbackSettingsTests(TestCase):
    @override_settings(
        SES_ACCESS_KEY_ID='AKIAEXAMPLEEXAMPLE', SES_SECRET_ACCESS_KEY='s3cret', SES_REGION='eu-central-2'
    )
    def test_ses_credentials_from_env(self):
        creds = _platform_credentials('ses')
        self.assertEqual(creds['region'], 'eu-central-2')
        self.assertEqual(creds['access_key_id'], 'AKIAEXAMPLEEXAMPLE')

    @override_settings(DEBUG=False, EMAIL_FALLBACK_PROVIDER='ses', SES_ACCESS_KEY_ID='', SES_SECRET_ACCESS_KEY='s3cret')
    def test_missing_credentials_warn_without_values(self):
        warnings = fallback_provider_configured(None)
        self.assertEqual([item.id for item in warnings], ['email.W001'])
        self.assertNotIn('s3cret', warnings[0].msg + warnings[0].hint)

    @override_settings(EMAIL_FALLBACK_PROVIDER='none')
    def test_disabled_fallback_has_no_warning(self):
        self.assertEqual(fallback_provider_configured(None), [])
