"""The staff notice (``identity.auth.magic_link.staff_blocked``) only ever uses Shellui's copy.

Identity sends it instead of a magic link when a staff account asks for one. It has
no sign-in link or token, goes to exactly one address, and a company cannot edit
it, put a rule on it, or receive it through ``POST /api/v1/events``.
"""

from __future__ import annotations

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.authapi.principal import EmailPrincipal
from apps.email.auth_templates import validate_auth_template
from apps.email.builtin_auth import COPY, STAFF_BLOCKED, builtin_document
from apps.email.catalog import get_definition
from apps.email.crypto import encrypt_json
from apps.email.keys import issue_service_key
from apps.email.models import CompanyProvider, EmailRule, EmailTemplate, LibraryTemplate, Message
from apps.providers.registry import fake_provider

SIGN_IN = 'https://app.acme.com/'


class _NoticeSetup(TestCase):
    def setUp(self):
        cache.clear()
        fake_provider().sent.clear()
        self.owner = APIClient()
        self.owner.force_authenticate(
            user=EmailPrincipal(user_id=3, company_id=40, email='owner@acme.com', is_company_owner=True)
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

    def _identity(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.identity_key}')
        return client

    def _send(self, to=None, language='en', variables=None):
        return self._identity().post(
            '/api/v1/send',
            {
                'company_id': 40,
                'template_key': STAFF_BLOCKED,
                'language': language,
                'to': to if to is not None else [{'email': 'staff@shellui.com'}],
                'variables': variables if variables is not None else {'company_name': 'Acme', 'sign_in_url': SIGN_IN},
            },
            format='json',
        )

    def _batch(self, template_key, items):
        return self._identity().post(
            '/api/v1/send/batch',
            {'company_id': 40, 'template_key': template_key, 'language': 'en', 'items': items},
            format='json',
        )


@override_settings(
    EMAIL_DELIVER_SYNC=True,
    EMAIL_FALLBACK_PROVIDER='fake',
    EMAIL_ALLOW_FAKE_PROVIDER=True,
    EMAIL_AUTH_LINK_HOSTS=['id.shellui.com'],
    EMAIL_PUBLIC_URL='https://mail.shellui.test',
    DEBUG=True,
)
class StaffBlockedNoticeTests(_NoticeSetup):
    def test_catalog_definition_is_auth_lane_and_not_company_editable(self):
        definition = get_definition(STAFF_BLOCKED)
        self.assertEqual(definition['lane_class'], 'auth')
        self.assertFalse(definition['company_editable'])
        self.assertEqual(definition['link_token'], '')
        self.assertFalse(any(item.get('required') for item in definition['variables'] if item['type'] == 'url'))
        tokens = {item['token'] for item in definition['variables']}
        self.assertEqual(tokens, {'company_name', 'sign_in_url'})
        self.assertFalse(any(item.get('sensitive') for item in definition['variables']))

    def test_send_goes_to_one_address_with_builtin_copy_and_no_token(self):
        sent = self._send()
        self.assertEqual(sent.status_code, 202, sent.content)
        self.assertEqual([m['to'] for m in sent.json()['messages']], ['staff@shellui.com'])
        self.assertEqual(sent.json()['messages'][0]['lane'], 'auth')
        self.assertEqual(len(fake_provider().sent), 1)
        message = fake_provider().sent[0]
        self.assertEqual(message.to_email, 'staff@shellui.com')
        self.assertEqual(message.subject, '[Shellui] Sign in to Acme with your usual sign-in method')
        self.assertIn('No sign-in link for staff accounts', message.html)
        self.assertIn("staff accounts can&#x27;t sign in with an email link", message.html.replace('&#39;', '&#x27;'))
        self.assertIn('Sign in with your usual sign-in method instead.', message.text)
        self.assertNotIn('password', message.text.lower())
        self.assertIn(f'href="{SIGN_IN}"', message.html)
        self.assertIn('Go to sign-in', message.html)
        for body in (message.html, message.text, message.subject):
            self.assertNotIn('token', body.lower())
            self.assertNotIn('magic-link/verify', body)
        stored = Message.objects.get()
        self.assertEqual(stored.event_type, STAFF_BLOCKED)
        self.assertEqual(stored.template_key, STAFF_BLOCKED)

    def test_french_copy(self):
        sent = self._send(language='fr')
        self.assertEqual(sent.status_code, 202, sent.content)
        message = fake_provider().sent[0]
        self.assertEqual(message.subject, '[Shellui] Connectez-vous à Acme avec votre méthode de connexion habituelle')
        self.assertIn('Pas de lien de connexion pour les comptes staff', message.html)
        self.assertIn('Aller à la connexion', message.html)
        self.assertIn('Connectez-vous avec votre méthode de connexion habituelle.', message.text)

    def test_exactly_one_recipient(self):
        refused = self._send(to=[{'email': 'staff@shellui.com'}, {'email': 'owner@acme.com'}])
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertEqual(refused.json()['error_code'], 'auth_single_recipient')
        self.assertEqual(fake_provider().sent, [])

    def test_no_button_without_a_sign_in_page(self):
        sent = self._send(variables={'company_name': 'Acme'})
        self.assertEqual(sent.status_code, 202, sent.content)
        message = fake_provider().sent[0]
        self.assertIn('No sign-in link for staff accounts', message.html)
        self.assertNotIn('Go to sign-in', message.html)
        self.assertNotIn('href=', message.html.split('<body', 1)[1])
        self.assertNotIn('Go to sign-in', message.text)

    def test_sign_in_url_must_be_a_safe_scheme(self):
        sent = self._send(variables={'company_name': 'Acme', 'sign_in_url': 'javascript:alert(1)'})
        self.assertEqual(sent.status_code, 202, sent.content)
        self.assertEqual(fake_provider().sent, [])
        self.assertEqual(Message.objects.get().status, Message.STATUS_FAILED)

    def test_auth_rate_limit_applies(self):
        with override_settings(EMAIL_RECIPIENT_AUTH_LIMIT=1):
            self.assertEqual(self._send().status_code, 202)
            limited = self._send()
        self.assertEqual(limited.status_code, 429, limited.content)
        self.assertEqual(limited.json()['error_code'], 'recipient_rate_limited')

    def test_company_cannot_create_a_rule_on_it(self):
        refused = self.owner.post(
            '/api/v1/rules?company_id=40',
            {
                'event_type': STAFF_BLOCKED,
                'recipient_mode': 'static',
                'static_recipients': ['owner@acme.com'],
                'content': {'library_id': LibraryTemplate.objects.get(key='barebone.text-only').pk},
            },
            format='json',
        )
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertEqual(refused.json()['error_code'], 'auth_event_rule_forbidden')
        self.assertFalse(EmailRule.objects.filter(event_type=STAFF_BLOCKED).exists())

    def test_no_builtin_rule_or_company_copy_is_created(self):
        rules = self.owner.get('/api/v1/rules?company_id=40&service=identity').json()['rules']
        self.assertTrue(any(row['event_type'] == 'identity.auth.magic_link.requested' for row in rules))
        self.assertFalse(any(row['event_type'] == STAFF_BLOCKED for row in rules))
        self.assertFalse(EmailRule.objects.filter(event_type=STAFF_BLOCKED).exists())
        self.assertFalse(EmailTemplate.objects.filter(event_type=STAFF_BLOCKED).exists())

    def test_catalog_does_not_offer_it_to_editors(self):
        catalog = self.owner.get('/api/v1/catalog').json()
        keys = {row['event_type'] for row in catalog['events']}
        self.assertIn('identity.auth.magic_link.requested', keys)
        self.assertNotIn(STAFF_BLOCKED, keys)

    def test_send_ignores_company_copies_and_rules_written_directly(self):
        """Rows that the API refuses, written straight to the database, still never apply."""
        template = EmailTemplate.objects.create(
            template_key='company.sneaky', company_id=40, language='en', event_type=STAFF_BLOCKED, active_version=1
        )
        template.versions.create(
            number=1,
            state='published',
            subject='Owner subject',
            html='<p>Owner copy <a href="https://evil.example/phish">Sign in</a></p>',
            text='Owner copy',
        )
        EmailRule.objects.create(
            company_id=40,
            service='identity',
            event_type=STAFF_BLOCKED,
            enabled=True,
            template=template,
            recipient_mode='static',
            static_recipients=['owner@acme.com'],
            built_in=True,
        )
        sent = self._send()
        self.assertEqual(sent.status_code, 202, sent.content)
        self.assertEqual([m.to_email for m in fake_provider().sent], ['staff@shellui.com'])
        message = fake_provider().sent[0]
        self.assertNotIn('evil.example', message.html)
        self.assertNotIn('Owner copy', message.html)
        self.assertIn('No sign-in link for staff accounts', message.html)

    def test_event_ingest_never_sends_it(self):
        response = self._identity().post(
            '/api/v1/events',
            {
                'company_id': 40,
                'service': 'identity',
                'event_type': STAFF_BLOCKED,
                'payload': {'company_name': 'Acme', 'sign_in_url': SIGN_IN},
                'recipients': [{'email': 'staff@shellui.com'}],
            },
            format='json',
        )
        self.assertIn(response.status_code, (200, 202), response.content)
        self.assertEqual(response.json().get('skipped_reason'), 'no_rule')
        self.assertEqual(fake_provider().sent, [])
        self.assertFalse(Message.objects.exists())

    def test_builtin_copy_passes_the_auth_checks(self):
        definition = get_definition(STAFF_BLOCKED)
        for language in COPY[STAFF_BLOCKED]:
            with self.subTest(language=language):
                pack = definition['languages'][language]
                validate_auth_template(
                    definition, builtin_document(definition, language), pack['subject'], pack['preheader']
                )


AUTH_VARIABLES = {
    'identity.auth.magic_link.requested': {
        'company_name': 'Acme',
        'magic_link_url': 'https://id.shellui.com/api/v1/magic-link/verify?token=batch-secret',
    },
    'identity.user.invited': {'company_name': 'Acme', 'invitation_url': 'https://app.acme.com/'},
    STAFF_BLOCKED: {'company_name': 'Acme', 'sign_in_url': SIGN_IN},
}


@override_settings(
    EMAIL_DELIVER_SYNC=True,
    EMAIL_FALLBACK_PROVIDER='fake',
    EMAIL_ALLOW_FAKE_PROVIDER=True,
    EMAIL_AUTH_LINK_HOSTS=['id.shellui.com'],
    EMAIL_PUBLIC_URL='https://mail.shellui.test',
    DEBUG=True,
)
class AuthBatchSingleRecipientTests(_NoticeSetup):
    """``/send/batch`` takes one item with one address for auth mail, like ``/send``."""

    def test_auth_batch_with_several_items_is_refused(self):
        for template_key, variables in AUTH_VARIABLES.items():
            with self.subTest(template_key=template_key):
                cache.clear()
                refused = self._batch(
                    template_key,
                    [
                        {'to': {'email': 'staff@shellui.com'}, 'variables': variables},
                        {'to': {'email': 'owner@acme.com'}, 'variables': variables},
                    ],
                )
                self.assertEqual(refused.status_code, 400, refused.content)
                self.assertEqual(refused.json()['error_code'], 'auth_single_recipient')
                self.assertEqual(refused.json()['field_errors'], {'items': ['single_recipient']})
        self.assertEqual(fake_provider().sent, [])
        self.assertFalse(Message.objects.exists())

    def test_auth_batch_item_with_a_list_of_addresses_is_refused(self):
        refused = self._batch(
            STAFF_BLOCKED,
            [{'to': [{'email': 'staff@shellui.com'}, {'email': 'owner@acme.com'}], 'variables': AUTH_VARIABLES[STAFF_BLOCKED]}],
        )
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertEqual(refused.json()['error_code'], 'auth_single_recipient')
        self.assertFalse(Message.objects.exists())

    def test_auth_batch_with_one_item_sends_to_that_address(self):
        for template_key, variables in AUTH_VARIABLES.items():
            with self.subTest(template_key=template_key):
                cache.clear()
                fake_provider().sent.clear()
                sent = self._batch(template_key, [{'to': {'email': 'staff@shellui.com'}, 'variables': variables}])
                self.assertEqual(sent.status_code, 202, sent.content)
                self.assertEqual(len(sent.json()['accepted']), 1)
                self.assertEqual([m.to_email for m in fake_provider().sent], ['staff@shellui.com'])

    def test_empty_auth_batch_is_still_a_validation_error(self):
        refused = self._batch(STAFF_BLOCKED, [])
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertEqual(refused.json()['error_code'], 'validation_failed')

    def test_transactional_batch_still_takes_several_items(self):
        variables = {'company_name': 'Acme'}
        sent = self._batch(
            'identity.user.invitation_revoked',
            [
                {'to': {'email': 'a@acme.com'}, 'variables': {**variables, 'recipient_email': 'a@acme.com'}},
                {'to': {'email': 'b@acme.com'}, 'variables': {**variables, 'recipient_email': 'b@acme.com'}},
            ],
        )
        self.assertEqual(sent.status_code, 202, sent.content)
        self.assertEqual(len(sent.json()['accepted']), 2)
