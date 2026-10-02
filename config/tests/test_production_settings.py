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
