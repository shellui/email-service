"""Per-company auth link hosts, and the sign-in link guardrails around them.

Auth mail in this service is the auth lane: magic link and invitation. A sign-in link
goes only to the address on the request. Company rules cannot add recipients. The link
and its token are not stored on the event log, delivery rows, or the API error.
"""

from __future__ import annotations

import json
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.actions.models import ActionOutbox, EventLog
from apps.authapi.principal import EmailPrincipal
from apps.email.keys import issue_service_key
from apps.email.crypto import encrypt_json
from apps.email.models import CompanyProfile, CompanyProvider, Message, MessageEvent
from apps.providers.registry import fake_provider

MAGIC = 'identity.auth.magic_link.requested'
INVITED = 'identity.user.invited'
APP = 'playground.shellui.com'
OTHER = 'app.other.test'
SECRET = 'sign-in-secret-token'
LINK = f'https://{APP}/verify?token={SECRET}'
JWT = 'Bearer header.payload.sig'


def _owner(company_id, *, user_id=3, staff=False, owner=True):
    client = APIClient()
    client.force_authenticate(
        user=EmailPrincipal(
            user_id=user_id,
            company_id=None if staff else company_id,
            email='owner@acme.com',
            is_staff=staff,
            is_company_owner=owner,
        )
    )
    return client


