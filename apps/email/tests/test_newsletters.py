"""Newsletters: public double opt-in sign-up, per-list unsubscribe, admin API, and sending to a list."""

from __future__ import annotations

import re
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.actions.models import EventLog
from apps.authapi.principal import EmailPrincipal
from apps.email import newsletters
from apps.email.broadcasts import apply_contact_unsubscribe
from apps.email.crypto import email_hmac, encrypt_json
from apps.email.models import (
    CompanyProvider,
    EmailTemplate,
    NewsletterSubscriber,
    Suppression,
    Unsubscribe,
)
from apps.email.unsubscribe import unsubscribe_token
from apps.providers.registry import fake_provider

COMPANY = 60
CONFIRM_RE = re.compile(r'/n/confirm/([A-Za-z0-9_-]+)')


def _owner(company_id=COMPANY):
    client = APIClient()
    client.force_authenticate(
        user=EmailPrincipal(user_id=7, company_id=company_id, email='owner@acme.com', is_company_owner=True)
    )
    client.credentials(HTTP_AUTHORIZATION='Bearer owner-jwt')
    return client


@override_settings(
    EMAIL_DELIVER_SYNC=True,
    EMAIL_ALLOW_FAKE_PROVIDER=True,
    PUBLIC_BASE_URL='https://mail.shellui.test',
    EMAIL_PUBLIC_URL='https://mail.shellui.test',
    DEBUG=True,
)
class NewsletterTests(TestCase):
    def setUp(self):
        cache.clear()
        fake_provider().sent.clear()
        self.owner = _owner()
        self.public = APIClient()
        self.provider = CompanyProvider.objects.create(
            company_id=COMPANY,
            provider='fake',
            from_email='hello@acme.com',
            bulk_from_email='news@news.acme.com',
            from_name='Acme',
            credentials_ciphertext=encrypt_json({'marker': 'company'}, setting='EMAIL_CREDENTIALS_KEY'),
            configured=True,
        )

    def _create(self, **extra):
        response = self.owner.post('/api/v1/newsletters', {'name': 'Product news', **extra}, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def _subscribe(self, newsletter, email='ada@example.org', **extra):
        headers = extra.pop('headers', {})
        return self.public.post(
            f'/api/v1/public/newsletters/{newsletter["public_key"]}/subscribe',
            {'email': email, **extra},
            format='json',
            **headers,
        )

    def _token(self, email='ada@example.org'):
        messages = [message for message in fake_provider().sent if message.to_email == email]
        self.assertTrue(messages, f'no confirmation sent to {email}')
        match = CONFIRM_RE.search(messages[-1].html)
        self.assertIsNotNone(match)
        return match.group(1)

    def _confirmed(self, newsletter, email):
        self.assertEqual(self._subscribe(newsletter, email).status_code, 202)
        self.assertEqual(self.public.post(f'/n/confirm/{self._token(email)}').status_code, 200)

    def test_a_new_list_has_its_own_confirmation_email(self):
        newsletter = self._create(allowed_origins=['https://shellui.com/'])
        self.assertTrue(newsletter['public_key'].startswith('nl_'))
        self.assertEqual(newsletter['allowed_origins'], ['https://shellui.com'])
        self.assertEqual(newsletter['counts'], {'pending': 0, 'confirmed': 0, 'unsubscribed': 0})
        self.assertEqual(newsletter['sender'], {'from_email': 'hello@acme.com', 'error_code': ''})
        self.assertEqual(
            newsletter['subscribe_url'],
            f'https://mail.shellui.test/api/v1/public/newsletters/{newsletter["public_key"]}/subscribe',
        )
        template = EmailTemplate.objects.get(pk=newsletter['confirmation_template_id'])
        self.assertEqual(template.kind, EmailTemplate.KIND_NEWSLETTER_CONFIRMATION)
        self.assertTrue(template.active_version)
        listed = self.owner.get('/api/v1/templates').json()['templates']
        self.assertNotIn(template.pk, [row['id'] for row in listed])

    def test_sign_up_sends_a_confirmation_in_the_visitor_language(self):
        newsletter = self._create()
        response = self._subscribe(newsletter, headers={'HTTP_ACCEPT_LANGUAGE': 'fr-FR,fr;q=0.9'})
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(response.json(), {'status': 'pending'})
        [message] = fake_provider().sent
        self.assertEqual(message.subject, 'Confirmez votre inscription à Product news')
        self.assertEqual(message.from_email, 'hello@acme.com')
        self.assertIn('https://mail.shellui.test/n/confirm/', message.html)
        self.assertIn("Confirmer l'inscription", message.text)
        self.assertIn('Merci de vous être inscrit à Product news.', message.text)
        self.assertNotIn('unsubscribe', message.html.lower())
        row = NewsletterSubscriber.objects.get()
        self.assertEqual((row.status, row.language, row.source), ('pending', 'fr', 'form'))

    def test_the_answer_does_not_tell_who_is_subscribed(self):
        newsletter = self._create()
        self._confirmed(newsletter, 'ada@example.org')
        sent = len(fake_provider().sent)
        again = self._subscribe(newsletter, 'ada@example.org')
        self.assertEqual((again.status_code, again.json()), (202, {'status': 'pending'}))
        self.assertEqual(len(fake_provider().sent), sent)

    def test_the_honeypot_answers_like_a_sign_up(self):
        newsletter = self._create()
        response = self._subscribe(newsletter, website='https://spam.example')
        self.assertEqual(response.status_code, 202)
        self.assertFalse(NewsletterSubscriber.objects.exists())
        self.assertEqual(fake_provider().sent, [])

    @override_settings(EMAIL_NEWSLETTER_IP_PER_HOUR=2)
    def test_sign_ups_are_rate_limited_per_ip(self):
        newsletter = self._create()
        for index in range(2):
            self.assertEqual(self._subscribe(newsletter, f'p{index}@example.org').status_code, 202)
        response = self._subscribe(newsletter, 'p9@example.org')
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()['error_code'], 'rate_limited')

    def test_a_pending_address_is_not_mailed_twice_in_a_row(self):
        newsletter = self._create()
        self._subscribe(newsletter)
        self._subscribe(newsletter)
        self.assertEqual(len(fake_provider().sent), 1)

    @override_settings(CORS_ALLOW_ALL_ORIGINS=False)
    def test_allowed_origins_and_cors(self):
        newsletter = self._create(allowed_origins=['https://shellui.com'])
        url = f'/api/v1/public/newsletters/{newsletter["public_key"]}/subscribe'
        preflight = self.public.options(
            url,
            HTTP_ORIGIN='https://shellui.com',
            HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST',
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS='content-type',
        )
        self.assertEqual(preflight['Access-Control-Allow-Origin'], 'https://shellui.com')
        allowed = self._subscribe(newsletter, headers={'HTTP_ORIGIN': 'https://shellui.com'})
        self.assertEqual(allowed.status_code, 202)
        self.assertEqual(allowed['Access-Control-Allow-Origin'], 'https://shellui.com')
        blocked = self._subscribe(newsletter, 'bea@example.org', headers={'HTTP_ORIGIN': 'https://evil.example'})
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.json()['error_code'], 'origin_not_allowed')
        self.assertNotIn('Access-Control-Allow-Origin', blocked)
        missing = self.public.post('/api/v1/public/newsletters/nl_nope/subscribe', {'email': 'a@b.co'}, format='json')
        self.assertEqual(missing.status_code, 404)

    def test_turnstile_is_checked_when_configured(self):
        newsletter = self._create()
        self.owner.patch(
            f'/api/v1/newsletters/{newsletter["id"]}',
            {'turnstile_site_key': '0x4AAA', 'turnstile_secret': 'secret'},
            format='json',
        )
        detail = self.owner.get(f'/api/v1/newsletters/{newsletter["id"]}').json()
        self.assertTrue(detail['turnstile_configured'])
        self.assertNotIn('turnstile_secret', detail)
        self.assertEqual(self._subscribe(newsletter).json()['error_code'], 'turnstile_failed')
        verdict = mock.Mock(status_code=200)
        verdict.json.return_value = {'success': True}
        with mock.patch('apps.email.newsletters.requests.post', return_value=verdict) as post:
            response = self._subscribe(newsletter, turnstile_token='tok')
        self.assertEqual(response.status_code, 202)
        self.assertEqual(post.call_args.kwargs['data']['secret'], 'secret')

    def test_opening_the_link_does_not_confirm_but_the_button_does(self):
        newsletter = self._create()
        self._subscribe(newsletter)
        token = self._token()
        page = self.public.get(f'/n/confirm/{token}')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Confirm subscription')
        self.assertEqual(NewsletterSubscriber.objects.get().status, 'pending')
        done = self.public.post(f'/n/confirm/{token}')
        self.assertContains(done, 'You are subscribed')
        row = NewsletterSubscriber.objects.get()
        self.assertEqual(row.status, 'confirmed')
        self.assertIsNotNone(row.confirmed_at)
        self.assertTrue(EventLog.objects.filter(event_type='email.newsletter.confirmed').exists())
        self.assertEqual(self.public.get('/n/confirm/not-a-token').status_code, 404)

    def test_confirmation_redirects_to_the_website(self):
        newsletter = self._create(confirmed_redirect_url='https://shellui.com/newsletter/confirmed/')
        self._subscribe(newsletter)
        response = self.public.post(f'/n/confirm/{self._token()}')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://shellui.com/newsletter/confirmed/')

    @override_settings(EMAIL_NEWSLETTER_CONFIRM_HOURS=1)
    def test_confirmation_links_expire(self):
        newsletter = self._create()
        self._subscribe(newsletter)
        token = self._token()
        NewsletterSubscriber.objects.update(confirm_sent_at=timezone.now() - timedelta(hours=2))
        self.assertEqual(self.public.post(f'/n/confirm/{token}').status_code, 404)
        self.assertEqual(NewsletterSubscriber.objects.get().status, 'pending')

    def test_unsubscribe_link_leaves_one_list_only(self):
        first = self._create()
        second = self._create(name='Changelog')
        self._confirmed(first, 'ada@example.org')
        self._confirmed(second, 'ada@example.org')
        token = unsubscribe_token(COMPANY, 'ada@example.org', newsletters.unsubscribe_category(first['id']))
        page = self.public.get(f'/u/{token}')
        self.assertContains(page, 'Stop receiving Product news.')
        self.assertEqual(self.public.post(f'/u/{token}').status_code, 200)
        statuses = dict(NewsletterSubscriber.objects.values_list('list_id', 'status'))
        self.assertEqual(statuses, {first['id']: 'unsubscribed', second['id']: 'confirmed'})
        self.assertFalse(Unsubscribe.objects.exists())
        self.assertTrue(EventLog.objects.filter(event_type='email.newsletter.unsubscribed').exists())
        foreign = unsubscribe_token(COMPANY + 1, 'ada@example.org', newsletters.unsubscribe_category(second['id']))
        self.assertEqual(self.public.post(f'/u/{foreign}').status_code, 404)

    def test_company_wide_unsubscribes_reach_every_list(self):
        newsletter = self._create()
        self._confirmed(newsletter, 'ada@example.org')
        self._confirmed(newsletter, 'bea@example.org')
        self.public.post(f'/u/{unsubscribe_token(COMPANY, "ada@example.org", "bulk")}')
        self.assertTrue(apply_contact_unsubscribe([COMPANY], {'email': 'bea@example.org', 'unsubscribed': True}))
        self.assertEqual(
            set(NewsletterSubscriber.objects.values_list('status', flat=True)),
            {NewsletterSubscriber.STATUS_UNSUBSCRIBED},
        )

    def test_signing_up_again_after_leaving_needs_a_new_confirmation(self):
        newsletter = self._create()
        self._confirmed(newsletter, 'ada@example.org')
        row = NewsletterSubscriber.objects.get()
        newsletters.unsubscribe_subscriber(row, source='test')
        self._subscribe(newsletter)
        row.refresh_from_db()
        self.assertEqual(row.status, 'pending')

    def test_send_a_broadcast_to_confirmed_subscribers(self):
        newsletter = self._create()
        self._confirmed(newsletter, 'ada@example.org')
        self._confirmed(newsletter, 'bea@example.org')
        self._subscribe(newsletter, 'pending@example.org')
        Unsubscribe.objects.create(
            company_id=COMPANY, email_hmac=email_hmac('bea@example.org'), category='bulk', source='link'
        )
        fake_provider().sent.clear()
        audience = {'mode': 'newsletter', 'list_id': newsletter['id']}
        created = self.owner.post(
            '/api/v1/broadcasts',
            {'name': 'Issue 1', 'source_key': 'barebone.product-update', 'audience': audience},
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.content)
        broadcast = created.json()
        self.assertEqual(broadcast['audience']['list_id'], newsletter['id'])
        with mock.patch('apps.email.broadcasts.requests.get') as identity:
            preview = self.owner.post(f'/api/v1/broadcasts/{broadcast["id"]}/preview', {}, format='json').json()
            sent = self.owner.post(f'/api/v1/broadcasts/{broadcast["id"]}/send')
        identity.assert_not_called()
        self.assertEqual((preview['total'], preview['sendable']), (2, 2))
        self.assertEqual(sent.status_code, 202, sent.content)
        messages = {message.to_email: message for message in fake_provider().sent}
        self.assertEqual(set(messages), {'ada@example.org', 'bea@example.org'})
        link = messages['ada@example.org'].headers['List-Unsubscribe']
        token = re.search(r'/u/([^>]+)>', link).group(1)
        self.assertIn(f'.list-{newsletter["id"]}.', token)

    def test_a_broadcast_cannot_use_another_company_list(self):
        foreign = newsletters.create_list(COMPANY + 1, {'name': 'Theirs'}, None)
        response = self.owner.post(
            '/api/v1/broadcasts',
            {
                'name': 'Nope',
                'source_key': 'barebone.product-update',
                'audience': {'mode': 'newsletter', 'list_id': foreign.pk},
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('audience.list_id', response.json()['field_errors'])

    def test_admin_adds_imports_and_exports_subscribers(self):
        newsletter = self._create()
        base = f'/api/v1/newsletters/{newsletter["id"]}'
        added = self.owner.post(f'{base}/subscribers', {'email': 'ada@example.org', 'mode': 'consented'}, format='json')
        self.assertEqual(added.status_code, 201, added.content)
        self.assertEqual(added.json()['outcome'], 'added')
        self.assertEqual(added.json()['subscriber']['status'], 'confirmed')
        invited = self.owner.post(f'{base}/subscribers', {'email': 'bea@example.org'}, format='json')
        self.assertEqual(invited.json()['outcome'], 'sent')
        Suppression.objects.create(
            company_id=COMPANY,
            email_hmac=email_hmac('bounced@example.org'),
            email_masked='b***@example.org',
            reason=Suppression.REASON_HARD_BOUNCE,
            lanes=[],
        )
        csv_text = 'email,first_name,language\ncarl@example.org,=cmd(),fr\nnope\nada@example.org,,\nbounced@example.org,,\n'
        imported = self.owner.post(f'{base}/subscribers/import', {'csv': csv_text, 'mode': 'consented'}, format='json')
        self.assertEqual(
            imported.json(),
            {'added': 1, 'existing': 1, 'invalid': 1, 'skipped_unsubscribed': 0, 'skipped_suppressed': 1},
        )
        page = self.owner.get(f'{base}/subscribers?status=confirmed').json()
        self.assertEqual(page['count'], 2)
        export = self.owner.get(f'{base}/subscribers.csv')
        self.assertEqual(export.status_code, 200)
        self.assertEqual(export['Content-Type'], 'text/csv; charset=utf-8')
        body = export.content.decode()
        self.assertIn('carl@example.org,\'=cmd(),fr,confirmed,import', body)
        sid = page['results'][0]['id']
        self.assertEqual(self.owner.delete(f'{base}/subscribers/{sid}').status_code, 204)
        self.assertEqual(self.owner.delete(f'{base}/subscribers/{sid}').status_code, 404)

    def test_consented_add_respects_an_unsubscribe(self):
        newsletter = self._create()
        self._confirmed(newsletter, 'ada@example.org')
        newsletters.unsubscribe_subscriber(NewsletterSubscriber.objects.get(), source='test')
        response = self.owner.post(
            f'/api/v1/newsletters/{newsletter["id"]}/subscribers',
            {'email': 'ada@example.org', 'mode': 'consented'},
            format='json',
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error_code'], 'subscriber_unsubscribed')

    def test_rotating_the_key_retires_the_old_form(self):
        newsletter = self._create()
        rotated = self.owner.post(f'/api/v1/newsletters/{newsletter["id"]}/rotate-key').json()
        self.assertNotEqual(rotated['public_key'], newsletter['public_key'])
        self.assertEqual(self._subscribe(newsletter).status_code, 404)
        self.assertEqual(self._subscribe(rotated).status_code, 202)

    def test_other_companies_cannot_see_a_list(self):
        newsletter = self._create()
        other = _owner(COMPANY + 1)
        self.assertEqual(other.get(f'/api/v1/newsletters/{newsletter["id"]}').status_code, 403)
        self.assertEqual(other.get(f'/api/v1/newsletters/{newsletter["id"]}/subscribers.csv').status_code, 403)
        self.assertEqual(other.get('/api/v1/newsletters').json()['newsletters'], [])
        self.assertEqual(APIClient().get('/api/v1/newsletters').status_code, 401)

    def test_deleting_a_list_removes_its_confirmation_email(self):
        newsletter = self._create()
        self._subscribe(newsletter)
        self.assertEqual(self.owner.delete(f'/api/v1/newsletters/{newsletter["id"]}').status_code, 204)
        self.assertFalse(EmailTemplate.objects.filter(pk=newsletter['confirmation_template_id']).exists())
        self.assertFalse(NewsletterSubscriber.objects.exists())

    def test_without_a_sender_the_form_is_unavailable(self):
        self.provider.delete()
        newsletter = self._create()
        self.assertNotEqual(newsletter['sender']['error_code'], '')
        response = self._subscribe(newsletter)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error_code'], 'newsletter_unavailable')

    @override_settings(EMAIL_NEWSLETTER_PENDING_DAYS=7)
    def test_purge_drops_stale_sign_ups_and_forgets_people_who_left(self):
        newsletter = self._create()
        self._subscribe(newsletter, 'stale@example.org')
        self._confirmed(newsletter, 'left@example.org')
        newsletters.unsubscribe_subscriber(
            NewsletterSubscriber.objects.get(email_hmac=email_hmac('left@example.org')), source='test'
        )
        old = timezone.now() - timedelta(days=8)
        NewsletterSubscriber.objects.filter(email_hmac=email_hmac('stale@example.org')).update(confirm_sent_at=old)
        NewsletterSubscriber.objects.filter(email_hmac=email_hmac('left@example.org')).update(unsubscribed_at=old)
        call_command('purge_expired_data', stdout=mock.Mock())
        self.assertFalse(NewsletterSubscriber.objects.filter(email_hmac=email_hmac('stale@example.org')).exists())
        left = NewsletterSubscriber.objects.get(email_hmac=email_hmac('left@example.org'))
        self.assertEqual(left.email_ciphertext, '')
        self.assertEqual(left.status, 'unsubscribed')

    def test_privacy_erase_removes_subscribers(self):
        newsletter = self._create()
        self._subscribe(newsletter)
        staff = APIClient()
        staff.force_authenticate(user=EmailPrincipal(user_id=1, company_id=None, email='s@shellui.com', is_staff=True))
        response = staff.post(
            '/api/v1/privacy/erase', {'company_id': COMPANY, 'email': 'ada@example.org'}, format='json'
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(NewsletterSubscriber.objects.exists())
