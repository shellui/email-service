"""Broadcasts: audience from identity, one language per recipient, bulk lane or Resend Broadcasts."""

from __future__ import annotations

import json
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.authapi.principal import EmailPrincipal
from apps.email.broadcasts import apply_broadcast_event, apply_contact_unsubscribe
from apps.email.crypto import email_hmac, encrypt_json
from apps.email.models import (
    Broadcast,
    BroadcastRecipient,
    CompanyProvider,
    EmailTemplate,
    Suppression,
    Unsubscribe,
)
from apps.providers.registry import fake_provider

COMPANY = 50
PEOPLE = [
    {'id': 1, 'email': 'ada@acme.com', 'first_name': 'Ada', 'last_name': 'L', 'language': 'en'},
    {'id': 2, 'email': 'bea@acme.com', 'first_name': 'Bea', 'last_name': 'M', 'language': 'fr'},
    {'id': 3, 'email': 'carl@acme.com', 'first_name': 'Carl', 'last_name': 'N', 'language': 'de'},
    {'id': 4, 'email': 'dan@acme.com', 'first_name': 'Dan', 'last_name': 'O', 'language': 'en'},
    {'id': 5, 'email': 'eve@acme.com', 'first_name': 'Eve', 'last_name': 'P', 'language': 'fr'},
]


def _owner(company_id=COMPANY):
    client = APIClient()
    client.force_authenticate(
        user=EmailPrincipal(user_id=3, company_id=company_id, email='owner@acme.com', is_company_owner=True)
    )
    client.credentials(HTTP_AUTHORIZATION='Bearer owner-jwt')
    return client


def _identity(rows):
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append({'url': url, 'params': params, 'headers': headers})
        response = mock.Mock(status_code=200)
        response.json.return_value = {'count': len(rows), 'page': 1, 'page_size': 1000, 'results': rows}
        return response

    return get, calls


