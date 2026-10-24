"""manage.py generate_env and the env registry (config/env_registry.py)."""

import os
import re
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

from config import env_registry as reg

BASE_DIR = Path(__file__).resolve().parents[2]
CODE_PATTERNS = (
    re.compile(r"getenv\(\s*['\"]([A-Z][A-Z0-9_]+)['\"]"),
    re.compile(r"_env_[a-z_]+\(\s*'([A-Z][A-Z0-9_]+)'"),
    re.compile(r'\$\{([A-Z][A-Z0-9_]+):'),
)
INTERNAL = {'APP_USER', 'DJANGO_SETTINGS_MODULE'}
COMPOSE_ONLY = {'EMAIL_SERVICE_PORT'}


def _run(*args):
    return subprocess.run(
        [sys.executable, 'manage.py', 'generate_env', *args],
        cwd=BASE_DIR,
        env={'PATH': os.environ.get('PATH', '')},
        capture_output=True,
        text=True,
        check=True,
    )


def _parse(text):
    values = {}
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        name, _, value = line.partition('=')
        assert re.fullmatch(r'[A-Z][A-Z0-9_]*', name), line
        values[name] = value
    return values


def _code_names():
    names = set()
    files = [*BASE_DIR.joinpath('config').glob('*.py'), *BASE_DIR.joinpath('apps').rglob('*.py'),
             BASE_DIR / 'tools' / 'docker-entrypoint.sh']
    for path in files:
        if 'tests' in path.parts or path.name == 'env_registry.py':
            continue
        text = path.read_text(encoding='utf-8')
        for pattern in CODE_PATTERNS:
            names.update(pattern.findall(text))
    return names - INTERNAL


class RegistryTests(SimpleTestCase):
    def test_registry_matches_code(self):
        self.assertEqual(sorted(_code_names() - reg.all_names()), [], 'read by code, missing in env_registry')
        self.assertEqual(sorted(reg.all_names() - _code_names() - COMPOSE_ONLY), [], 'stale in env_registry')

    def test_env_example_is_rendered_from_registry(self):
        self.assertEqual((BASE_DIR / '.env.example').read_text(encoding='utf-8'), reg.render_example(),
                         'run: python manage.py generate_env --example > .env.example')

    def test_docs_reference_every_setting(self):
        docs = (BASE_DIR / 'docs' / 'configuration.md').read_text(encoding='utf-8')
        missing = sorted(n for n in reg.all_names() if f'`{n}`' not in docs)
        self.assertEqual(missing, [])


class GenerateEnvTests(SimpleTestCase):
    def test_works_with_zero_env_and_warns_on_stderr(self):
        out = _run()
        self.assertIn('secret manager', out.stderr)
        self.assertNotIn('SECRET_KEY', out.stderr)
        values = _parse(out.stdout)
        self.assertEqual(values['DEBUG'], 'false')
        self.assertGreaterEqual(len(values['SECRET_KEY']), 50)
        self.assertEqual(set(values), {v.name for v in reg.REQUIRED})

    def test_host_derives_urls(self):
        values = _parse(_run('--host', 'https://Email.example.com/').stdout)
        self.assertEqual(values['ALLOWED_HOSTS'], 'email.example.com')
        self.assertEqual(values['CSRF_TRUSTED_ORIGINS'], 'https://email.example.com')
        self.assertEqual(values['PUBLIC_BASE_URL'], 'https://email.example.com')

    def test_mail_keys_are_valid_fernet_keys(self):
        from cryptography.fernet import Fernet

        values = _parse(_run().stdout)
        for name in ('EMAIL_CREDENTIALS_KEY', 'EMAIL_VARIABLES_KEY', 'EMAIL_HASH_PEPPER'):
            Fernet(values[name].encode())

    def test_two_runs_give_different_secrets(self):
        self.assertNotEqual(_parse(_run().stdout)['SECRET_KEY'], _parse(_run().stdout)['SECRET_KEY'])

    def test_optional_lists_every_setting_once(self):
        out = _run('--optional').stdout
        names = re.findall(r'^#? ?([A-Z][A-Z0-9_]+)=', out, re.M)
        self.assertEqual(sorted(n for n in set(names) if names.count(n) > 1), [])
        self.assertTrue(reg.all_names() <= set(names))

    def test_example_matches_file(self):
        self.assertEqual(_run('--example').stdout, (BASE_DIR / '.env.example').read_text(encoding='utf-8'))


class ProductionSecretKeyTests(SimpleTestCase):
    def _import_settings(self, secret_key):
        env = {
            'PATH': os.environ.get('PATH', ''),
            'DJANGO_SETTINGS_MODULE': 'config.settings',
            'SECRET_KEY': secret_key,
            'DEBUG': 'false',
            'POSTGRES_DATABASE_URL': 'postgres://u:p@127.0.0.1:1/email',
            'REDIS_URL': 'redis://127.0.0.1:1/0',
            'EMAIL_CREDENTIALS_KEY': 'k' * 44,
            'EMAIL_VARIABLES_KEY': 'k' * 44,
            'EMAIL_HASH_PEPPER': 'k' * 44,
            'IDENTITY_ISSUER': 'https://id.example.com',
            'IDENTITY_AUDIENCE': 'shellui',
            'IDENTITY_JWKS': '{"keys": [{"kty": "RSA", "kid": "k", "n": "AQAB", "e": "AQAB"}]}',
        }
        probe = 'import django; django.setup(); print("ok")'
        return subprocess.run([sys.executable, '-c', probe], cwd=BASE_DIR, env=env, capture_output=True, text=True)

    def test_weak_secret_key_refused_without_printing_it(self):
        weak = 'django-insecure-short-value-xyz'
        out = self._import_settings(weak)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn('SECRET_KEY is shorter than 50 characters', out.stderr)
        self.assertNotIn(weak, out.stderr)

    def test_generated_secret_key_accepted(self):
        out = self._import_settings(_parse(_run().stdout)['SECRET_KEY'])
        self.assertEqual(out.returncode, 0, out.stderr[-1500:])
