"""Single source of truth for email-service environment variables.

Pure Python: no Django settings, database, Redis or env needed. Used by
``manage.py generate_env``, to render ``.env.example``, and by the tests that
keep both in sync with ``config/settings.py`` and ``docs/configuration.md``.
"""

from __future__ import annotations

from dataclasses import dataclass

PLACEHOLDER = 'change-me'


@dataclass(frozen=True)
class Var:
    name: str
    default: str = ''
    group: str = ''


@dataclass(frozen=True)
class Required:
    """Required in production (DEBUG=false)."""

    name: str
    comment: str


REQUIRED = (
    Required('SECRET_KEY', 'Signs sessions and CSRF tokens. At least 50 characters. Generated.'),
    Required('DEBUG', 'Production mode.'),
    Required('ALLOWED_HOSTS', 'Public host names, comma-separated, no scheme.'),
    Required('CSRF_TRUSTED_ORIGINS', 'Full HTTPS origin of this service.'),
    Required('PUBLIC_BASE_URL', 'Public URL of this service (links in mail).'),
    Required('IDENTITY_ISSUER', 'JWT iss claim, the identity-service JWT_ISSUER.'),
    Required('IDENTITY_AUDIENCE', 'JWT aud claim, the identity-service JWT_AUDIENCE.'),
    Required('IDENTITY_JWKS', 'Pinned JWKS JSON from the identity /.well-known/jwks.json.'),
    Required('POSTGRES_DATABASE_URL', 'PostgreSQL DSN.'),
    Required('REDIS_URL', 'Shared cache and job broker.'),
    Required('EMAIL_CREDENTIALS_KEY', 'Fernet key for provider credentials. Generated.'),
    Required('EMAIL_VARIABLES_KEY', 'Fernet key for template variables. Generated.'),
    Required('EMAIL_HASH_PEPPER', 'HMAC secret for recipient addresses. Generated.'),
    Required('RESEND_API_KEY', 'Platform fallback key when EMAIL_FALLBACK_PROVIDER=resend.'),
)

