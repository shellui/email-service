"""
Django settings for Shellui email-service.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import tomllib
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / '.env')


def _env_csv(name, default):
    raw = os.getenv(name, '').strip()
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(',') if item.strip()]


def _env_float(name, default):
    raw = os.getenv(name, '').strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ImproperlyConfigured(f'{name} must be a number. Got: {raw!r}') from exc


def _env_int(name, default):
    raw = os.getenv(name, '').strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ImproperlyConfigured(f'{name} must be an integer. Got: {raw!r}') from exc


def _env_bool(name, default: bool) -> bool:
    raw = os.getenv(name, '').strip()
    if not raw:
        return default
    return raw.lower() in {'1', 'true', 'yes', 'on'}


def _caches_config(redis_url: str) -> dict:
    """Shared cache for rate limits. LocMem when REDIS_URL is unset (local dev)."""
    if redis_url:
        return {
            'default': {
                'BACKEND': 'django.core.cache.backends.redis.RedisCache',
                'LOCATION': redis_url,
            }
        }
    return {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'email-service',
        }
    }


def _fernet_key_from_secret(secret: str) -> str:
    digest = hashlib.sha256(secret.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest).decode('ascii')


_secret_key = os.getenv('SECRET_KEY', '').strip()
if not _secret_key:
    raise ImproperlyConfigured(
        '\n'
        'SECRET_KEY is not set. email-service cannot start.\n'
        '\n'
        'How to fix:\n'
        '  1. Copy the example env file:\n'
        '       cp .env.example .env\n'
        '  2. Set SECRET_KEY in .env.\n'
        '  3. Generate a strong key:\n'
        '       python -c "from django.core.management.utils import '
        'get_random_secret_key; print(get_random_secret_key())"\n'
    )
SECRET_KEY = _secret_key

DEBUG = os.getenv('DEBUG', 'false').strip().lower() in {'1', 'true', 'yes', 'on'}
SETUP_TOKEN = os.getenv('SETUP_TOKEN', '').strip()
LOG_LEVEL = os.getenv('LOG_LEVEL', 'DEBUG' if DEBUG else 'INFO').strip().upper() or (
    'DEBUG' if DEBUG else 'INFO'
)

ALLOWED_HOSTS = _env_csv('ALLOWED_HOSTS', ('localhost', '127.0.0.1'))
CSRF_TRUSTED_ORIGINS = _env_csv(
    'CSRF_TRUSTED_ORIGINS',
    (
        'http://localhost:8003',
        'http://127.0.0.1:8003',
        'http://localhost:4000',
        'http://127.0.0.1:4000',
        'http://localhost:5174',
        'http://127.0.0.1:5174',
        'http://localhost:5175',
        'http://127.0.0.1:5175',
        'https://admin.shellui.com',
    ),
)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')


def _project_version():
    pyproject = BASE_DIR / 'pyproject.toml'
    with pyproject.open('rb') as fh:
        data = tomllib.load(fh)
    version = str(data.get('project', {}).get('version', '')).strip()
    if not version:
        raise ImproperlyConfigured(f'project.version is missing in {pyproject}')
    return version


VERSION = _project_version()

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'corsheaders',
    'rest_framework',
    'drf_spectacular',
    'config.apps.ConfigConfig',
    'apps.authapi',
    'apps.email',
    'apps.actions',
]

REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'apps.authapi.service_auth.ShelluiAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'apps.authapi.permissions.IsAuthenticatedPrincipal',
    ],
    'EXCEPTION_HANDLER': 'config.exceptions.exception_handler',
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'Shellui Email API',
    'DESCRIPTION': (
        'Email templates, company provider settings, and delivery for Shellui. '
        'Service callers send `Authorization: Bearer esk_<token>`. '
        'Admin callers send a Bearer JWT issued by identity-service.'
    ),
    'VERSION': VERSION,
    'SERVE_INCLUDE_SCHEMA': False,
    'SWAGGER_UI_SETTINGS': {
        'persistAuthorization': True,
    },
    'TAGS': [
        {'name': 'send', 'description': 'Direct send and event ingest.'},
        {'name': 'messages', 'description': 'Message status and cancellation.'},
        {'name': 'catalog', 'description': 'Service events, suggested subjects, and default designs.'},
        {'name': 'library', 'description': 'Built-in and company email designs.'},
        {'name': 'templates', 'description': 'Event copies and their versions.'},
        {'name': 'rules', 'description': 'Per-company email rules.'},
        {'name': 'provider', 'description': 'Company sending provider credentials.'},
        {'name': 'stats', 'description': 'Per-company delivery statistics.'},
        {'name': 'suppressions', 'description': 'Address suppressions.'},
        {'name': 'lanes', 'description': 'Priority lane pause switches.'},
        {'name': 'privacy', 'description': 'Erasure of stored addresses.'},
        {'name': 'actions', 'description': 'Outbound Shellui Actions webhooks.'},
        {'name': 'service-clients', 'description': 'Service API keys issued to other Shellui services.'},
        {'name': 'platform-metrics', 'description': 'Prometheus metrics.'},
        {'name': 'health', 'description': 'Service health checks.'},
    ],
}

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'config.request_context.RequestIdMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

REDIS_URL = os.getenv('REDIS_URL', '').strip()
CACHES = _caches_config(REDIS_URL)

POSTGRES_DATABASE_URL = os.getenv('POSTGRES_DATABASE_URL', '').strip()
POSTGRES_SSL_REQUIRE = _env_bool('POSTGRES_SSL_REQUIRE', not DEBUG)

if POSTGRES_DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.parse(
            POSTGRES_DATABASE_URL,
            conn_max_age=600,
            ssl_require=POSTGRES_SSL_REQUIRE,
        )
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': os.getenv('SQLITE_PATH', str(BASE_DIR / 'db.sqlite3')),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

if DEBUG:
    WHITENOISE_USE_FINDERS = True
    WHITENOISE_AUTOREFRESH = True

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


def _parse_jwks_document(raw: str, *, source: str) -> dict:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ImproperlyConfigured(f'{source} is not valid JSON: {exc}') from exc
    if not isinstance(document, dict) or not isinstance(document.get('keys'), list):
        raise ImproperlyConfigured(f'{source} must be a JWKS object with a "keys" array.')
    if not document['keys']:
        raise ImproperlyConfigured(f'{source} has an empty "keys" array.')
    return document


def _load_static_jwks() -> tuple[dict | None, str | None, str | None]:
    file_path = os.getenv('IDENTITY_JWKS_FILE', '').strip()
    if file_path:
        path = Path(file_path)
        if not path.is_absolute():
            path = BASE_DIR / path
        if not path.is_file():
            raise ImproperlyConfigured(f'IDENTITY_JWKS_FILE not found: {path}')
        return _parse_jwks_document(path.read_text(encoding='utf-8'), source=str(path)), 'file', str(path)

    raw = os.getenv('IDENTITY_JWKS', '').strip()
    if raw:
        return _parse_jwks_document(raw, source='IDENTITY_JWKS'), 'env', None
    return None, None, None


def _resolve_identity_jwks_url() -> str:
    explicit = os.getenv('IDENTITY_JWKS_URL', '').strip()
    if explicit:
        return explicit.rstrip('/')
    base = os.getenv('IDENTITY_SERVICE_URL', '').strip().rstrip('/')
    if base:
        return f'{base}/.well-known/jwks.json'
    return 'http://localhost:8000/.well-known/jwks.json'


IDENTITY_JWKS_DOCUMENT, IDENTITY_JWKS_SOURCE, IDENTITY_JWKS_FILE = _load_static_jwks()
IDENTITY_SERVICE_URL = os.getenv('IDENTITY_SERVICE_URL', '').strip().rstrip('/') or None
if IDENTITY_JWKS_DOCUMENT is not None:
    IDENTITY_JWKS_URL = os.getenv('IDENTITY_JWKS_URL', '').strip().rstrip('/') or None
else:
    IDENTITY_JWKS_URL = _resolve_identity_jwks_url()
    if not IDENTITY_JWKS_URL.startswith(('http://', 'https://')):
        raise ImproperlyConfigured(
            f'IDENTITY_JWKS_URL must be an absolute http(s) URL. Got: {IDENTITY_JWKS_URL!r}'
        )
IDENTITY_ISSUER = os.getenv('IDENTITY_ISSUER', '').strip() or None
IDENTITY_AUDIENCE = os.getenv('IDENTITY_AUDIENCE', '').strip() or None
JWKS_CACHE_TTL = _env_int('JWKS_CACHE_TTL', 900)
JWKS_TIMEOUT = _env_float('JWKS_TIMEOUT', 15)
JWKS_RETRIES = _env_int('JWKS_RETRIES', 2)
JWT_HS256_FALLBACK_SECRET = os.getenv('JWT_HS256_FALLBACK_SECRET', '').strip() or None
ALLOW_JWT_HS256_FALLBACK = os.getenv('ALLOW_JWT_HS256_FALLBACK', '').strip().lower() in {
    '1',
    'true',
    'yes',
    'on',
}
if JWT_HS256_FALLBACK_SECRET and not DEBUG and not ALLOW_JWT_HS256_FALLBACK:
    raise ImproperlyConfigured(
        'JWT_HS256_FALLBACK_SECRET is set but DEBUG is false. '
        'HS256 fallback must not be enabled in production. '
        'Unset JWT_HS256_FALLBACK_SECRET, or set DEBUG=true for local use, '
        'or set ALLOW_JWT_HS256_FALLBACK=true only if you fully understand the risk.'
    )
JWT_ALGORITHMS = _env_csv('JWT_ALGORITHMS', ('RS256',))

# Allow-all is a local convenience. Production (DEBUG=false) stays off unless set explicitly.
CORS_ALLOW_ALL_ORIGINS = _env_bool('CORS_ALLOW_ALL_ORIGINS', DEBUG)
CORS_ALLOWED_ORIGINS = [
    'http://localhost:4000',
    'http://127.0.0.1:4000',
    'http://localhost:5174',
    'http://127.0.0.1:5174',
    'http://localhost:5175',
    'http://127.0.0.1:5175',
    'https://admin.shellui.com',
]
for _origin in os.getenv('CORS_ALLOWED_ORIGINS', '').split(','):
    _origin = _origin.strip()
    if _origin and _origin not in CORS_ALLOWED_ORIGINS:
        CORS_ALLOWED_ORIGINS.append(_origin)

CORS_ALLOW_CREDENTIALS = _env_bool('CORS_ALLOW_CREDENTIALS', False)
if CORS_ALLOW_ALL_ORIGINS and CORS_ALLOW_CREDENTIALS:
    raise ImproperlyConfigured(
        'CORS_ALLOW_ALL_ORIGINS=true with CORS_ALLOW_CREDENTIALS=true is unsafe. '
        'Use explicit CORS_ALLOWED_ORIGINS when credentials are enabled.'
    )
CORS_ALLOW_PRIVATE_NETWORK = _env_bool('CORS_ALLOW_PRIVATE_NETWORK', True)
CORS_ALLOW_HEADERS = list(
    {
        'accept',
        'accept-encoding',
        'authorization',
        'content-type',
        'dnt',
        'origin',
        'user-agent',
        'x-csrftoken',
        'x-requested-with',
        'cache-control',
        'x-request-id',
    }
)
CORS_EXPOSE_HEADERS = ['X-Request-ID']

SECURE_SSL_REDIRECT = _env_bool('SECURE_SSL_REDIRECT', not DEBUG)
SECURE_HSTS_SECONDS = _env_int('SECURE_HSTS_SECONDS', 31536000 if not DEBUG else 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', not DEBUG)
SECURE_HSTS_PRELOAD = _env_bool('SECURE_HSTS_PRELOAD', False)
SESSION_COOKIE_SECURE = _env_bool('SESSION_COOKIE_SECURE', not DEBUG)
CSRF_COOKIE_SECURE = _env_bool('CSRF_COOKIE_SECURE', not DEBUG)

DJANGO_ADMIN_ENABLED = _env_bool('DJANGO_ADMIN_ENABLED', True)
GUNICORN_WORKERS = _env_int('GUNICORN_WORKERS', 2)

# Shellui Actions (outbound webhooks)
ACTIONS_WEBHOOK_TIMEOUT_SECONDS = _env_float('ACTIONS_WEBHOOK_TIMEOUT_SECONDS', 5.0)
ACTIONS_OUTBOX_MAX_ATTEMPTS = _env_int('ACTIONS_OUTBOX_MAX_ATTEMPTS', 8)
ACTIONS_WEBHOOK_ALLOW_PRIVATE = _env_bool('ACTIONS_WEBHOOK_ALLOW_PRIVATE', False)
ACTIONS_WEBHOOK_RETRY_LEASE_SECONDS = _env_int('ACTIONS_WEBHOOK_RETRY_LEASE_SECONDS', 120)
ACTIONS_WEBHOOK_SYNC_DELIVERY = _env_bool('ACTIONS_WEBHOOK_SYNC_DELIVERY', False)
EVENT_LOG_RETENTION_DAYS = _env_int('EVENT_LOG_RETENTION_DAYS', 7)

# Email
PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL', 'https://email.shellui.com').strip().rstrip('/')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'no-reply@shellui.com').strip()
DEFAULT_FROM_NAME = os.getenv('DEFAULT_FROM_NAME', 'Shellui').strip()
BULK_FROM_EMAIL = os.getenv('BULK_FROM_EMAIL', 'news@news.shellui.com').strip()
BULK_FROM_NAME = os.getenv('BULK_FROM_NAME', 'Shellui').strip()
EMAIL_FALLBACK_PROVIDER = os.getenv('EMAIL_FALLBACK_PROVIDER', 'resend').strip().lower() or 'none'
RESEND_API_KEY = os.getenv('RESEND_API_KEY', '').strip()
RESEND_WEBHOOK_SECRET = os.getenv('RESEND_WEBHOOK_SECRET', '').strip()
EMAIL_HOST = os.getenv('EMAIL_HOST', '').strip()
EMAIL_PORT = _env_int('EMAIL_PORT', 587)
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '').strip()
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '').strip()
EMAIL_USE_TLS = _env_bool('EMAIL_USE_TLS', True)
EMAIL_USE_SSL = _env_bool('EMAIL_USE_SSL', False)
# Library images load from {EMAIL_PUBLIC_URL}/static/library/ in sent mail.
EMAIL_PUBLIC_URL = os.getenv('EMAIL_PUBLIC_URL', '').strip().rstrip('/') or PUBLIC_BASE_URL
EMAIL_NODE_BINARY = os.getenv('EMAIL_NODE_BINARY', 'node').strip() or 'node'
EMAIL_COMPOSE_TIMEOUT_SECONDS = _env_int('EMAIL_COMPOSE_TIMEOUT_SECONDS', 60)
EMAIL_MAX_DOCUMENT_BYTES = _env_int('EMAIL_MAX_DOCUMENT_BYTES', 512 * 1024)
EMAIL_DELIVER_SYNC = _env_bool('EMAIL_DELIVER_SYNC', False)
EMAIL_ALLOW_FAKE_PROVIDER = _env_bool('EMAIL_ALLOW_FAKE_PROVIDER', DEBUG)
# Company SMTP opens a connection from the worker. Off until an operator opts in.
# Even when enabled, the host must resolve to a public address.
EMAIL_ALLOW_COMPANY_SMTP = _env_bool('EMAIL_ALLOW_COMPANY_SMTP', False)
_LOCAL_AUTH_HOSTS = {'localhost', '127.0.0.1', '::1'}
_auth_host_default = ('id.shellui.com', 'localhost', '127.0.0.1') if DEBUG else ('id.shellui.com',)
EMAIL_AUTH_LINK_HOSTS = [
    host
    for host in _env_csv('EMAIL_AUTH_LINK_HOSTS', _auth_host_default)
    if DEBUG or host.lower().strip('[]') not in _LOCAL_AUTH_HOSTS
]
EMAIL_AUTH_DEFAULT_TTL_SECONDS = _env_int('EMAIL_AUTH_DEFAULT_TTL_SECONDS', 120)
EMAIL_AUTH_MAX_TTL_SECONDS = _env_int('EMAIL_AUTH_MAX_TTL_SECONDS', 300)
EMAIL_MESSAGE_RETENTION_DAYS = _env_int('EMAIL_MESSAGE_RETENTION_DAYS', 30)
EMAIL_IDEMPOTENCY_HOURS = _env_int('EMAIL_IDEMPOTENCY_HOURS', 24)
EMAIL_MAX_RECIPIENTS = _env_int('EMAIL_MAX_RECIPIENTS', 50)
EMAIL_MAX_BATCH_ITEMS = _env_int('EMAIL_MAX_BATCH_ITEMS', 500)
EMAIL_MAX_VARIABLES_BYTES = _env_int('EMAIL_MAX_VARIABLES_BYTES', 8192)
EMAIL_COMPANY_TRANSACTIONAL_PER_HOUR = _env_int('EMAIL_COMPANY_TRANSACTIONAL_PER_HOUR', 1000)
EMAIL_RECIPIENT_AUTH_LIMIT = _env_int('EMAIL_RECIPIENT_AUTH_LIMIT', 5)
EMAIL_RECIPIENT_AUTH_WINDOW_SECONDS = _env_int('EMAIL_RECIPIENT_AUTH_WINDOW_SECONDS', 600)
# Company-wide auth budget so one tenant cannot fill the auth worker past the magic-link TTL.
EMAIL_COMPANY_AUTH_LIMIT = _env_int('EMAIL_COMPANY_AUTH_LIMIT', 30)
EMAIL_COMPANY_AUTH_WINDOW_SECONDS = _env_int('EMAIL_COMPANY_AUTH_WINDOW_SECONDS', 60)
# Longer than the SMTP (15s) and Resend (10s) provider timeouts.
EMAIL_SEND_LEASE_SECONDS = _env_int('EMAIL_SEND_LEASE_SECONDS', 120)
EMAIL_PLATFORM_COMPANY_IDS = []
for _company_raw in os.getenv('EMAIL_PLATFORM_COMPANY_IDS', '').split(','):
    _company_raw = _company_raw.strip()
    if not _company_raw:
        continue
    try:
        EMAIL_PLATFORM_COMPANY_IDS.append(int(_company_raw))
    except ValueError as exc:
        raise ImproperlyConfigured(
            f'EMAIL_PLATFORM_COMPANY_IDS must be a comma-separated list of integers. Got: {_company_raw!r}'
        ) from exc
EMAIL_VARIABLES_KEY = os.getenv('EMAIL_VARIABLES_KEY', '').strip()
EMAIL_CREDENTIALS_KEY = os.getenv('EMAIL_CREDENTIALS_KEY', '').strip()
EMAIL_HASH_PEPPER = os.getenv('EMAIL_HASH_PEPPER', '').strip()
if DEBUG:
    if not EMAIL_VARIABLES_KEY:
        EMAIL_VARIABLES_KEY = _fernet_key_from_secret(SECRET_KEY + ':variables')
    if not EMAIL_CREDENTIALS_KEY:
        EMAIL_CREDENTIALS_KEY = _fernet_key_from_secret(SECRET_KEY + ':credentials')
    if not EMAIL_HASH_PEPPER:
        EMAIL_HASH_PEPPER = _fernet_key_from_secret(SECRET_KEY + ':pepper')

SHELLUI_WEBSITE_URL = os.getenv('SHELLUI_WEBSITE_URL', 'https://shellui.com').strip()
SHELLUI_DOCS_URL = os.getenv('SHELLUI_DOCS_URL', 'https://docs.shellui.com').strip()
SHELLUI_PLAYGROUND_URL = os.getenv('SHELLUI_PLAYGROUND_URL', 'https://playground.shellui.com').strip()
SHELLUI_EMAIL_DOCS_URL = os.getenv('SHELLUI_EMAIL_DOCS_URL', 'https://email.docs.shellui.com').strip()
SHELLUI_IDENTITY_DOCS_URL = os.getenv(
    'SHELLUI_IDENTITY_DOCS_URL', 'https://identity.docs.shellui.com'
).strip()
SHELLUI_GITHUB_EMAIL_URL = os.getenv(
    'SHELLUI_GITHUB_EMAIL_URL', 'https://github.com/shellui/email-service'
).strip()

if not DEBUG:
    _production_config_errors = []
    if not IDENTITY_ISSUER:
        _production_config_errors.append(
            'IDENTITY_ISSUER is required when DEBUG=false. '
            'Set it to identity-service JWT_ISSUER (for example https://id.shellui.com).'
        )
    if not IDENTITY_AUDIENCE:
        _production_config_errors.append(
            'IDENTITY_AUDIENCE is required when DEBUG=false. '
            'Set it to identity-service JWT_AUDIENCE (typically shellui).'
        )
    if not POSTGRES_DATABASE_URL:
        _production_config_errors.append(
            'POSTGRES_DATABASE_URL is required when DEBUG=false. SQLite is for local development only.'
        )
    if not REDIS_URL:
        _production_config_errors.append(
            'REDIS_URL is required when DEBUG=false. Rate limits and lane counters need a shared cache.'
        )
    if not os.getenv('EMAIL_CREDENTIALS_KEY', '').strip():
        _production_config_errors.append('EMAIL_CREDENTIALS_KEY is required when DEBUG=false.')
    if not os.getenv('EMAIL_VARIABLES_KEY', '').strip():
        _production_config_errors.append('EMAIL_VARIABLES_KEY is required when DEBUG=false.')
    if not os.getenv('EMAIL_HASH_PEPPER', '').strip():
        _production_config_errors.append('EMAIL_HASH_PEPPER is required when DEBUG=false.')
    if IDENTITY_JWKS_DOCUMENT is None:
        _production_config_errors.append(
            'IDENTITY_JWKS or IDENTITY_JWKS_FILE is required when DEBUG=false. '
            'Pin the identity public keys. A runtime fetch of IDENTITY_JWKS_URL is not enough.'
        )
    if _production_config_errors:
        raise ImproperlyConfigured('\n'.join(_production_config_errors))

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        'request_id': {
            '()': 'config.request_context.RequestIdFilter',
        },
    },
    'formatters': {
        'console': {
            'format': '{asctime} {levelname} [{name}] [req={request_id}] {message}',
            'style': '{',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'console',
            'filters': ['request_id'],
        },
    },
    'root': {
        'handlers': ['console'],
        'level': LOG_LEVEL,
    },
    'loggers': {
        'django': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'django.request': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False},
        'django.server': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'apps': {'handlers': ['console'], 'level': LOG_LEVEL, 'propagate': False},
        'config': {'handlers': ['console'], 'level': LOG_LEVEL, 'propagate': False},
        'gunicorn.error': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'gunicorn.access': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
    },
}

SENTRY_DSN = os.getenv('SENTRY_DSN', '').strip()
SENTRY_ENVIRONMENT = os.getenv('SENTRY_ENVIRONMENT', '').strip() or (
    'development' if DEBUG else 'production'
)
SENTRY_RELEASE = os.getenv('SENTRY_RELEASE', '').strip() or VERSION
SENTRY_TRACES_SAMPLE_RATE = _env_float('SENTRY_TRACES_SAMPLE_RATE', 0.0)

if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[
            DjangoIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        environment=SENTRY_ENVIRONMENT,
        release=SENTRY_RELEASE,
        traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=False,
        attach_stacktrace=True,
    )
