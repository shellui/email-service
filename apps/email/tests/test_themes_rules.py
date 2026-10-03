"""Themes, company settings, and list-style email rules."""

from __future__ import annotations

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from apps.authapi.principal import EmailPrincipal
from apps.email.crypto import encrypt_json
from apps.email.keys import issue_service_key
from apps.email.models import EmailRule, EmailTemplate, EventSkip, Message, TemplateVersion
from apps.email.rendering import render_document
from apps.providers.registry import fake_provider

PALETTE = {
    'background': '#ffffff',
    'foreground': '#111111',
    'muted': '#eeeeee',
    'mutedForeground': '#666666',
    'primary': '#112233',
    'primaryForeground': '#ffffff',
    'border': '#cccccc',
}


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
class ThemeAndRuleTests(TestCase):
    def setUp(self):
        fake_provider().sent.clear()
        self.owner = _owner(40)
        raw, _client = issue_service_key(
            service='hosting',
            allowed_lanes=['transactional'],
            allowed_template_prefixes=['hosting.'],
        )
        self.hosting_key = raw
        raw, _client = issue_service_key(
            service='identity',
            allowed_lanes=['auth', 'transactional'],
            allowed_template_prefixes=['identity.'],
        )
        self.identity_key = raw
        from apps.email.models import CompanyProvider

        CompanyProvider.objects.create(
            company_id=40,
            provider='fake',
            from_email='ops@acme.com',
            from_name='Acme',
            credentials_ciphertext=encrypt_json({'marker': 'company'}, setting='EMAIL_CREDENTIALS_KEY'),
            credentials_hint='••••test',
            configured=True,
        )

    def test_theme_list_and_preview(self):
        denied = APIClient().get('/api/v1/themes?company_id=40')
        self.assertEqual(denied.status_code, 401)
        listed = self.owner.get('/api/v1/themes?company_id=40')
        self.assertEqual(listed.status_code, 200, listed.content)
        keys = [item['key'] for item in listed.json()]
        self.assertEqual(keys, ['barebone', 'matte', 'protocol', 'arcane', 'studio'])
        self.assertEqual([item['name'] for item in listed.json()], ['Barebone', 'Matte', 'Protocol', 'Arcane', 'Studio'])
        preview = self.owner.get('/api/v1/themes/matte/preview?language=en&company_id=40')
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertIn('text/html', preview['Content-Type'])
        self.assertIn('#103B05', preview.content.decode())
        self.assertIn('Hello from Shellui', preview.content.decode())
        french = self.owner.get('/api/v1/themes/arcane/preview?language=fr&company_id=40')
        self.assertIn('#300610', french.content.decode())
        self.assertIn('Bonjour de Shellui', french.content.decode())
        unknown = self.owner.get(
            '/api/v1/themes/harbor/preview?company_id=40',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(unknown.json()['error_code'], 'theme_unknown')

    def test_renderer_uses_each_theme_palette(self):
        document = {
            'preview': 'Sample',
            'blocks': [
                {'type': 'heading', 'text': 'Hello'},
                {'type': 'text', 'text': 'One paragraph.'},
                {'type': 'button', 'text': 'Continue', 'href': 'https://shellui.com'},
            ],
        }
        expected = {
            'barebone': '#14171E',
            'matte': '#103B05',
            'protocol': '#131313',
            'arcane': '#300610',
            'studio': '#332C2C',
        }
        for key, color in expected.items():
            html, _text, _version = render_document(document, None, key)
            self.assertIn(color, html)
            self.assertIn(color.lower(), html.lower())
        overridden, _text, _version = render_document(document, PALETTE, 'protocol')
        self.assertIn('#112233', overridden)
        self.assertNotIn('#131313', overridden.lower())

    def test_settings_apply_to_existing_bumps_published_and_drafts(self):
        created = self.owner.post(
            '/api/v1/templates?company_id=40',
            {'template_key': 'hosting.app.created', 'language': 'en'},
            format='json',
        )
        template_id = created.json()['id']
        draft = self.owner.post(
            f'/api/v1/templates/{template_id}/versions',
            {
                'subject': 'Published subject',
                'preheader': 'Published preheader',
                'document': {'preview': 'Published', 'blocks': [{'type': 'heading', 'text': 'Stay'}]},
            },
            format='json',
        )
        published = self.owner.post(f'/api/v1/templates/{template_id}/versions/{draft.json()["number"]}/publish')
        self.assertEqual(published.status_code, 200, published.content)
        published_number = draft.json()['number']
        self.owner.post(
            f'/api/v1/templates/{template_id}/versions',
            {
                'subject': 'Draft subject',
                'preheader': '',
                'document': {'preview': '', 'blocks': [{'type': 'text', 'text': 'Draft copy'}]},
            },
            format='json',
        )
        current = self.owner.get('/api/v1/settings?company_id=40')
        self.assertEqual(current.json(), {'theme': 'barebone', 'templates_using_other_theme': 0})
        kept = self.owner.put(
            '/api/v1/settings?company_id=40',
            {'theme': 'matte', 'apply_to_existing': False},
            format='json',
        )
        self.assertEqual(kept.status_code, 200, kept.content)
        self.assertEqual(kept.json(), {'theme': 'matte', 'updated_templates': 0})
        warned = self.owner.get('/api/v1/settings?company_id=40')
        self.assertEqual(warned.json()['templates_using_other_theme'], 1)
        listed = self.owner.get('/api/v1/templates?company_id=40').json()['templates']
        row = next(item for item in listed if item['id'] == template_id)
        self.assertEqual(row['theme'], 'barebone')
        self.assertFalse(row['uses_company_theme'])
        applied = self.owner.put(
            '/api/v1/settings?company_id=40',
            {'theme': 'protocol', 'apply_to_existing': True},
            format='json',
        )
        self.assertEqual(applied.json(), {'theme': 'protocol', 'updated_templates': 1})
        template = EmailTemplate.objects.get(pk=template_id)
        self.assertEqual(template.active_version, published_number + 2)
        active = TemplateVersion.objects.get(template=template, number=template.active_version)
        self.assertEqual(active.state, 'published')
        self.assertEqual(active.theme_name, 'protocol')
        self.assertEqual(active.document['blocks'][0]['text'], 'Stay')
        previous = TemplateVersion.objects.get(template=template, number=published_number)
        self.assertEqual(previous.state, 'archived')
        newest = template.versions.order_by('-number').first()
        self.assertEqual(newest.state, 'draft')
        self.assertEqual(newest.theme_name, 'protocol')
        self.assertEqual(newest.document['blocks'][0]['text'], 'Draft copy')
        self.assertEqual(self.owner.get('/api/v1/settings?company_id=40').json()['templates_using_other_theme'], 0)
        unknown = self.owner.put(
            '/api/v1/settings?company_id=40',
            {'theme': 'harbor', 'apply_to_existing': False},
            format='json',
        )
        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(unknown.json()['error_code'], 'theme_unknown')
        detail = self.owner.get(f'/api/v1/templates/{template_id}')
        self.assertEqual(detail.json()['theme'], 'protocol')
        self.assertTrue(detail.json()['uses_company_theme'])

    def test_rule_crud_builtins_and_variable_compatibility(self):
        listed = self.owner.get('/api/v1/rules?company_id=40&service=identity')
        self.assertEqual(listed.status_code, 200, listed.content)
        events = {row['event_type']: row for row in listed.json()['rules']}
        self.assertEqual(set(events), {'identity.auth.magic_link.requested', 'identity.user.invited'})
        magic = events['identity.auth.magic_link.requested']
        self.assertTrue(magic['built_in'])
        self.assertTrue(magic['enabled'])
        removed = self.owner.delete(f'/api/v1/rules/{magic["id"]}?company_id=40')
        self.assertEqual(removed.status_code, 409)
        self.assertEqual(removed.json()['error_code'], 'rule_built_in')
        disabled = self.owner.patch(
            f'/api/v1/rules/{magic["id"]}?company_id=40',
            {'enabled': False},
            format='json',
        )
        self.assertEqual(disabled.status_code, 409)
        self.assertEqual(disabled.json()['error_code'], 'rule_built_in')
        again = self.owner.get('/api/v1/rules?company_id=40&service=identity')
        self.assertEqual(len(again.json()['rules']), 2)

        created = self.owner.post(
            '/api/v1/rules?company_id=40',
            {
                'service': 'hosting',
                'event_type': 'hosting.deployment.failed',
                'recipient_mode': 'static',
                'static_recipients': ['ops@acme.com'],
                'content': {'mode': 'suggested'},
            },
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.content)
        body = created.json()
        self.assertEqual(body['template_id'], EmailRule.objects.get(pk=body['id']).template_id)
        self.assertFalse(body['built_in'])
        template = EmailTemplate.objects.get(pk=body['template_id'])
        self.assertTrue(template.template_key.startswith('company.'))
        self.assertEqual(template.event_type, 'hosting.deployment.failed')
        self.assertEqual(template.name, 'Deployment failed')
        version = template.versions.get(number=template.active_version)
        self.assertEqual(version.state, 'published')
        self.assertEqual(version.theme_name, 'barebone')
        self.assertEqual([block['type'] for block in version.document['blocks']], ['heading', 'text'])

        mismatched = self.owner.post(
            '/api/v1/templates?company_id=40',
            {'template_key': 'hosting.deployment.succeeded', 'language': 'en'},
            format='json',
        )
        bad = self.owner.post(
            f'/api/v1/templates/{mismatched.json()["id"]}/versions',
            {
                'subject': 'Uses {{ slug }}',
                'document': {'preview': '', 'blocks': [{'type': 'text', 'text': 'Slug {{ slug }}'}]},
            },
            format='json',
        )
        self.owner.post(f'/api/v1/templates/{mismatched.json()["id"]}/versions/{bad.json()["number"]}/publish')
        refused = self.owner.post(
            '/api/v1/rules?company_id=40',
            {
                'event_type': 'hosting.deployment.failed',
                'content': {'mode': 'existing', 'template_id': mismatched.json()['id']},
            },
            format='json',
        )
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertEqual(refused.json()['error_code'], 'template_variables_mismatch')
        self.assertIn('slug', refused.json()['missing_variables'])
        compatible = self.owner.get('/api/v1/templates?company_id=40&event_type=hosting.deployment.failed')
        ids = {item['id'] for item in compatible.json()['templates']}
        self.assertIn(body['template_id'], ids)
        self.assertNotIn(mismatched.json()['id'], ids)
        missing = self.owner.post(
            '/api/v1/rules?company_id=40',
            {'event_type': 'not.an.event', 'content': {'mode': 'suggested'}},
            format='json',
        )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json()['error_code'], 'event_unknown')
        absent = self.owner.post(
            '/api/v1/rules?company_id=40',
            {'event_type': 'hosting.app.created', 'content': {'mode': 'existing', 'template_id': 99999}},
            format='json',
        )
        self.assertEqual(absent.status_code, 404)
        self.assertEqual(absent.json()['error_code'], 'template_not_found')
        switched = self.owner.patch(
            f'/api/v1/rules/{magic["id"]}?company_id=40',
            {'template_id': body['template_id']},
            format='json',
        )
        self.assertEqual(switched.status_code, 400)
        self.assertEqual(switched.json()['error_code'], 'template_variables_mismatch')
        deleted = self.owner.delete(f'/api/v1/rules/{body["id"]}?company_id=40')
        self.assertEqual(deleted.status_code, 204)

    def test_several_rules_send_once_per_rule_and_retry_is_idempotent(self):
        first = self.owner.post(
            '/api/v1/rules?company_id=40',
            {'event_type': 'hosting.deployment.failed', 'content': {'mode': 'suggested'}},
            format='json',
        )
        second = self.owner.post(
            '/api/v1/rules?company_id=40',
            {
                'event_type': 'hosting.deployment.failed',
                'recipient_mode': 'static',
                'static_recipients': ['ops@acme.com'],
                'content': {'mode': 'existing', 'template_id': first.json()['template_id']},
            },
            format='json',
        )
        self.assertEqual(second.status_code, 201, second.content)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.hosting_key}')
        payload = {
            'company_id': 40,
            'service': 'hosting',
            'event_type': 'hosting.deployment.failed',
            'payload': {'display_name': 'Docs', 'error': 'artifact_extract_failed'},
            'recipients': [{'email': 'ada@acme.com'}],
            'idempotency_key': 'deploy-rules',
        }
        sent = client.post('/api/v1/events', payload, format='json')
        self.assertEqual(sent.status_code, 202, sent.content)
        self.assertEqual(len(sent.json()['messages']), 2)
        self.assertEqual(Message.objects.filter(company_id=40, event_type='hosting.deployment.failed').count(), 2)
        addresses = sorted(message.to_email for message in Message.objects.filter(company_id=40))
        self.assertEqual(addresses, ['ada@acme.com', 'ops@acme.com'])
        replay = client.post('/api/v1/events', payload, format='json')
        self.assertTrue(replay.json()['idempotent_replay'])
        self.assertEqual(Message.objects.filter(company_id=40).count(), 2)
        self.assertEqual(len(fake_provider().sent), 2)

    def test_no_rule_skip_and_builtin_auth_event_sends(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.hosting_key}')
        skipped = client.post(
            '/api/v1/events',
            {
                'company_id': 40,
                'service': 'hosting',
                'event_type': 'hosting.app.created',
                'payload': {'display_name': 'Docs'},
                'recipients': [{'email': 'ada@acme.com'}],
            },
            format='json',
        )
        self.assertEqual(skipped.status_code, 202, skipped.content)
        self.assertEqual(skipped.json()['skipped_reason'], 'no_rule')
        self.assertTrue(EventSkip.objects.filter(company_id=40, reason='no_rule').exists())
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.identity_key}')
        magic = client.post(
            '/api/v1/events',
            {
                'company_id': 40,
                'service': 'identity',
                'event_type': 'identity.auth.magic_link.requested',
                'language': 'fr',
                'payload': {
                    'company_name': 'Acme',
                    'magic_link_url': 'https://id.shellui.com/api/v1/magic-link/verify?token=example',
                },
                'recipients': [{'email': 'ada@acme.com'}],
            },
            format='json',
        )
        self.assertEqual(magic.status_code, 202, magic.content)
        self.assertEqual(magic.json()['messages'][0]['lane'], 'auth')
        self.assertEqual(magic.json()['messages'][0]['status'], 'sent')
        self.assertIn('id.shellui.com', fake_provider().sent[-1].html)


