"""Template library, event copies, and list-style email rules."""

from __future__ import annotations

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.authapi.principal import EmailPrincipal
from apps.email.crypto import encrypt_json
from apps.email.document import validate_document
from apps.email.keys import issue_service_key
from apps.email.library import seed_files
from apps.email.models import CompanyProvider, EmailRule, EmailTemplate, EventSkip, LibraryTemplate, Message
from apps.email.service import SendError
from apps.providers.registry import fake_provider

LIBRARY_KEYS = {
    f'{set_key}.{name}'
    for set_key in ('barebone', 'matte', 'protocol')
    for name in (
        'activation',
        'feature-announcement',
        'password-reset',
        'product-update',
        'subscription-confirmation',
        'subscription-update',
        'text-only',
        'welcome',
    )
} | {
    f'{set_key}.{name}'
    for set_key in ('arcane', 'studio')
    for name in (
        'abandoned-cart',
        'activation',
        'newsletter',
        'order-confirmation',
        'order-shipping',
        'password-reset',
        'promo',
        'welcome',
    )
}


def _owner(company_id, email='owner@acme.com'):
    client = APIClient()
    client.force_authenticate(
        user=EmailPrincipal(user_id=3, company_id=company_id, email=email, is_company_owner=True)
    )
    return client


def _paragraph(text, href=None):
    node = {'type': 'text', 'text': text}
    if href:
        node['marks'] = [{'type': 'link', 'attrs': {'href': href}}]
    return {'type': 'doc', 'content': [{'type': 'paragraph', 'content': [node]}]}


class LibraryContentTests(TestCase):
    def test_library_has_all_forty_designs(self):
        self.assertEqual({seed['key'] for seed in seed_files()}, LIBRARY_KEYS)
        rows = LibraryTemplate.objects.filter(built_in=True)
        self.assertEqual(set(rows.values_list('key', flat=True)), LIBRARY_KEYS)
        self.assertFalse(rows.filter(html='').exists())
        welcome = rows.get(key='matte.welcome')
        self.assertIn('{{ company_name }}', welcome.html)
        self.assertIn('{{ system.assets_url }}/', welcome.html)
        self.assertIn('<!DOCTYPE html', welcome.html)

    def test_validate_document_rejects_unsafe_content(self):
        cases = {
            'node_not_allowed': {'type': 'doc', 'content': [{'type': 'script'}]},
            'unsafe_link': _paragraph('Go', 'ftp://files.example/report'),
            'template_tags_forbidden': _paragraph('{% if x %}'),
            'unsafe_attribute': {
                'type': 'doc',
                'content': [{'type': 'paragraph', 'attrs': {'style': 'width: expression(alert(1))'}}],
            },
            'unsafe_image': {
                'type': 'doc',
                'content': [{'type': 'image', 'attrs': {'src': 'http://tracker.example/pixel.png'}}],
            },
        }
        for code, document in cases.items():
            with self.subTest(code=code), self.assertRaises(SendError) as raised:
                validate_document(document)
            self.assertEqual(raised.exception.field_errors['document'], [code])
        self.assertEqual(validate_document(_paragraph('Hi', 'https://shellui.com'))['type'], 'doc')


