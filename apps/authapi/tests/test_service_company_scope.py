"""Empty service-key company lists deny every company. None stays unrestricted."""

from __future__ import annotations

import importlib

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.authapi.service_auth import authenticate_service_key, hash_service_key
from apps.email.admin import ServiceClientAdminForm
from apps.email.keys import issue_service_key
from apps.email.models import ServiceClient

_company_scope = importlib.import_module('apps.email.migrations.0013_service_key_empty_company_list')


def _client_row(raw: str, allowed):
    return ServiceClient.objects.create(
        service='hosting',
        name='hosting',
        key_prefix=raw[:12],
        key_hash=hash_service_key(raw),
        allowed_lanes=['transactional'],
        allowed_template_prefixes=['hosting.'],
        allowed_company_ids=allowed,
    )


@override_settings(
    EMAIL_DELIVER_SYNC=True,
    EMAIL_FALLBACK_PROVIDER='fake',
    EMAIL_ALLOW_FAKE_PROVIDER=True,
    DEBUG=True,
)
class ServiceKeyCompanyScopeTests(TestCase):
    def test_unset_company_list_allows_every_company(self):
        raw, row = issue_service_key(
            service='identity',
            allowed_lanes=['auth'],
            allowed_template_prefixes=['identity.'],
        )
        self.assertIsNone(row.allowed_company_ids)
        principal = authenticate_service_key(raw)
        self.assertIsNone(principal.allowed_company_ids)
        self.assertTrue(principal.allows_company(1))
        self.assertTrue(principal.allows_company(99))

    def test_empty_company_list_denies_every_company(self):
        raw, row = issue_service_key(
            service='hosting',
            allowed_lanes=['transactional'],
            allowed_template_prefixes=['hosting.'],
            allowed_company_ids=[],
        )
        self.assertEqual(row.allowed_company_ids, [])
        principal = authenticate_service_key(raw)
        self.assertEqual(principal.allowed_company_ids, ())
        self.assertFalse(principal.allows_company(1))
        self.assertFalse(principal.allows_company(42))

    def test_listed_company_is_the_only_match(self):
        raw, _row = issue_service_key(
            service='hosting',
            allowed_lanes=['transactional'],
            allowed_template_prefixes=['hosting.'],
            allowed_company_ids=[42],
        )
        principal = authenticate_service_key(raw)
        self.assertTrue(principal.allows_company(42))
        self.assertFalse(principal.allows_company(7))

    def test_send_with_an_unrestricted_key_still_queues_magic_link_mail(self):
        raw, _row = issue_service_key(
            service='identity',
            allowed_lanes=['auth', 'transactional'],
            allowed_template_prefixes=['identity.'],
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {raw}')
        sent = client.post(
            '/api/v1/send',
            {
                'company_id': 42,
                'template_key': 'identity.auth.magic_link.requested',
                'to': [{'email': 'ada@acme.com'}],
                'variables': {
                    'company_name': 'Acme',
                    'magic_link_url': 'https://id.shellui.com/api/v1/magic-link/verify?token=example',
                },
            },
            format='json',
        )
        self.assertEqual(sent.status_code, 202, sent.content)
        self.assertEqual(sent.json()['messages'][0]['status'], 'sent')

    def test_send_with_an_empty_company_list_is_company_mismatch(self):
        raw, _row = issue_service_key(
            service='identity',
            allowed_lanes=['auth'],
            allowed_template_prefixes=['identity.'],
            allowed_company_ids=[],
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {raw}')
        refused = client.post(
            '/api/v1/send',
            {
                'company_id': 42,
                'template_key': 'identity.auth.magic_link.requested',
                'to': [{'email': 'ada@acme.com'}],
                'variables': {
                    'company_name': 'Acme',
                    'magic_link_url': 'https://id.shellui.com/api/v1/magic-link/verify?token=example',
                },
            },
            format='json',
        )
        self.assertEqual(refused.status_code, 403, refused.content)
        self.assertEqual(refused.json()['error_code'], 'company_mismatch')

    def test_api_omits_companies_and_an_empty_list_is_stored_as_given(self):
        staff = APIClient()
        staff.force_authenticate(user=_staff_principal())
        omitted = staff.post('/api/v1/service-clients', {'service': 'storage'}, format='json')
        self.assertEqual(omitted.status_code, 201, omitted.content)
        self.assertIsNone(ServiceClient.objects.get(pk=omitted.json()['id']).allowed_company_ids)
        empty = staff.post(
            '/api/v1/service-clients',
            {'service': 'storage', 'allowed_company_ids': []},
            format='json',
        )
        self.assertEqual(empty.status_code, 201, empty.content)
        self.assertEqual(ServiceClient.objects.get(pk=empty.json()['id']).allowed_company_ids, [])

    def test_migration_turns_stored_empty_lists_into_null(self):
        empty = _client_row('esk_empty_list_key_for_migration', [])
        scoped = _client_row('esk_scoped_list_key_for_migration', [42])
        unset = _client_row('esk_unset_list_key_for_migration', None)
        _company_scope.empty_company_lists_to_null(django_apps, None)
        empty.refresh_from_db()
        scoped.refresh_from_db()
        unset.refresh_from_db()
        self.assertIsNone(empty.allowed_company_ids)
        self.assertEqual(scoped.allowed_company_ids, [42])
        self.assertIsNone(unset.allowed_company_ids)
        principal = authenticate_service_key('esk_empty_list_key_for_migration')
        self.assertIsNone(principal.allowed_company_ids)
        self.assertTrue(principal.allows_company(3))

    def test_admin_new_key_defaults_to_an_empty_list(self):
        form = ServiceClientAdminForm()
        self.assertEqual(form.initial['allowed_company_ids'], [])
        bound = ServiceClientAdminForm(
            data={
                'service': 'hosting',
                'name': 'hosting',
                'key_prefix': 'esk_admin_new',
                'key_hash': hash_service_key('esk_admin_new'),
                'allowed_lanes': '["transactional"]',
                'allowed_template_prefixes': '["hosting."]',
                'allowed_company_ids': '',
                'callback_url': '',
                'callback_secret_ciphertext': '',
                'active': 'on',
            }
        )
        self.assertTrue(bound.is_valid(), bound.errors)
        self.assertEqual(bound.cleaned_data['allowed_company_ids'], [])
        saved = bound.save()
        self.assertEqual(saved.allowed_company_ids, [])
        self.assertFalse(authenticate_service_key('esk_admin_new').allows_company(1))

    def test_admin_null_stays_unrestricted_and_blank_denies(self):
        row = _client_row('esk_admin_null_key_value', None)
        unbound = ServiceClientAdminForm(instance=row)
        self.assertIsNone(unbound.initial['allowed_company_ids'])
        kept = ServiceClientAdminForm(
            data=_admin_data(row, 'null'),
            instance=row,
        )
        self.assertTrue(kept.is_valid(), kept.errors)
        self.assertIsNone(kept.cleaned_data['allowed_company_ids'])
        kept.save()
        row.refresh_from_db()
        self.assertIsNone(row.allowed_company_ids)
        cleared = ServiceClientAdminForm(
            data=_admin_data(row, ''),
            instance=row,
        )
        self.assertTrue(cleared.is_valid(), cleared.errors)
        self.assertEqual(cleared.cleaned_data['allowed_company_ids'], [])

    def test_admin_add_page_shows_an_empty_list(self):
        User = get_user_model()
        user = User.objects.create_superuser('admin', 'admin@example.com', 'long-enough-passphrase')
        client = APIClient()
        client.force_login(user)
        page = client.get('/admin/email/serviceclient/add/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'An empty list allows no company.')
        self.assertContains(page, 'null allows every company.')
        self.assertRegex(page.content, br'id="id_allowed_company_ids">\s*\[\]</textarea>')


def _staff_principal():
    from apps.authapi.principal import EmailPrincipal

    return EmailPrincipal(user_id=1, company_id=None, email='staff@example.com', is_staff=True)


def _admin_data(row: ServiceClient, companies: str) -> dict:
    return {
        'service': row.service,
        'name': row.name,
        'key_prefix': row.key_prefix,
        'key_hash': row.key_hash,
        'allowed_lanes': '["transactional"]',
        'allowed_template_prefixes': '["hosting."]',
        'allowed_company_ids': companies,
        'callback_url': '',
        'callback_secret_ciphertext': '',
        'active': 'on',
    }