def _auth_document(href, label='Open playground'):
    return {
        'type': 'doc',
        'content': [
            {
                'type': 'button',
                'attrs': {'href': href},
                'content': [{'type': 'text', 'text': label}],
            },
            {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Or paste {{ magic_link_url }}'}]},
        ],
    }


def _identity_body(origins):
    return [{'id': index, 'base_url': origin, 'is_active': True, 'source': 'manual'} for index, origin in enumerate(origins, start=1)]


class _IdentityResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.content = json.dumps(body).encode()

    def json(self):
        return self._body


@override_settings(
    EMAIL_DELIVER_SYNC=True,
    EMAIL_FALLBACK_PROVIDER='fake',
    EMAIL_ALLOW_FAKE_PROVIDER=True,
    EMAIL_AUTH_LINK_HOSTS=['id.shellui.com'],
    EMAIL_PUBLIC_URL='https://mail.shellui.test',
    IDENTITY_SERVICE_URL='https://id.shellui.test',
    DEBUG=True,
)
class AuthLinkHostTests(TestCase):
    def setUp(self):
        cache.clear()
        fake_provider().sent.clear()
        self.owner = _owner(40)
        self.other = _owner(41, user_id=4)
        self.identity_key, _client = issue_service_key(
            service='identity',
            allowed_lanes=['auth', 'transactional'],
            allowed_template_prefixes=['identity.'],
        )

    def _identity(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.identity_key}')
        return client

    def _template_id(self, client, company_id, event_type=MAGIC):
        rules = client.get(f'/api/v1/rules?company_id={company_id}&service=identity').json()['rules']
        return next(row['template_id'] for row in rules if row['event_type'] == event_type and row['built_in'])

    def _publish(self, client, company_id, href, *, authorization=''):
        template_id = self._template_id(client, company_id)
        if authorization:
            client.credentials(HTTP_AUTHORIZATION=authorization)
        draft = client.post(
            f'/api/v1/templates/{template_id}/versions?company_id={company_id}',
            {'subject': 'Sign in', 'document': _auth_document(href)},
            format='json',
        )
        self.assertEqual(draft.status_code, 201, draft.content)
        return client.post(f'/api/v1/templates/{template_id}/versions/{draft.json()["number"]}/publish?company_id={company_id}')

    def _put_hosts(self, client, company_id, hosts):
        return client.put(
            f'/api/v1/auth-link-hosts?company_id={company_id}',
            {'hosts': hosts},
            format='json',
        )

    def _sync(self, company_id, body, status=200):
        self.owner.credentials(HTTP_AUTHORIZATION=JWT)
        with mock.patch('apps.email.link_hosts.requests.get', return_value=_IdentityResponse(status, body)) as get:
            response = self.owner.get(f'/api/v1/auth-link-hosts?company_id={company_id}')
        return response, get

    def test_company_host_is_allowed_and_another_company_is_refused(self):
        saved = self._put_hosts(self.owner, 40, [APP])
        self.assertEqual(saved.status_code, 200, saved.content)
        body = saved.json()
        self.assertEqual(body['hosts'], [APP])
        self.assertIn('id.shellui.com', body['platform_hosts'])
        self.assertEqual(body['auth_link_hosts'], sorted({APP, 'id.shellui.com'}))
        added = EventLog.objects.get(company_id=40, event_type='email.auth_link_host.added')
        self.assertEqual(added.data, {'host': APP})
        self.assertNotIn('http', json.dumps(added.data))

        published = self._publish(self.owner, 40, f'https://{APP}/home')
        self.assertEqual(published.status_code, 200, published.content)
        refused = self._publish(self.other, 41, f'https://{APP}/home')
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertEqual(refused.json()['error_code'], 'auth_link_host_not_allowed')
        self.assertEqual(refused.json()['field_errors']['allowed_link_hosts'], ['not_allowlisted'])
        self.assertNotIn(APP, self.other.get('/api/v1/auth-link-hosts?company_id=41').json()['auth_link_hosts'])

        again = self._put_hosts(self.owner, 40, [APP])
        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(EventLog.objects.filter(company_id=40, event_type='email.auth_link_host.added').count(), 1)
        cleared = self._put_hosts(self.owner, 40, [])
        self.assertEqual(cleared.status_code, 200, cleared.content)
        removed = EventLog.objects.get(company_id=40, event_type='email.auth_link_host.removed')
        self.assertEqual(removed.data, {'host': APP})

    def test_catalog_and_provider_use_the_company_list(self):
        self._put_hosts(self.owner, 40, [APP])
        catalog = self.owner.get('/api/v1/catalog?company_id=40')
        self.assertIn(APP, catalog.json()['auth_link_hosts'])
        self.assertIn('id.shellui.com', catalog.json()['auth_link_hosts'])
        other_catalog = self.other.get('/api/v1/catalog?company_id=41')
        self.assertNotIn(APP, other_catalog.json()['auth_link_hosts'])
        provider = self.owner.get('/api/v1/provider?company_id=40')
        self.assertIn(APP, provider.json()['auth_link_hosts'])
        service = APIClient()
        service.credentials(HTTP_AUTHORIZATION=f'Bearer {self.identity_key}')
        service_catalog = service.get('/api/v1/catalog?company_id=40')
        self.assertEqual(service_catalog.status_code, 200, service_catalog.content)
        self.assertNotIn(APP, service_catalog.json()['auth_link_hosts'])
        self.assertIn('id.shellui.com', service_catalog.json()['auth_link_hosts'])

    def test_application_domains_are_company_scoped_and_fail_closed(self):
        poisoned = [
            {'base_url': f'https://{APP}', 'is_active': True},
            {'base_url': f'https://evil.example/login?token={SECRET}', 'is_active': True},
            {'base_url': 'https://old.example', 'is_active': False},
            {'base_url': 'https://user:pass@sneaky.example', 'is_active': True},
            {'base_url': 'http://plain.example', 'is_active': True},
            {'base_url': 'https://*.wild.example', 'is_active': True},
            {'base_url': 'https://1.2.3.4', 'is_active': True},
            {'base_url': 'https://app.example:8443', 'is_active': True},
        ]
        response, get = self._sync(40, poisoned)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['application_hosts'], [APP])
        self.assertNotIn(SECRET, response.content.decode())
        self.assertNotIn(SECRET, json.dumps(CompanyProfile.objects.get(company_id=40).application_link_hosts))
        self.assertEqual(get.call_args.kwargs['params'], {'company_id': 40})
        self.assertFalse(get.call_args.kwargs['allow_redirects'])
        self.assertEqual(get.call_args.kwargs['headers']['Authorization'], JWT)

        self.owner.credentials(HTTP_AUTHORIZATION=JWT)
        with mock.patch(
            'apps.email.link_hosts.requests.get',
            return_value=_IdentityResponse(200, _identity_body([f'https://{APP}'])),
        ):
            published = self._publish(self.owner, 40, f'https://{APP}/home', authorization=JWT)
        self.assertEqual(published.status_code, 200, published.content)

        self.other.credentials(HTTP_AUTHORIZATION=JWT)
        with mock.patch(
            'apps.email.link_hosts.requests.get',
            return_value=_IdentityResponse(200, _identity_body([f'https://{OTHER}'])),
        ):
            refused = self._publish(self.other, 41, f'https://{APP}/home', authorization=JWT)
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertNotIn(SECRET, refused.content.decode())
        self.assertEqual(
            CompanyProfile.objects.get(company_id=41).application_link_hosts,
            [OTHER],
        )

        with mock.patch(
            'apps.email.link_hosts.requests.get',
            return_value=_IdentityResponse(503, {'error': 'down'}),
        ):
            kept = self.owner.get('/api/v1/auth-link-hosts?company_id=40', HTTP_AUTHORIZATION=JWT)
        self.assertIn(APP, kept.json()['application_hosts'])
        self.assertNotIn('down.example', kept.json()['auth_link_hosts'])

    def test_rejected_hostnames(self):
        cases = {
            'https://playground.shellui.com': 'scheme_not_allowed',
            'playground.shellui.com/login': 'path_not_allowed',
            'playground.shellui.com:443': 'port_not_allowed',
            '*.shellui.com': 'wildcard_not_allowed',
            '.shellui.com': 'wildcard_not_allowed',
            '1.2.3.4': 'ip_not_allowed',
            '127.0.0.1': 'ip_not_allowed',
            '::1': 'ip_not_allowed',
            '127.000.000.001': 'ip_not_allowed',
            '0x7f.0.0.1': 'ip_not_allowed',
            'not a host': 'path_not_allowed',
            '': 'invalid_hostname',
        }
        for host, code in cases.items():
            with self.subTest(host=host):
                refused = self._put_hosts(self.owner, 40, [host] if host != '' else [''])
                self.assertEqual(refused.status_code, 400, refused.content)
                self.assertEqual(refused.json()['field_errors']['hosts'], [code])
                self.assertNotIn(SECRET, refused.content.decode())
        too_many = self._put_hosts(self.owner, 40, [f'app{index}.example' for index in range(33)])
        self.assertEqual(too_many.json()['field_errors']['hosts'], ['too_many'])
        self.assertEqual(self._put_hosts(self.owner, 40, 'playground.shellui.com').status_code, 400)

    @override_settings(DEBUG=False)
    def test_localhost_is_refused_in_production(self):
        refused = self._put_hosts(self.owner, 40, ['localhost'])
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertEqual(refused.json()['field_errors']['hosts'], ['localhost_not_allowed'])
        self.assertNotIn('localhost', self.owner.get('/api/v1/catalog?company_id=40').json()['auth_link_hosts'])

    def test_localhost_hostname_is_allowed_when_debug_is_on(self):
        saved = self._put_hosts(self.owner, 40, ['localhost'])
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertIn('localhost', saved.json()['hosts'])

    def test_near_miss_hosts_and_ports_are_refused(self):
        self._put_hosts(self.owner, 40, [APP])
        for href in (
            f'https://{APP}.evil.example/login',
            f'https://not{APP}/login',
            f'https://{APP}:443/login',
            f'https://user@{APP}/login',
            f'https://evil.example/login?token={SECRET}',
        ):
            with self.subTest(href=href):
                refused = self._publish(self.owner, 40, href)
                self.assertEqual(refused.status_code, 400, refused.content)
                self.assertEqual(refused.json()['error_code'], 'auth_link_host_not_allowed')
                self.assertEqual(refused.json()['field_errors']['allowed_link_hosts'], ['not_allowlisted'])
                self.assertNotIn(SECRET, refused.content.decode())

    def test_sign_in_link_goes_only_to_the_requester_and_is_not_stored(self):
        self._put_hosts(self.owner, 40, [APP])
        published = self._publish(self.owner, 40, f'https://{APP}/home')
        self.assertEqual(published.status_code, 200, published.content)
        for event_type in (MAGIC, INVITED):
            with self.subTest(event_type=event_type):
                refused = self.owner.post(
                    '/api/v1/rules?company_id=40',
                    {
                        'event_type': event_type,
                        'recipient_mode': 'static',
                        'static_recipients': ['owner@acme.com'],
                        'content': {'library_id': self._template_id(self.owner, 40) and 1},
                    },
                    format='json',
                )
                # library_id above may be wrong; the event is refused before the copy is read
                # when the event is auth. Assert the code, then use a real library id if needed.
                if refused.status_code != 400:
                    from apps.email.models import LibraryTemplate

                    refused = self.owner.post(
                        '/api/v1/rules?company_id=40',
                        {
                            'event_type': event_type,
                            'recipient_mode': 'static',
                            'static_recipients': ['owner@acme.com'],
                            'content': {'library_id': LibraryTemplate.objects.get(key='barebone.activation').pk},
                        },
                        format='json',
                    )
                self.assertEqual(refused.status_code, 400, refused.content)
                self.assertEqual(refused.json()['error_code'], 'auth_event_rule_forbidden')

        # The company copy only goes out on the company's own provider, never on the Shellui fallback.
        CompanyProvider.objects.create(
            company_id=40,
            provider='fake',
            from_email='ops@acme.com',
            credentials_ciphertext=encrypt_json({'marker': 'company'}, setting='EMAIL_CREDENTIALS_KEY'),
            configured=True,
        )
        sent = self._identity().post(
            '/api/v1/send',
            {
                'company_id': 40,
                'template_key': MAGIC,
                'to': [{'email': 'ada@acme.com'}],
                'cc': [{'email': 'owner@acme.com'}],
                'variables': {'company_name': 'Acme', 'magic_link_url': LINK},
            },
            format='json',
        )
        self.assertEqual(sent.status_code, 202, sent.content)
        self.assertEqual(sent.json()['messages'][0]['status'], 'sent')
        self.assertNotIn(SECRET, sent.content.decode())
        self.assertEqual([message.to_email for message in fake_provider().sent], ['ada@acme.com'])
        delivered = fake_provider().sent[-1]
        self.assertIn(SECRET, delivered.html)
        self.assertIn('Open playground', delivered.html)
        self.assertIn(f'https://{APP}/home', delivered.html)

        many = self._identity().post(
            '/api/v1/send',
            {
                'company_id': 40,
                'template_key': MAGIC,
                'to': [{'email': 'ada@acme.com'}, {'email': 'owner@acme.com'}],
                'variables': {'company_name': 'Acme', 'magic_link_url': LINK},
            },
            format='json',
        )
        self.assertEqual(many.status_code, 400, many.content)
        self.assertEqual(many.json()['error_code'], 'auth_single_recipient')
        self.assertNotIn(SECRET, many.content.decode())

        denied = self._identity().post(
            '/api/v1/send',
            {
                'company_id': 41,
                'template_key': MAGIC,
                'to': [{'email': 'bea@acme.com'}],
                'variables': {'company_name': 'Acme', 'magic_link_url': LINK},
            },
            format='json',
        )
        self.assertEqual(denied.status_code, 202, denied.content)
        self.assertEqual(denied.json()['messages'][0]['status'], 'failed')
        self.assertNotIn(SECRET, denied.content.decode())
        row = Message.objects.get(to_email='bea@acme.com')
        self.assertEqual(row.last_error_code, 'variable_url_not_allowed')
        self.assertEqual(row.variables_ciphertext, '')
        self.assertNotIn(SECRET, json.dumps(list(MessageEvent.objects.filter(message=row).values_list('detail', flat=True))))
        self.assertFalse(any(message.to_email == 'bea@acme.com' for message in fake_provider().sent))

        stored = json.dumps(
            {
                'events': list(EventLog.objects.values('event_type', 'data')),
                'outbox': list(ActionOutbox.objects.values('event_type', 'envelope', 'last_error')),
                'messages': list(Message.objects.values('last_error_code', 'variables_ciphertext', 'to_email')),
            },
            default=str,
        )
        self.assertNotIn(SECRET, stored)
        self.assertNotIn(LINK, stored)

        from config.sentry_scrub import before_send

        cleaned = before_send({'request': {'url': LINK, 'data': {'magic_link_url': LINK}, 'query_string': f'token={SECRET}'}})
        rendered = json.dumps(cleaned)
        self.assertNotIn(SECRET, rendered)
        self.assertNotIn(LINK, rendered)

    def test_permissions(self):
        member = _owner(40, owner=False, staff=False)
        refused = member.put('/api/v1/auth-link-hosts?company_id=40', {'hosts': [APP]}, format='json')
        self.assertEqual(refused.status_code, 403, refused.content)
        service = self._identity()
        self.assertEqual(service.get('/api/v1/auth-link-hosts?company_id=40').status_code, 401)
        staff = _owner(1, user_id=9, staff=True, owner=False)
        saved = self._put_hosts(staff, 40, [OTHER])
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(EventLog.objects.get(event_type='email.auth_link_host.added').user_id, 9)
        mismatch = self.owner.get('/api/v1/auth-link-hosts?company_id=41')
        self.assertEqual(mismatch.status_code, 403, mismatch.content)


@override_settings(DEBUG=True)
class AuthLinkHostAdminTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username='operator', email='ops@shellui.com', password='long-enough-passphrase')
        self.profile = CompanyProfile.objects.create(company_id=40, name='Acme', auth_link_hosts=[])
        self.client.force_login(self.user)

    def _post(self, hosts):
        url = reverse('admin:email_companyprofile_change', args=[self.profile.pk])
        return self.client.post(
            url,
            {'company_id': 40, 'name': 'Acme', 'auth_link_hosts': json.dumps(hosts)},
        )

    def test_admin_rejects_a_wildcard_and_audits_a_real_host(self):
        refused = self._post(['*.shellui.com'])
        self.assertEqual(refused.status_code, 200, refused.content)
        self.assertContains(refused, 'Wildcards are not allowed.')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.auth_link_hosts, [])

        saved = self._post([APP])
        self.assertEqual(saved.status_code, 302, saved.content)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.auth_link_hosts, [APP])
        row = EventLog.objects.get(company_id=40, event_type='email.auth_link_host.added')
        self.assertEqual(row.data, {'host': APP})
        self.assertNotIn('http', json.dumps(row.data))
