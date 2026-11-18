"""register_dev_service_keys seeds dev keys only, idempotently, storing only the hash."""

from __future__ import annotations

from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.authapi.service_auth import authenticate_service_key, hash_service_key
from apps.email.models import ServiceClient

IDENTITY_KEY = 'esk_' + 'i' * 43  # gitleaks:allow
STORAGE_KEY = 'esk_' + 's' * 43  # gitleaks:allow


def run(value: str) -> str:
    out = StringIO()
    with mock.patch.dict('os.environ', {'EMAIL_DEV_SERVICE_KEYS': value}):
        call_command('register_dev_service_keys', stdout=out)
    return out.getvalue()


@override_settings(DEBUG=True)
class RegisterDevServiceKeysTests(TestCase):
    def test_registers_hash_only_and_is_idempotent(self):
        output = run(f'identity={IDENTITY_KEY}, storage={STORAGE_KEY}')
        output += run(f'identity={IDENTITY_KEY},storage={STORAGE_KEY}')
        self.assertEqual(ServiceClient.objects.count(), 2)
        row = ServiceClient.objects.get(service='identity')
        self.assertEqual(row.key_hash, hash_service_key(IDENTITY_KEY))
        self.assertEqual(row.allowed_template_prefixes, ['identity.'])
        self.assertIsNone(row.allowed_company_ids)
        self.assertEqual(authenticate_service_key(STORAGE_KEY).service, 'storage')
        self.assertNotIn(IDENTITY_KEY, output)
        self.assertIn('identity already registered', output)

    def test_only_identity_gets_the_auth_lane(self):
        run(f'identity={IDENTITY_KEY},storage={STORAGE_KEY}')
        self.assertEqual(ServiceClient.objects.get(service='identity').allowed_lanes, ['auth', 'transactional'])
        self.assertEqual(ServiceClient.objects.get(service='storage').allowed_lanes, ['transactional'])

    def test_rerun_fixes_lanes_of_an_existing_dev_key(self):
        run(f'identity={IDENTITY_KEY}')
        ServiceClient.objects.filter(service='identity').update(allowed_lanes=['transactional'])
        self.assertIn('identity lanes updated', run(f'identity={IDENTITY_KEY}'))
        self.assertEqual(ServiceClient.objects.get(service='identity').allowed_lanes, ['auth', 'transactional'])

    def test_key_owned_by_another_service_is_refused(self):
        run(f'identity={IDENTITY_KEY}')
        with self.assertRaises(CommandError):
            run(f'storage={IDENTITY_KEY}')
        self.assertEqual(ServiceClient.objects.count(), 1)

    def test_invalid_entry_is_refused_without_echoing_it(self):
        for value in ('identity', 'identity=not-a-key', f'={IDENTITY_KEY}', 'identity=esk_short'):
            with self.assertRaises(CommandError) as ctx:
                run(value)
            self.assertNotIn('not-a-key', str(ctx.exception))
            self.assertNotIn(IDENTITY_KEY, str(ctx.exception))
        self.assertFalse(ServiceClient.objects.exists())

    @override_settings(DEBUG=False)
    def test_refuses_outside_debug(self):
        with self.assertRaises(CommandError):
            run(f'identity={IDENTITY_KEY}')
        self.assertFalse(ServiceClient.objects.exists())