OPTIONAL = (
    Var('LOG_LEVEL', 'INFO', 'Runtime'),
    Var('SETUP_TOKEN', ''),
    Var('DJANGO_ADMIN_ENABLED', 'true'),
    Var('EMAIL_SERVICE_PORT', '8003'),
    Var('SQLITE_PATH', 'db.sqlite3', 'Database: SQLite (local only) unless POSTGRES_DATABASE_URL is set'),
    Var('POSTGRES_SSL_REQUIRE', 'true'),
    Var('IDENTITY_SERVICE_URL', '', 'Identity (admin JWTs)'),
    Var('IDENTITY_JWKS_FILE', ''),
    Var('IDENTITY_JWKS_URL', ''),
    Var('JWKS_CACHE_TTL', '900'),
    Var('JWKS_TIMEOUT', '15'),
    Var('JWKS_RETRIES', '2'),
    Var('JWT_ALGORITHMS', 'RS256'),
    Var('JWT_HS256_FALLBACK_SECRET', ''),
    Var('ALLOW_JWT_HS256_FALLBACK', 'false'),
    Var('EMAIL_PUBLIC_URL', '', 'Addresses and links (EMAIL_PUBLIC_URL defaults to PUBLIC_BASE_URL)'),
    Var('DEFAULT_FROM_EMAIL', 'no-reply@shellui.com'),
    Var('DEFAULT_FROM_NAME', 'Shellui'),
    Var('BULK_FROM_EMAIL', 'news@news.shellui.com'),
    Var('BULK_FROM_NAME', 'Shellui'),
    Var('EMAIL_AUTH_LINK_HOSTS', 'id.shellui.com'),
    Var('EMAIL_AUTH_DEFAULT_TTL_SECONDS', '120'),
    Var('EMAIL_AUTH_MAX_TTL_SECONDS', '300'),
    Var('EMAIL_FALLBACK_PROVIDER', 'resend', 'Provider fallback'),
    Var('EMAIL_PLATFORM_COMPANY_IDS', ''),
    Var('SES_ACCESS_KEY_ID', ''),
    Var('SES_SECRET_ACCESS_KEY', ''),
    Var('SES_REGION', 'eu-central-2'),
    Var('SES_CONFIGURATION_SET', ''),
    Var('EMAIL_FALLBACK_COMPANY_DAILY_LIMIT', '50'),
    Var('EMAIL_FALLBACK_GLOBAL_DAILY_LIMIT', '500'),
    Var('EMAIL_FALLBACK_RECIPIENT_HOURLY_LIMIT', '5'),
    Var('EMAIL_ALLOW_COMPANY_SMTP', 'false'),
    Var('EMAIL_ALLOW_FAKE_PROVIDER', 'false'),
    Var('RESEND_WEBHOOK_SECRET', ''),
    Var('EMAIL_RESEND_BROADCAST_RPS', '4'),
    Var('EMAIL_HOST', ''),
    Var('EMAIL_PORT', '587'),
    Var('EMAIL_HOST_USER', ''),
    Var('EMAIL_HOST_PASSWORD', ''),
    Var('EMAIL_USE_TLS', 'true'),
    Var('EMAIL_USE_SSL', 'false'),
    Var('EMAIL_MESSAGE_RETENTION_DAYS', '30', 'Rate limits and retention'),
    Var('EMAIL_IDEMPOTENCY_HOURS', '24'),
    Var('EMAIL_COMPANY_TRANSACTIONAL_PER_HOUR', '1000'),
    Var('EMAIL_RECIPIENT_AUTH_LIMIT', '5'),
    Var('EMAIL_RECIPIENT_AUTH_WINDOW_SECONDS', '600'),
    Var('EMAIL_COMPANY_AUTH_LIMIT', '30'),
    Var('EMAIL_COMPANY_AUTH_WINDOW_SECONDS', '60'),
    Var('EMAIL_CLIENT_IP_HEADER', ''),
    Var('EVENT_LOG_RETENTION_DAYS', '7'),
    Var('EMAIL_MAX_RECIPIENTS', '50', 'Limits'),
    Var('EMAIL_MAX_BATCH_ITEMS', '500'),
    Var('EMAIL_MAX_DOCUMENT_BYTES', '524288'),
    Var('EMAIL_MAX_VARIABLES_BYTES', '8192'),
    Var('EMAIL_BROADCAST_MAX_RECIPIENTS', '10000', 'Broadcasts'),
    Var('EMAIL_BROADCAST_STEP_SECONDS', '20'),
    Var('EMAIL_NEWSLETTER_IP_PER_HOUR', '10', 'Newsletters'),
    Var('EMAIL_NEWSLETTER_ADDRESS_PER_DAY', '3'),
    Var('EMAIL_NEWSLETTER_LIST_PER_HOUR', '500'),
    Var('EMAIL_NEWSLETTER_CONFIRM_HOURS', '48'),
    Var('EMAIL_NEWSLETTER_PENDING_DAYS', '7'),
    Var('EMAIL_NEWSLETTER_RESEND_MINUTES', '10'),
    Var('EMAIL_TURNSTILE_VERIFY_URL', 'https://challenges.cloudflare.com/turnstile/v0/siteverify'),
    Var('EMAIL_NODE_BINARY', 'node', 'Composing'),
    Var('EMAIL_COMPOSE_TIMEOUT_SECONDS', '60'),
    Var('EMAIL_DELIVER_SYNC', 'false', 'Delivery and scheduled jobs'),
    Var('EMAIL_SEND_LEASE_SECONDS', '120'),
    Var('EMAIL_WORKERS_ENABLED', 'true'),
    Var('SCHEDULER_ENABLED', 'true'),
    Var('CELERY_BROKER_URL', ''),
    Var('CELERY_WORKER_CONCURRENCY', '2'),
    Var('CELERY_BEAT_SCHEDULE_FILE', '/tmp/celerybeat-schedule'),
    Var('ACTIONS_OUTBOX_MAX_ATTEMPTS', '8', 'Shellui Actions (webhooks)'),
    Var('ACTIONS_WEBHOOK_TIMEOUT_SECONDS', '5'),
    Var('ACTIONS_WEBHOOK_RETRY_LEASE_SECONDS', '120'),
    Var('ACTIONS_WEBHOOK_SYNC_DELIVERY', 'false'),
    Var('ACTIONS_WEBHOOK_ALLOW_PRIVATE', 'false'),
    Var('CORS_ALLOW_ALL_ORIGINS', 'false', 'CORS and HTTPS'),
    Var('CORS_ALLOW_CREDENTIALS', 'false'),
    Var('CORS_ALLOWED_ORIGINS', ''),
    Var('CORS_ALLOW_PRIVATE_NETWORK', 'true'),
    Var('SECURE_SSL_REDIRECT', 'true'),
    Var('SECURE_HSTS_SECONDS', '31536000'),
    Var('SECURE_HSTS_INCLUDE_SUBDOMAINS', 'true'),
    Var('SECURE_HSTS_PRELOAD', 'false'),
    Var('SESSION_COOKIE_SECURE', 'true'),
    Var('CSRF_COOKIE_SECURE', 'true'),
    Var('SHELLUI_WEBSITE_URL', 'https://shellui.com', 'Public pages'),
    Var('SHELLUI_DOCS_URL', 'https://docs.shellui.com'),
    Var('SHELLUI_PLAYGROUND_URL', 'https://playground.shellui.com'),
    Var('SHELLUI_EMAIL_DOCS_URL', 'https://docs.shellui.com/email'),
    Var('SHELLUI_IDENTITY_DOCS_URL', 'https://docs.shellui.com/identity'),
    Var('SHELLUI_GITHUB_EMAIL_URL', 'https://github.com/shellui/email-service'),
    Var('SENTRY_DSN', '', 'Error reporting'),
    Var('SENTRY_ENVIRONMENT', 'production'),
    Var('SENTRY_RELEASE', ''),
    Var('SENTRY_TRACES_SAMPLE_RATE', '0'),
    Var('GUNICORN_WORKERS', '2', 'Gunicorn (Docker entrypoint)'),
    Var('GUNICORN_THREADS', '2'),
    Var('GUNICORN_TIMEOUT', '120'),
)


