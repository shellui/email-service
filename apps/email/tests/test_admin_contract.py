"""Contract gaps reported by the admin UI."""

from __future__ import annotations

from pathlib import Path

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.authapi.principal import EmailPrincipal
from apps.email.crypto import encrypt_json
from apps.email.keys import issue_service_key
from apps.email.models import CompanyProvider
from apps.providers.registry import fake_provider

ROOT = Path(__file__).resolve().parents[3]

PALETTE = {
    'background': '#ffffff',
    'foreground': '#111111',
    'muted': '#eeeeee',
    'mutedForeground': '#666666',
    'primary': '#112233',
    'primaryForeground': '#ffffff',
    'border': '#cccccc',
}


def _owner(company_id, *, email='owner@acme.com', staff=False):
    client = APIClient()
    client.force_authenticate(
        user=EmailPrincipal(
            user_id=3,
            company_id=company_id,
            email=email,
            is_company_owner=True,
            is_staff=staff,
        )
    )
    return client


@override_settings(
    EMAIL_DELIVER_SYNC=True,
    EMAIL_FALLBACK_PROVIDER='fake',
    EMAIL_ALLOW_FAKE_PROVIDER=True,
    EMAIL_ALLOW_COMPANY_SMTP=False,
    DEBUG=True,
)
class AdminContractTests(TestCase):
    def setUp(self):
        fake_provider().sent.clear()
        fake_provider().fail_code = ''
        raw, _client = issue_service_key(
            service='hosting',
            allowed_lanes=['transactional'],
            allowed_template_prefixes=['hosting.'],
        )
        self.hosting_key = raw
        CompanyProvider.objects.create(
            company_id=40,
            provider='fake',
            from_email='ops@acme.com',
            from_name='Acme',
            credentials_ciphertext=encrypt_json({'marker': 'company'}, setting='EMAIL_CREDENTIALS_KEY'),
            credentials_hint='••••test',
            configured=True,
        )

    def test_unconfigured_provider_shape_and_smtp_flag(self):
        owner = _owner(41)
        body = owner.get('/api/v1/provider?company_id=41').json()
        self.assertFalse(body['configured'])
        self.assertIsNone(body['provider'])
        self.assertEqual(body['credentials_hint'], '')
        self.assertEqual(body['from_name'], '')
        self.assertEqual(body['sending_domain'], '')
        self.assertFalse(body['webhook_configured'])
        self.assertEqual(body['webhook_hint'], '')
        self.assertFalse(body['smtp_allowed'])
        self.assertIn('id.shellui.com', body['auth_link_hosts'])
        self.assertIn('from_email', body)
        self.assertIn('bulk_from_email', body)

    @override_settings(EMAIL_ALLOW_COMPANY_SMTP=True)
    def test_smtp_allowed_follows_the_setting(self):
        owner = _owner(41)
        body = owner.get('/api/v1/provider?company_id=41').json()
        self.assertTrue(body['smtp_allowed'])

    def test_omitted_provider_fields_are_kept(self):
        owner = _owner(41)
        saved = owner.put(
            '/api/v1/provider?company_id=41',
            {
                'provider': 'resend',
                'from_email': 'hello@acme.com',
                'from_name': 'Acme',
                'sending_domain': 'acme.com',
                'bulk_from_email': 'news@acme.com',
                'credentials': {'api_key': 're_company_secret_value'},  # gitleaks:allow
            },
            format='json',
        )
        self.assertEqual(saved.status_code, 200, saved.content)
        kept = owner.put(
            '/api/v1/provider?company_id=41',
            {
                'provider': 'resend',
                'from_email': 'hello@acme.com',
            },
            format='json',
        )
        self.assertEqual(kept.status_code, 200, kept.content)
        self.assertEqual(kept.json()['bulk_from_email'], 'news@acme.com')
        self.assertEqual(kept.json()['from_name'], 'Acme')
        self.assertEqual(kept.json()['sending_domain'], 'acme.com')
        cleared = owner.put(
            '/api/v1/provider?company_id=41',
            {
                'provider': 'resend',
                'from_email': 'hello@acme.com',
                'from_name': 'Acme',
                'sending_domain': 'acme.com',
                'bulk_from_email': '',
            },
            format='json',
        )
        self.assertEqual(cleared.status_code, 200, cleared.content)
        self.assertEqual(cleared.json()['bulk_from_email'], '')

    def test_versions_return_the_document_and_palette(self):
        owner = _owner(40)
        created = owner.post(
            '/api/v1/templates?company_id=40',
            {'template_key': 'hosting.deployment.failed', 'language': 'en'},
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.content)
        template_id = created.json()['id']
        draft = owner.post(
            f'/api/v1/templates/{template_id}/versions',
            {
                'subject': 'Edited subject',
                'preheader': 'Edited preheader',
                'document': {'preview': 'Edited', 'blocks': [{'type': 'heading', 'text': 'Edited heading'}]},
                'theme_name': 'matte',
                'theme_palette': PALETTE,
            },
            format='json',
        )
        self.assertEqual(draft.status_code, 201, draft.content)
        number = draft.json()['number']
        listed = owner.get(f'/api/v1/templates/{template_id}/versions').json()['versions']
        row = next(item for item in listed if item['number'] == number)
        self.assertEqual(row['preheader'], 'Edited preheader')
        self.assertEqual(row['document']['blocks'][0]['text'], 'Edited heading')
        self.assertEqual(row['theme_name'], 'matte')
        self.assertEqual(row['theme_palette']['primary'], '#112233')
        one = owner.get(f'/api/v1/templates/{template_id}/versions/{number}')
        self.assertEqual(one.status_code, 200, one.content)
        self.assertEqual(one.json()['subject'], 'Edited subject')
        rejected = owner.post(
            f'/api/v1/templates/{template_id}/versions',
            {
                'subject': 'Bad color',
                'preheader': '',
                'document': {'preview': '', 'blocks': []},
                'theme_palette': {**PALETTE, 'primary': 'gold'},
            },
            format='json',
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.json()['field_errors']['theme_palette'], ['invalid_color'])

    def test_sent_mail_uses_the_stored_palette(self):
        owner = _owner(40)
        created = owner.post(
            '/api/v1/templates?company_id=40',
            {'template_key': 'hosting.deployment.failed', 'language': 'en'},
            format='json',
        )
        template_id = created.json()['id']
        draft = owner.post(
            f'/api/v1/templates/{template_id}/versions',
            {
                'subject': 'Edited subject',
                'preheader': 'Edited preheader',
                'document': {
                    'preview': 'Edited',
                    'blocks': [
                        {'type': 'heading', 'text': 'Edited heading'},
                        {'type': 'button', 'text': 'Open', 'href': 'https://example.com'},
                    ],
                },
                'theme_name': 'matte',
                'theme_palette': PALETTE,
            },
            format='json',
        )
        published = owner.post(f'/api/v1/templates/{template_id}/versions/{draft.json()["number"]}/publish')
        self.assertEqual(published.status_code, 200, published.content)
        rule = owner.post(
            '/api/v1/rules?company_id=40',
            {
                'event_type': 'hosting.deployment.failed',
                'content': {'mode': 'existing', 'template_id': template_id},
            },
            format='json',
        )
        self.assertEqual(rule.status_code, 201, rule.content)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.hosting_key}')
        sent = client.post(
            '/api/v1/events',
            {
                'company_id': 40,
                'service': 'hosting',
                'event_type': 'hosting.deployment.failed',
                'payload': {'display_name': 'Docs', 'error': 'artifact_extract_failed'},
                'recipients': [{'email': 'ops@acme.com'}],
            },
            format='json',
        )
        self.assertEqual(sent.status_code, 202, sent.content)
        html = fake_provider().sent[-1].html
        self.assertIn('#112233', html)
        self.assertIn('Edited heading', html)
        preview = owner.post(
            '/api/v1/render',
            {
                'document': {'preview': '', 'blocks': [{'type': 'button', 'text': 'Open', 'href': 'https://example.com'}]},
                'subject': 'Preview',
                'theme_palette': PALETTE,
            },
            format='json',
        )
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertIn('#112233', preview.json()['html'])

    def test_send_test_renders_the_draft_and_stays_on_the_owner_email(self):
        owner = _owner(40)
        created = owner.post(
            '/api/v1/templates?company_id=40',
            {'template_key': 'hosting.deployment.failed', 'language': 'en'},
            format='json',
        )
        template_id = created.json()['id']
        suggested = owner.post(f'/api/v1/templates/{template_id}/send-test', {}, format='json')
        self.assertEqual(suggested.status_code, 200, suggested.content)
        self.assertEqual(fake_provider().sent[-1].to_email, 'owner@acme.com')
        self.assertIn('My App', fake_provider().sent[-1].subject)
        owner.post(
            f'/api/v1/templates/{template_id}/versions',
            {
                'subject': 'Edited subject {{ display_name }}',
                'preheader': '',
                'document': {'preview': '', 'blocks': [{'type': 'heading', 'text': 'Edited heading'}]},
                'theme_palette': PALETTE,
            },
            format='json',
        )
        latest = owner.post(f'/api/v1/templates/{template_id}/send-test', {}, format='json')
        self.assertEqual(latest.status_code, 200, latest.content)
        self.assertIn('Edited heading', fake_provider().sent[-1].html)
        self.assertIn('#111111', fake_provider().sent[-1].html)
        self.assertIn('My App', fake_provider().sent[-1].subject)
        unsaved = owner.post(
            f'/api/v1/templates/{template_id}/send-test',
            {
                'subject': 'Unsaved {{ display_name }}',
                'document': {'preview': '', 'blocks': [{'type': 'heading', 'text': 'Unsaved heading'}]},
                'theme_palette': PALETTE,
            },
            format='json',
        )
        self.assertEqual(unsaved.status_code, 200, unsaved.content)
        self.assertIn('Unsaved heading', fake_provider().sent[-1].html)
        denied = owner.post(
            f'/api/v1/templates/{template_id}/send-test',
            {'to': 'other@acme.com', 'subject': 'Nope', 'document': {'preview': '', 'blocks': []}},
            format='json',
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()['error_code'], 'forbidden')

    def test_catalog_exposes_auth_link_hosts_and_rules_shape(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.hosting_key}')
        catalog = client.get('/api/v1/catalog')
        self.assertEqual(catalog.status_code, 200)
        self.assertIn('id.shellui.com', catalog.json()['auth_link_hosts'])
        owner = _owner(40)
        rules = owner.get('/api/v1/rules?company_id=40')
        self.assertEqual(rules.status_code, 200, rules.content)
        body = rules.json()
        self.assertEqual(body['company_id'], 40)
        row = next(item for item in body['rules'] if item['event_type'] == 'identity.auth.magic_link.requested')
        self.assertEqual(
            set(row),
            {
                'id',
                'service',
                'event_type',
                'enabled',
                'recipient_mode',
                'static_recipients',
                'language',
                'template_id',
                'built_in',
                'created_at',
                'updated_at',
            },
        )
        self.assertTrue(row['built_in'])
        text = (ROOT / 'docs' / 'integration.md').read_text(encoding='utf-8')
        self.assertIn('"error": "artifact_extract_failed"', text)
        self.assertNotIn('error_summary', text)
        self.assertIn('smtp_allowed', text)
        self.assertIn('theme_palette', text)
        self.assertIn('auth_link_hosts', text)