@override_settings(
    EMAIL_DELIVER_SYNC=True,
    EMAIL_ALLOW_FAKE_PROVIDER=True,
    EMAIL_PUBLIC_URL='https://mail.shellui.test',
    IDENTITY_SERVICE_URL='https://id.test',
    EMAIL_RESEND_BROADCAST_RPS=0,
    DEBUG=True,
)
class BroadcastTests(TestCase):
    def setUp(self):
        fake_provider().sent.clear()
        self.owner = _owner()
        self.provider = CompanyProvider.objects.create(
            company_id=COMPANY,
            provider='fake',
            from_email='ops@acme.com',
            bulk_from_email='news@news.acme.com',
            from_name='Acme',
            credentials_ciphertext=encrypt_json({'marker': 'company'}, setting='EMAIL_CREDENTIALS_KEY'),
            configured=True,
        )
        Unsubscribe.objects.create(company_id=COMPANY, email_hmac=email_hmac('dan@acme.com'), category='bulk', source='link')
        Suppression.objects.create(
            company_id=COMPANY,
            email_hmac=email_hmac('eve@acme.com'),
            email_masked='e***@acme.com',
            reason=Suppression.REASON_HARD_BOUNCE,
            lanes=[],
        )

    def _create(self, **audience):
        response = self.owner.post(
            '/api/v1/broadcasts',
            {
                'name': 'October news',
                'source_key': 'barebone.product-update',
                'language': 'en',
                'audience': audience or {'mode': 'filter', 'roles': ['member']},
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def _translate(self, broadcast):
        template_id = broadcast['template_id']
        document = self.owner.get(f'/api/v1/templates/{template_id}/versions').json()['versions'][0]['document']
        saved = self.owner.post(
            f'/api/v1/templates/{template_id}/versions',
            {
                'document': document,
                'subject': 'Hello {{ first_name|default:"there" }}',
                'translations': {'fr': {'subject': 'Bonjour {{ first_name|default:"toi" }}', 'blocks': {}}},
            },
            format='json',
        )
        self.assertEqual(saved.status_code, 201, saved.content)
        number = saved.json()['number']
        published = self.owner.post(f'/api/v1/templates/{template_id}/versions/{number}/publish')
        self.assertEqual(published.status_code, 200, published.content)

    def test_create_keeps_the_content_out_of_event_templates(self):
        broadcast = self._create()
        self.assertEqual(broadcast['state'], 'draft')
        self.assertEqual(broadcast['sender']['from_email'], 'news@news.acme.com')
        self.assertEqual(broadcast['sender']['delivery'], 'bulk_lane')
        template = EmailTemplate.objects.get(pk=broadcast['template_id'])
        self.assertEqual(template.kind, EmailTemplate.KIND_BROADCAST)
        self.assertTrue(template.active_version)
        listed = self.owner.get('/api/v1/templates')
        self.assertNotIn(template.pk, [row['id'] for row in listed.json()['templates']])
        self.assertEqual(self.owner.get(f'/api/v1/templates/{template.pk}').json()['kind'], 'broadcast')
        self.assertEqual([row['id'] for row in self.owner.get('/api/v1/broadcasts').json()['broadcasts']], [broadcast['id']])

    def test_audience_is_validated(self):
        broadcast = self._create()
        response = self.owner.patch(
            f'/api/v1/broadcasts/{broadcast["id"]}', {'audience': {'mode': 'filter', 'roles': ['admin']}}, format='json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('audience.roles', response.json()['field_errors'])
        response = self.owner.patch(
            f'/api/v1/broadcasts/{broadcast["id"]}', {'audience': {'mode': 'pick', 'emails': ['nope']}}, format='json'
        )
        self.assertIn('audience.emails', response.json()['field_errors'])

    def test_preview_counts_each_send_language_and_the_skips(self):
        broadcast = self._create()
        self._translate(broadcast)
        get, calls = _identity(PEOPLE)
        with mock.patch('apps.email.broadcasts.requests.get', side_effect=get):
            response = self.owner.post(
                f'/api/v1/broadcasts/{broadcast["id"]}/preview',
                {'audience': {'mode': 'filter', 'roles': ['member'], 'seen_after': '2026-01-01'}},
                format='json',
            )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body['total'], 5)
        self.assertEqual(body['sendable'], 3)
        self.assertEqual(body['unsubscribed'], 1)
        self.assertEqual(body['suppressed'], 1)
        self.assertEqual(body['languages'], {'en': 2, 'fr': 1})
        self.assertEqual(calls[0]['url'], 'https://id.test/api/v1/users/audience')
        self.assertEqual(calls[0]['headers']['Authorization'], 'Bearer owner-jwt')
        self.assertEqual(calls[0]['params']['roles'], 'member')
        self.assertEqual(calls[0]['params']['seen_after'], '2026-01-01')
        self.assertEqual(calls[0]['params']['access'], 'enabled')

    def test_picked_addresses_skip_identity(self):
        broadcast = self._create(mode='pick', emails=['guest@example.org', 'GUEST@example.org'])
        with mock.patch('apps.email.broadcasts.requests.get') as get:
            response = self.owner.post(f'/api/v1/broadcasts/{broadcast["id"]}/preview', {}, format='json')
        get.assert_not_called()
        self.assertEqual(response.json()['total'], 1)
        self.assertEqual(response.json()['languages'], {'en': 1})

    def test_send_through_the_bulk_lane_in_each_language(self):
        broadcast = self._create()
        self._translate(broadcast)
        get, _calls = _identity(PEOPLE)
        with mock.patch('apps.email.broadcasts.requests.get', side_effect=get):
            response = self.owner.post(f'/api/v1/broadcasts/{broadcast["id"]}/send')
        self.assertEqual(response.status_code, 202, response.content)
        body = response.json()
        self.assertEqual(body['state'], 'sent')
        self.assertEqual(body['delivery'], 'bulk_lane')
        self.assertEqual(body['counts']['sent'], 3)
        self.assertEqual(body['counts']['skipped_unsubscribed'], 1)
        self.assertEqual(body['counts']['skipped_suppressed'], 1)
        sent = {message.to_email: message for message in fake_provider().sent}
        self.assertEqual(set(sent), {'ada@acme.com', 'bea@acme.com', 'carl@acme.com'})
        self.assertEqual(sent['bea@acme.com'].subject, 'Bonjour Bea')
        self.assertEqual(sent['carl@acme.com'].subject, 'Hello Carl')
        self.assertEqual(sent['ada@acme.com'].from_email, 'news@news.acme.com')
        self.assertIn('List-Unsubscribe', sent['ada@acme.com'].headers)
        self.assertFalse(BroadcastRecipient.objects.exclude(contact_ciphertext='').exists())
        again = self.owner.patch(f'/api/v1/broadcasts/{broadcast["id"]}', {'name': 'Again'}, format='json')
        self.assertEqual(again.status_code, 409)
        edit = self.owner.post(
            f'/api/v1/templates/{broadcast["template_id"]}/versions', {'subject': 'Later'}, format='json'
        )
        self.assertEqual(edit.json()['error_code'], 'broadcast_not_draft')

    def test_send_through_resend_with_a_segment_and_broadcast_per_language(self):
        self.provider.provider = 'resend'
        self.provider.credentials_ciphertext = encrypt_json({'api_key': 're_test'}, setting='EMAIL_CREDENTIALS_KEY')
        self.provider.save()
        broadcast = self._create()
        self._translate(broadcast)
        requests_made = []

        def resend(method, url, json=None, headers=None, timeout=None):
            path = url.replace('https://api.resend.com', '')
            requests_made.append((method, path, json))
            response = mock.Mock(content=b'{}')
            response.status_code = 200
            if path == '/segments':
                response.json.return_value = {'id': f'seg_{len(requests_made)}'}
            elif path == '/contacts' and json['email'] == 'carl@acme.com':
                response.status_code = 422
                response.json.return_value = {'name': 'validation_error'}
            elif path == '/broadcasts':
                response.json.return_value = {'id': f'bc_{json["name"][-3:-1]}'}
            else:
                response.json.return_value = {'id': 'contact'}
            return response

        get, _calls = _identity(PEOPLE)
        with (
            mock.patch('apps.email.broadcasts.requests.get', side_effect=get),
            mock.patch('apps.providers.resend.requests.request', side_effect=resend),
        ):
            response = self.owner.post(f'/api/v1/broadcasts/{broadcast["id"]}/send')
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(response.json()['state'], 'sent')
        self.assertEqual(fake_provider().sent, [])
        segments = [call for call in requests_made if call[1] == '/segments']
        broadcasts = {call[2]['name']: call[2] for call in requests_made if call[1] == '/broadcasts'}
        self.assertEqual(len(segments), 2)
        self.assertEqual(set(broadcasts), {'October news (en)', 'October news (fr)'})
        french = broadcasts['October news (fr)']
        self.assertEqual(french['subject'], 'Bonjour {{{contact.first_name|toi}}}')
        self.assertEqual(french['from'], '"Acme" <news@news.acme.com>')
        self.assertTrue(french['send'])
        self.assertIn('{{{RESEND_UNSUBSCRIBE_URL}}}', french['html'])
        self.assertNotIn('{{ system.', french['html'])
        self.assertIn('https://mail.shellui.test/static/library/', french['html'])
        fallback = [call for call in requests_made if call[1].startswith('/contacts/carl@acme.com/segments/')]
        self.assertEqual(len(fallback), 1)
        contacts = {call[2]['email'] for call in requests_made if call[1] == '/contacts'}
        self.assertEqual(contacts, {'ada@acme.com', 'bea@acme.com', 'carl@acme.com'})

        saved = Broadcast.objects.get(pk=broadcast['id'])
        self.assertIn('bc_fr', saved.resend_lookup)
        self.assertTrue(apply_broadcast_event('email.delivered', {'broadcast_id': 'bc_fr', 'to': ['bea@acme.com']}))
        self.assertTrue(apply_broadcast_event('email.sent', {'broadcast_id': 'bc_fr', 'to': ['bea@acme.com']}))
        bea = saved.recipients.get(email_hmac=email_hmac('bea@acme.com'))
        self.assertEqual(bea.status, BroadcastRecipient.STATUS_DELIVERED)
        self.assertEqual(self.owner.get(f'/api/v1/broadcasts/{saved.pk}').json()['counts']['delivered'], 1)

        self.assertTrue(apply_contact_unsubscribe([COMPANY], {'email': 'ada@acme.com', 'unsubscribed': True}))
        self.assertTrue(
            Unsubscribe.objects.filter(company_id=COMPANY, email_hmac=email_hmac('ada@acme.com'), category='bulk').exists()
        )

    def test_a_bulk_sender_is_required(self):
        self.provider.bulk_from_email = ''
        self.provider.save()
        broadcast = self._create()
        self.assertEqual(broadcast['sender']['error_code'], 'bulk_sender_required')
        response = self.owner.post(f'/api/v1/broadcasts/{broadcast["id"]}/send')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error_code'], 'bulk_sender_required')

    def test_other_companies_cannot_see_a_broadcast(self):
        broadcast = self._create()
        other = _owner(51)
        self.assertEqual(other.get(f'/api/v1/broadcasts/{broadcast["id"]}').status_code, 403)
        self.assertEqual(other.post(f'/api/v1/broadcasts/{broadcast["id"]}/send').status_code, 403)
        self.assertEqual(other.get('/api/v1/broadcasts').json()['broadcasts'], [])

    def test_a_draft_can_be_deleted_with_its_content(self):
        broadcast = self._create()
        response = self.owner.delete(f'/api/v1/broadcasts/{broadcast["id"]}')
        self.assertEqual(response.status_code, 204)
        self.assertFalse(EmailTemplate.objects.filter(pk=broadcast['template_id']).exists())

    def test_resend_webhook_routes_broadcast_events(self):
        payload = json.dumps({'type': 'email.delivered', 'data': {'broadcast_id': 'bc_x', 'to': ['a@b.co']}})
        with mock.patch('apps.email.views.verify_svix', return_value=True), override_settings(RESEND_WEBHOOK_SECRET='whsec'):
            with mock.patch('apps.email.broadcasts.apply_broadcast_event') as routed:
                response = APIClient().post(
                    '/api/v1/provider-webhooks/resend/bulk', payload, content_type='application/json'
                )
        self.assertEqual(response.status_code, 200)
        routed.assert_called_once()