class RuleMigrationTests(TransactionTestCase):
    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([('email', '0004_themes_and_rules')])
        super().tearDown()

    def test_enabled_rules_are_copied_and_shellui_theme_becomes_barebone(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('email', '0003_company_profile_event_skip')])
        apps = executor.loader.project_state([('email', '0003_company_profile_event_skip')]).apps
        OldTemplate = apps.get_model('email', 'EmailTemplate')
        OldVersion = apps.get_model('email', 'TemplateVersion')
        OldRule = apps.get_model('email', 'EmailRule')
        template = OldTemplate.objects.create(
            template_key='hosting.deployment.failed',
            company_id=5,
            language='en',
            active_version=1,
        )
        OldVersion.objects.create(
            template=template,
            number=1,
            state='published',
            subject='Old subject',
            preheader='',
            document={'preview': '', 'blocks': [{'type': 'heading', 'text': 'Kept'}]},
            theme_name='shellui',
            html='<p>old</p>',
            text='old',
        )
        OldRule.objects.create(
            company_id=5,
            service='hosting',
            event_type='hosting.deployment.failed',
            enabled=True,
            template_key='hosting.deployment.failed',
            language='en',
            recipient_mode='static',
            static_recipients=['ops@acme.com'],
        )
        OldRule.objects.create(
            company_id=5,
            service='hosting',
            event_type='hosting.app.created',
            enabled=False,
            template_key='hosting.app.created',
            language='',
            recipient_mode='hints',
            static_recipients=[],
        )
        OldRule.objects.create(
            company_id=None,
            service='hosting',
            event_type='hosting.deployment.succeeded',
            enabled=True,
            template_key='hosting.deployment.succeeded',
            language='',
            recipient_mode='hints',
            static_recipients=[],
        )
        executor.loader.build_graph()
        executor.migrate([('email', '0004_themes_and_rules')])
        apps = executor.loader.project_state([('email', '0004_themes_and_rules')]).apps
        Rule = apps.get_model('email', 'EmailRule')
        Version = apps.get_model('email', 'TemplateVersion')
        self.assertEqual(Rule.objects.filter(company_id=5).count(), 1)
        rule = Rule.objects.get(company_id=5)
        self.assertEqual(rule.event_type, 'hosting.deployment.failed')
        self.assertEqual(list(rule.static_recipients), ['ops@acme.com'])
        version = Version.objects.get(template_id=rule.template_id, number=1)
        self.assertEqual(version.theme_name, 'barebone')
        self.assertEqual(version.subject, 'Old subject')
        self.assertEqual(version.document['blocks'][0]['text'], 'Kept')
        self.assertFalse(Version.objects.filter(theme_name='shellui').exists())