def all_names() -> set[str]:
    return {v.name for v in REQUIRED} | {v.name for v in OPTIONAL}


def optional_lines(skip: frozenset[str] = frozenset()) -> list[str]:
    """Commented-out optional vars with their default, grouped by topic."""
    lines: list[str] = []
    for var in OPTIONAL:
        if var.name in skip:
            continue
        if var.group:
            lines.extend(['', f'# {var.group}'])
        lines.append(f'# {var.name}={var.default}')
    return lines


_EXAMPLE_HEADER = """\
# email-service settings. Full reference: docs/configuration.md
# Generated from config/env_registry.py: python manage.py generate_env --example > .env.example
# Production: python manage.py generate_env --host email.example.com
# Copy to .env for local runs. Commented-out lines show the default the code uses.

# Required
# Signs sessions and CSRF tokens. Required to start. 50+ characters when DEBUG=false.
SECRET_KEY=
# Local development only. Unset means false (production).
DEBUG=true
# Local identity-service. JWKS is fetched from {url}/.well-known/jwks.json in DEBUG.
IDENTITY_SERVICE_URL=http://localhost:8000
# Local dev: allow-all CORS. Unset in production (defaults to false).
CORS_ALLOW_ALL_ORIGINS=true

# Required when DEBUG=false (production). With DEBUG=true the mail keys derive from SECRET_KEY.
ALLOWED_HOSTS=
CSRF_TRUSTED_ORIGINS=
PUBLIC_BASE_URL=
IDENTITY_ISSUER=
IDENTITY_AUDIENCE=
IDENTITY_JWKS=
POSTGRES_DATABASE_URL=
REDIS_URL=
EMAIL_CREDENTIALS_KEY=
EMAIL_VARIABLES_KEY=
EMAIL_HASH_PEPPER=
RESEND_API_KEY=

# Optional (defaults shown)"""


def render_example() -> str:
    """The content of ``.env.example`` (local development)."""
    skip = frozenset({'IDENTITY_SERVICE_URL', 'CORS_ALLOW_ALL_ORIGINS'})
    return '\n'.join([_EXAMPLE_HEADER, *optional_lines(skip=skip)]) + '\n'
