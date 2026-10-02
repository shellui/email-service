"""Startup validation for production settings."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANAGE_PY = PROJECT_ROOT / 'manage.py'


def _settings_check(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged.update(env)
    merged.setdefault('SECRET_KEY', 'test-secret-key-for-settings-check-only')
    return subprocess.run(
        [sys.executable, str(MANAGE_PY), 'check'],
        cwd=PROJECT_ROOT,
        env=merged,
        capture_output=True,
        text=True,
        check=False,
    )


class ProductionSettingsValidationTests(unittest.TestCase):
    def test_production_requires_identity_issuer_and_audience(self):
        result = _settings_check({'DEBUG': 'false'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('IDENTITY_ISSUER is required when DEBUG=false', result.stderr)
        self.assertIn('REDIS_URL is required when DEBUG=false', result.stderr)
        self.assertIn('POSTGRES_DATABASE_URL is required when DEBUG=false', result.stderr)

    def test_production_requires_a_pinned_jwks_document(self):
        key = Fernet.generate_key().decode('ascii')
        result = _settings_check(
            {
                'DEBUG': 'false',
                'IDENTITY_ISSUER': 'https://id.shellui.com',
                'IDENTITY_AUDIENCE': 'shellui',
                'IDENTITY_JWKS_URL': 'https://id.shellui.com/.well-known/jwks.json',
                'POSTGRES_DATABASE_URL': 'postgres://email:email@127.0.0.1:5432/email',
                'REDIS_URL': 'redis://127.0.0.1:6379/0',
                'EMAIL_CREDENTIALS_KEY': key,
                'EMAIL_VARIABLES_KEY': key,
                'EMAIL_HASH_PEPPER': 'pepper-for-tests',
                'SECURE_SSL_REDIRECT': 'false',
            }
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('IDENTITY_JWKS or IDENTITY_JWKS_FILE is required when DEBUG=false', result.stderr)

    def test_production_cors_off_and_local_auth_hosts_removed(self):
        key = Fernet.generate_key().decode('ascii')
        script = (
            'import django; django.setup(); from django.conf import settings; '
            'print("CORS", settings.CORS_ALLOW_ALL_ORIGINS); '
            'print("HOSTS", ",".join(settings.EMAIL_AUTH_LINK_HOSTS))'
        )
        env = os.environ.copy()
        env.update(
            {
                'DEBUG': 'false',
                'SECRET_KEY': 'test-secret-key-for-settings-check-only',
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
                'EMAIL_AUTH_LINK_HOSTS': 'id.shellui.com,localhost,127.0.0.1,::1',
            }
        )
        env.pop('CORS_ALLOW_ALL_ORIGINS', None)
        result = subprocess.run(
            [sys.executable, '-c', script],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertIn('CORS False', result.stdout)
        self.assertIn('id.shellui.com', result.stdout)
        self.assertNotIn('localhost', result.stdout.split('HOSTS', 1)[-1])
        self.assertNotIn('127.0.0.1', result.stdout)

    def test_production_starts_with_required_settings(self):
        key = Fernet.generate_key().decode('ascii')
        result = _settings_check(
            {
                'DEBUG': 'false',
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
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
