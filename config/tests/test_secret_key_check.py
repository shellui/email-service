"""SECRET_KEY is a system-check error outside DEBUG when it is empty, short, or a placeholder."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from django.core.checks import run_checks
from django.test import SimpleTestCase, override_settings

from apps.authapi.checks import secret_key_is_unsafe

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANAGE_PY = PROJECT_ROOT / 'manage.py'
OK_SECRET = 'test-secret-key-for-settings-check-only-0123456789abcdef'


def _check(secret: str) -> subprocess.CompletedProcess[str]:
    key = Fernet.generate_key().decode('ascii')
    env = os.environ.copy()
    env.update(
        {
            'DEBUG': 'false',
            'SECRET_KEY': secret,
            'DJANGO_SETTINGS_MODULE': 'config.settings',
            'IDENTITY_ISSUER': 'https://id.shellui.com',
            'IDENTITY_AUDIENCE': 'shellui',
            'IDENTITY_JWKS': '{"keys":[{"kty":"RSA","kid":"test","n":"x","e":"AQAB"}]}',
            'POSTGRES_DATABASE_URL': 'postgres://email:email@127.0.0.1:5432/email',
            'REDIS_URL': 'redis://127.0.0.1:6379/0',
            'EMAIL_CREDENTIALS_KEY': key,
            'EMAIL_VARIABLES_KEY': key,
            'EMAIL_HASH_PEPPER': 'pepper-for-tests',
            'SECURE_SSL_REDIRECT': 'false',
        }
    )
    return subprocess.run(
        [sys.executable, str(MANAGE_PY), 'check'],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


class SecretKeyCheckTests(SimpleTestCase):
    def test_known_placeholders_and_short_keys_are_unsafe(self):
        self.assertTrue(secret_key_is_unsafe(''))
        self.assertTrue(secret_key_is_unsafe('   '))
        self.assertTrue(secret_key_is_unsafe('short-secret'))
        self.assertTrue(secret_key_is_unsafe('change-me-generate-a-strong-secret-key'))
        self.assertTrue(secret_key_is_unsafe('django-insecure-' + ('a' * 50)))
        self.assertTrue(secret_key_is_unsafe('DJANGO-INSECURE-' + ('b' * 50)))
        self.assertFalse(secret_key_is_unsafe(OK_SECRET))

    @override_settings(SECRET_KEY_ENFORCE_STRENGTH=True, SECRET_KEY='short-secret')
    def test_check_errors_on_a_short_key_when_debug_is_off(self):
        errors = [item for item in run_checks() if item.id == 'authapi.E001']
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].is_serious())

    @override_settings(
        SECRET_KEY_ENFORCE_STRENGTH=True,
        SECRET_KEY='change-me-generate-a-strong-secret-key',
    )
    def test_check_errors_on_the_published_placeholder(self):
        errors = [item for item in run_checks() if item.id == 'authapi.E001']
        self.assertEqual(len(errors), 1)

    @override_settings(SECRET_KEY_ENFORCE_STRENGTH=False, SECRET_KEY='short-secret')
    def test_debug_allows_a_short_key(self):
        errors = [item for item in run_checks() if item.id == 'authapi.E001']
        self.assertEqual(errors, [])

    @override_settings(SECRET_KEY_ENFORCE_STRENGTH=True, SECRET_KEY=OK_SECRET)
    def test_a_long_key_passes_the_check(self):
        errors = [item for item in run_checks() if item.id == 'authapi.E001']
        self.assertEqual(errors, [])

    def test_manage_py_check_fails_for_a_placeholder_outside_debug(self):
        result = _check('change-me-generate-a-strong-secret-key')
        self.assertNotEqual(result.returncode, 0)
        # Settings import refuses the key before the system checks run.
        self.assertIn(
            'SECRET_KEY is shorter than 50 characters or a known placeholder',
            result.stderr + result.stdout,
        )

    def test_manage_py_check_fails_for_a_short_key_outside_debug(self):
        result = _check('django-insecure-12345678901234567890123456789012')
        self.assertNotEqual(result.returncode, 0)
        # Settings import refuses the key before the system checks run.
        self.assertIn(
            'SECRET_KEY is shorter than 50 characters or a known placeholder',
            result.stderr + result.stdout,
        )

    def test_manage_py_check_passes_for_a_long_key(self):
        result = _check(OK_SECRET)
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

    def test_empty_secret_still_refuses_to_start(self):
        env = os.environ.copy()
        env.update(
            {
                'DEBUG': 'false',
                'SECRET_KEY': '',
                'DJANGO_SETTINGS_MODULE': 'config.settings',
            }
        )
        result = subprocess.run(
            [sys.executable, str(MANAGE_PY), 'check'],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SECRET_KEY is not set', result.stderr + result.stdout)
