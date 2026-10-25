"""``manage.py generate_env``: print a production env block with fresh secrets.

Runs before Django settings load (see manage.py), so it works with zero env,
no database and no Redis. Secrets go to stdout only. Nothing is logged or written.
"""

from __future__ import annotations

import argparse
import base64
import secrets
import sys

from config import env_registry as reg

SECRET_KEY_BYTES = 64  # token_urlsafe(64) is about 86 characters


def _fernet_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('ascii')


def _values(host: str) -> dict[str, str]:
    ph = reg.PLACEHOLDER
    return {
        'SECRET_KEY': secrets.token_urlsafe(SECRET_KEY_BYTES),
        'DEBUG': 'false',
        'ALLOWED_HOSTS': host,
        'CSRF_TRUSTED_ORIGINS': f'https://{host}',
        'PUBLIC_BASE_URL': f'https://{host}',
        'IDENTITY_ISSUER': f'https://{ph}',
        'IDENTITY_AUDIENCE': 'shellui',
        'IDENTITY_JWKS': ph,
        'POSTGRES_DATABASE_URL': f'postgres://{ph}:{ph}@postgres:5432/email',
        'REDIS_URL': 'redis://redis:6379/0',
        'EMAIL_CREDENTIALS_KEY': _fernet_key(),
        'EMAIL_VARIABLES_KEY': _fernet_key(),
        'EMAIL_HASH_PEPPER': _fernet_key(),
    }


def render(*, host: str, optional: bool) -> str:
    values = _values(host)
    lines = [f'# email-service. Replace every {reg.PLACEHOLDER}.']
    for var in reg.REQUIRED:
        lines.append(f'# {var.comment}')
        lines.append(f'{var.name}={values[var.name]}')
    if optional:
        lines.extend(['', '# Optional (defaults shown)'])
        lines.extend(reg.optional_lines(skip=frozenset(values)))
    return '\n'.join(lines) + '\n'


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog='manage.py generate_env',
        description='Print the required production settings with fresh secrets.',
    )
    parser.add_argument('--host', default=f'{reg.PLACEHOLDER}.example.com', help='Public host, for example email.example.com.')
    parser.add_argument('--optional', action='store_true', help='Also print optional settings with their defaults.')
    parser.add_argument('--example', action='store_true', help='Print .env.example (local development, no secrets).')
    args = parser.parse_args(argv)
    if args.example:
        sys.stdout.write(reg.render_example())
        return 0
    host = args.host.strip().lower().removeprefix('https://').removeprefix('http://').rstrip('/')
    sys.stdout.write(render(host=host, optional=args.optional))
    sys.stderr.write('generate_env: this output holds secrets. Store it in a secret manager and do not commit it.\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