class LibraryApiTests(TestCase):
    def setUp(self):
        self.owner = _owner(40)

    def test_list_create_duplicate_update_delete(self):
        self.assertEqual(APIClient().get('/api/v1/library?company_id=40').status_code, 401)
        listed = self.owner.get('/api/v1/library?company_id=40')
        self.assertEqual(listed.status_code, 200, listed.content)
        body = listed.json()
        self.assertEqual([item['key'] for item in body['sets']], ['barebone', 'matte', 'protocol', 'arcane', 'studio'])
        self.assertEqual(len(body['templates']), 40)
        self.assertTrue(all(item['html'] for item in body['templates']))
        builtin = next(item for item in body['templates'] if item['key'] == 'studio.promo')

        blank = self.owner.post('/api/v1/library?company_id=40', {'name': 'Plain'}, format='json')
        self.assertEqual(blank.status_code, 201, blank.content)
        self.assertFalse(blank.json()['built_in'])
        self.assertEqual(blank.json()['set'], '')
        self.assertTrue(blank.json()['key'].startswith('company.'))

        duplicate = self.owner.post('/api/v1/library?company_id=40', {'source_id': builtin['id']}, format='json')
        self.assertEqual(duplicate.status_code, 201, duplicate.content)
        copy = duplicate.json()
        self.assertEqual(copy['name'], f'{builtin["name"]} copy')
        self.assertEqual(copy['set'], 'studio')
        self.assertTrue(copy['head'])
        self.assertEqual(copy['document'], LibraryTemplate.objects.get(key='studio.promo').document)

        for method in ('put', 'delete'):
            refused = getattr(self.owner, method)(f'/api/v1/library/{builtin["id"]}?company_id=40', {'name': 'x'}, format='json')
            self.assertEqual(refused.status_code, 409)
            self.assertEqual(refused.json()['error_code'], 'library_built_in')

        updated = self.owner.put(
            f'/api/v1/library/{copy["id"]}?company_id=40',
            {'name': 'Spring promo', 'document': _paragraph('Hello {{ company_name }}')},
            format='json',
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()['name'], 'Spring promo')
        self.assertIn('Hello {{ company_name }}', updated.json()['html'])
        unsafe = self.owner.put(
            f'/api/v1/library/{copy["id"]}?company_id=40',
            {'document': _paragraph('Go', 'javascript:alert(1)')},
            format='json',
        )
        self.assertEqual(unsafe.status_code, 400)

        other = _owner(41, 'owner@other.com')
        self.assertEqual(other.get(f'/api/v1/library/{copy["id"]}?company_id=41').status_code, 404)
        self.assertEqual(len(other.get('/api/v1/library?company_id=41').json()['templates']), 40)
        self.assertEqual(len(self.owner.get('/api/v1/library?company_id=40').json()['templates']), 42)

        deleted = self.owner.delete(f'/api/v1/library/{copy["id"]}?company_id=40')
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(LibraryTemplate.objects.filter(pk=copy['id']).exists())

    def test_catalog_exposes_link_token_and_default_design(self):
        events = {row['event_type']: row for row in self.owner.get('/api/v1/catalog').json()['events']}
        magic = events['identity.auth.magic_link.requested']
        self.assertEqual(magic['link_token'], 'magic_link_url')
        self.assertEqual(magic['default_template'], 'barebone.activation')
        self.assertIn('fr', magic['suggested'])


@override_settings(
    EMAIL_DELIVER_SYNC=True,
    EMAIL_FALLBACK_PROVIDER='fake',
    EMAIL_ALLOW_FAKE_PROVIDER=True,
    EMAIL_PUBLIC_URL='https://mail.shellui.test',
    DEBUG=True,
)
class RuleTests(TestCase):
    def setUp(self):
        fake_provider().sent.clear()
        self.owner = _owner(40)
        self.hosting_key, _client = issue_service_key(
            service='hosting',
            allowed_lanes=['transactional'],
            allowed_template_prefixes=['hosting.'],
        )
        self.identity_key, _client = issue_service_key(
            service='identity',
            allowed_lanes=['auth', 'transactional'],
            allowed_template_prefixes=['identity.'],
        )
        CompanyProvider.objects.create(
            company_id=40,
            provider='fake',
            from_email='ops@acme.com',
            from_name='Acme',
            credentials_ciphertext=encrypt_json({'marker': 'company'}, setting='EMAIL_CREDENTIALS_KEY'),
            credentials_hint='••••test',
            configured=True,
        )

    def _library_id(self, key):
        return LibraryTemplate.objects.get(key=key).pk

    def _rule(self, event_type, key='studio.promo', **extra):
        return self.owner.post(
            '/api/v1/rules?company_id=40',
            {'event_type': event_type, 'content': {'library_id': self._library_id(key)}, **extra},
            format='json',
        )

    def test_builtin_auth_rules_use_default_designs(self):
        listed = self.owner.get('/api/v1/rules?company_id=40&service=identity')
        self.assertEqual(listed.status_code, 200, listed.content)
        events = {row['event_type']: row for row in listed.json()['rules']}
        self.assertEqual(set(events), {'identity.auth.magic_link.requested', 'identity.user.invited'})
        magic = events['identity.auth.magic_link.requested']
        self.assertTrue(magic['built_in'])
        template = EmailTemplate.objects.get(pk=magic['template_id'])
        self.assertEqual(template.source_key, 'barebone.activation')
        self.assertEqual(template.set, 'barebone')
        version = template.versions.get(number=template.active_version)
        self.assertIn('{{ magic_link_url }}', version.html)
        self.assertNotIn('action_url', version.html)
        removed = self.owner.delete(f'/api/v1/rules/{magic["id"]}?company_id=40')
        self.assertEqual(removed.json()['error_code'], 'rule_built_in')
        disabled = self.owner.patch(f'/api/v1/rules/{magic["id"]}?company_id=40', {'enabled': False}, format='json')
        self.assertEqual(disabled.json()['error_code'], 'rule_built_in')

    def test_rule_creates_an_editable_copy_of_a_library_design(self):
        missing = self.owner.post(
            '/api/v1/rules?company_id=40',
            {'event_type': 'hosting.deployment.failed', 'content': {}},
            format='json',
        )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json()['field_errors'], {'content': ['library_id_required']})
        unknown = self.owner.post(
            '/api/v1/rules?company_id=40',
            {'event_type': 'not.an.event', 'content': {'library_id': 1}},
            format='json',
        )
        self.assertEqual(unknown.json()['error_code'], 'event_unknown')

        created = self._rule(
            'hosting.deployment.failed',
            recipient_mode='static',
            static_recipients=['ops@acme.com'],
        )
        self.assertEqual(created.status_code, 201, created.content)
        body = created.json()
        template = EmailTemplate.objects.get(pk=body['template_id'])
        self.assertTrue(template.template_key.startswith('company.'))
        self.assertEqual((template.source_key, template.set, template.name), ('studio.promo', 'studio', 'Deployment failed'))
        version = template.versions.get(number=template.active_version)
        self.assertEqual(version.state, 'published')
        self.assertIn('https://example.com', version.html)
        self.assertNotIn('action_url', version.html)
        self.assertFalse(LibraryTemplate.objects.filter(key=template.template_key).exists())

        summary = self.owner.get(f'/api/v1/templates/{template.pk}').json()
        self.assertEqual(summary['source_key'], 'studio.promo')
        self.assertTrue(summary['head'])

        restarted = self.owner.post(
            f'/api/v1/templates/{template.pk}/versions',
            {'library_id': self._library_id('matte.welcome')},
            format='json',
        )
        self.assertEqual(restarted.status_code, 201, restarted.content)
        draft = template.versions.get(number=restarted.json()['number'])
        self.assertEqual(draft.subject, version.subject)
        self.assertNotEqual(draft.document, version.document)
        template.refresh_from_db()
        self.assertEqual((template.source_key, template.set), ('matte.welcome', 'matte'))

        edited = self.owner.post(
            f'/api/v1/templates/{template.pk}/versions',
            {'subject': 'Deploy of {{ display_name }} failed', 'document': _paragraph('Error {{ error }}')},
            format='json',
        )
        self.assertEqual(edited.status_code, 201, edited.content)
        published = self.owner.post(f'/api/v1/templates/{template.pk}/versions/{edited.json()["number"]}/publish')
        self.assertEqual(published.status_code, 200, published.content)
        self.assertTrue(published.json()['checksum'])

        deleted = self.owner.delete(f'/api/v1/rules/{body["id"]}?company_id=40')
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(EmailTemplate.objects.filter(pk=template.pk).exists())

    def test_template_variables_must_fit_the_rule_event(self):
        slug = self._rule('hosting.deployment.succeeded').json()
        template_id = slug['template_id']
        draft = self.owner.post(
            f'/api/v1/templates/{template_id}/versions',
            {'subject': 'Uses {{ slug }}', 'document': _paragraph('Slug {{ slug }}')},
            format='json',
        )
        self.owner.post(f'/api/v1/templates/{template_id}/versions/{draft.json()["number"]}/publish')
        failed = self._rule('hosting.deployment.failed').json()
        patched = self.owner.patch(f'/api/v1/rules/{failed["id"]}?company_id=40', {'template_id': template_id}, format='json')
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual(patched.json()['template_id'], failed['template_id'])
        compatible = self.owner.get('/api/v1/templates?company_id=40&event_type=hosting.deployment.failed')
        ids = {item['id'] for item in compatible.json()['templates']}
        self.assertIn(failed['template_id'], ids)
        self.assertNotIn(template_id, ids)

    def test_auth_copies_reject_links_to_other_hosts(self):
        rule = next(
            row
            for row in self.owner.get('/api/v1/rules?company_id=40&service=identity').json()['rules']
            if row['event_type'] == 'identity.auth.magic_link.requested'
        )
        bad = self.owner.post(
            f'/api/v1/templates/{rule["template_id"]}/versions',
            {
                'subject': 'Sign in',
                'document': {
                    'type': 'doc',
                    'content': [
                        _paragraph('Sign in', '{{ magic_link_url }}')['content'][0],
                        _paragraph('Other', 'https://evil.example/login')['content'][0],
                    ],
                },
            },
            format='json',
        )
        self.assertEqual(bad.status_code, 201, bad.content)
        refused = self.owner.post(f'/api/v1/templates/{rule["template_id"]}/versions/{bad.json()["number"]}/publish')
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.json()['error_code'], 'auth_link_host_not_allowed')

    def test_several_rules_send_once_per_rule_and_retry_is_idempotent(self):
        first = self._rule('hosting.deployment.failed', key='barebone.text-only')
        second = self._rule(
            'hosting.deployment.failed',
            recipient_mode='static',
            static_recipients=['ops@acme.com'],
        )
        self.assertEqual(second.status_code, 201, second.content)
        self.assertNotEqual(first.json()['template_id'], second.json()['template_id'])
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
        addresses = sorted(message.to_email for message in Message.objects.filter(company_id=40))
        self.assertEqual(addresses, ['ada@acme.com', 'ops@acme.com'])
        replay = client.post('/api/v1/events', payload, format='json')
        self.assertTrue(replay.json()['idempotent_replay'])
        self.assertEqual(len(fake_provider().sent), 2)
        html = fake_provider().sent[-1].html
        self.assertIn('https://mail.shellui.test/static/library/', html)
        self.assertNotIn('{{', html)

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
        self.assertEqual(magic.json()['messages'][0]['status'], 'sent')
        sent = fake_provider().sent[-1]
        self.assertIn('id.shellui.com', sent.html)
        self.assertIn('Acme', sent.subject)
        self.assertEqual(EmailRule.objects.filter(company_id=40, built_in=True).count(), 2)

    def _translated_draft(self, template_id, translations):
        document = {
            'type': 'doc',
            'content': [
                {'type': 'paragraph', 'attrs': {'textId': 'greet'}, 'content': [{'type': 'text', 'text': 'Deploy failed'}]},
                {'type': 'paragraph', 'attrs': {'textId': 'error'}, 'content': [{'type': 'text', 'text': 'Error {{ error }}'}]},
            ],
        }
        return self.owner.post(
            f'/api/v1/templates/{template_id}/versions',
            {'subject': 'Deploy of {{ display_name }} failed', 'document': document, 'translations': translations},
            format='json',
        )

    def test_translations_render_and_send_in_the_recipient_language(self):
        rule = self._rule('hosting.deployment.failed', recipient_mode='static', static_recipients=['ops@acme.com']).json()
        fr = {
            'subject': 'Échec du déploiement de {{ display_name }}',
            'preheader': '',
            'blocks': {'greet': {'content': [{'type': 'text', 'text': 'Le déploiement a échoué'}], 'source': 'x1'}},
        }
        draft = self._translated_draft(rule['template_id'], {'fr': fr})
        self.assertEqual(draft.status_code, 201, draft.content)
        version = EmailTemplate.objects.get(pk=rule['template_id']).versions.get(number=draft.json()['number'])
        self.assertIn('Le déploiement a échoué', version.rendered['fr']['html'])
        self.assertIn('Error {{ error }}', version.rendered['fr']['html'])
        self.assertNotIn('textId', version.html)
        listed = self.owner.get(f'/api/v1/templates/{rule["template_id"]}/versions').json()['versions']
        self.assertEqual(listed[-1]['translations']['fr']['blocks']['greet']['source'], 'x1')
        published = self.owner.post(f'/api/v1/templates/{rule["template_id"]}/versions/{draft.json()["number"]}/publish')
        self.assertEqual(published.status_code, 200, published.content)

        self.owner.patch(f'/api/v1/rules/{rule["id"]}?company_id=40', {'language': 'fr'}, format='json')
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.hosting_key}')
        sent = client.post(
            '/api/v1/events',
            {
                'company_id': 40,
                'service': 'hosting',
                'event_type': 'hosting.deployment.failed',
                'payload': {'display_name': 'Docs', 'error': 'boom'},
            },
            format='json',
        )
        self.assertEqual(sent.status_code, 202, sent.content)
        message = fake_provider().sent[-1]
        self.assertEqual(message.subject, 'Échec du déploiement de Docs')
        self.assertIn('Le déploiement a échoué', message.html)
        self.assertIn('Error boom', message.html)
        self.assertEqual(Message.objects.filter(company_id=40).latest('accepted_at').language, 'fr')

    def test_translations_are_validated_per_language(self):
        rule = self._rule('hosting.deployment.failed').json()
        cases = {
            'language_not_available': {'en': {'subject': 'Same', 'blocks': {}}},
            'validation_failed': {'fr': {'blocks': {'greet': {'content': [{'type': 'image', 'attrs': {'src': ''}}]}}}},
        }
        for code, translations in cases.items():
            with self.subTest(code=code):
                refused = self._translated_draft(rule['template_id'], translations)
                self.assertEqual(refused.status_code, 400, refused.content)
                self.assertEqual(refused.json()['error_code'], code)
        unsafe = self._translated_draft(
            rule['template_id'],
            {
                'fr': {
                    'blocks': {
                        'greet': {
                            'content': [
                                {'type': 'text', 'text': 'Ici', 'marks': [{'type': 'link', 'attrs': {'href': 'ftp://x.example'}}]}
                            ]
                        }
                    }
                }
            },
        )
        self.assertEqual(unsafe.json()['field_errors'], {'document': ['unsafe_link']})
        self.assertEqual(unsafe.json()['language'], 'fr')

    def test_auth_translations_keep_the_sign_in_link(self):
        rule = next(
            row
            for row in self.owner.get('/api/v1/rules?company_id=40&service=identity').json()['rules']
            if row['event_type'] == 'identity.auth.magic_link.requested'
        )
        document = {
            'type': 'doc',
            'content': [
                {
                    'type': 'paragraph',
                    'attrs': {'textId': 'link'},
                    'content': [{'type': 'text', 'text': 'Sign in', 'marks': [{'type': 'link', 'attrs': {'href': '{{ magic_link_url }}'}}]}],
                }
            ],
        }
        draft = self.owner.post(
            f'/api/v1/templates/{rule["template_id"]}/versions',
            {
                'subject': 'Sign in',
                'document': document,
                'translations': {'fr': {'blocks': {'link': {'content': [{'type': 'text', 'text': 'Se connecter'}]}}}},
            },
            format='json',
        )
        self.assertEqual(draft.status_code, 201, draft.content)
        refused = self.owner.post(f'/api/v1/templates/{rule["template_id"]}/versions/{draft.json()["number"]}/publish')
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.json()['error_code'], 'auth_link_missing')
        self.assertEqual(refused.json()['language'], 'fr')

    def test_start_over_keeps_translated_subjects_only(self):
        rule = self._rule('hosting.deployment.failed').json()
        fr = {'subject': 'Échec', 'preheader': 'Détails', 'blocks': {'greet': {'content': [{'type': 'text', 'text': 'Oups'}]}}}
        self._translated_draft(rule['template_id'], {'fr': fr})
        restarted = self.owner.post(
            f'/api/v1/templates/{rule["template_id"]}/versions',
            {'library_id': self._library_id('matte.welcome')},
            format='json',
        )
        self.assertEqual(restarted.status_code, 201, restarted.content)
        version = EmailTemplate.objects.get(pk=rule['template_id']).versions.get(number=restarted.json()['number'])
        self.assertEqual(version.translations, {'fr': {'subject': 'Échec', 'preheader': 'Détails', 'blocks': {}}})
        self.assertIn('fr', version.rendered)

    def test_send_test_uses_the_draft_with_sample_values(self):
        rule = self._rule('hosting.deployment.failed').json()
        draft = self.owner.post(
            f'/api/v1/templates/{rule["template_id"]}/versions',
            {'subject': 'Deploy of {{ display_name }}', 'document': _paragraph('Error {{ error }}')},
            format='json',
        )
        self.assertEqual(draft.status_code, 201, draft.content)
        sent = self.owner.post(f'/api/v1/templates/{rule["template_id"]}/send-test', {}, format='json')
        self.assertEqual(sent.status_code, 200, sent.content)
        message = fake_provider().sent[-1]
        self.assertEqual(message.to_email, 'owner@acme.com')
        self.assertIn('Deploy of', message.subject)
        self.assertNotIn('{{', message.html)
