"""Django system checks for authapi."""

from django.conf import settings
from django.core.checks import Error, Warning, register

_SECRET_KEY_MIN_LENGTH = 50
_SECRET_KEY_PLACEHOLDERS = frozenset(
    {
        'change-me-generate-a-strong-secret-key',
    }
)


def secret_key_is_unsafe(secret: str | None) -> bool:
    value = (secret or '').strip()
    folded = value.casefold()
    if not value or len(value) < _SECRET_KEY_MIN_LENGTH:
        return True
    if folded.startswith('django-insecure-'):
        return True
    return folded in _SECRET_KEY_PLACEHOLDERS


@register(tags='security')
def secret_key_refused_outside_debug(app_configs, **kwargs):
    if not getattr(settings, 'SECRET_KEY_ENFORCE_STRENGTH', not settings.DEBUG):
        return []
    if not secret_key_is_unsafe(settings.SECRET_KEY):
        return []
    return [
        Error(
            'SECRET_KEY is empty, shorter than 50 characters, or a published placeholder.',
            hint=(
                'Set SECRET_KEY to the output of '
                'django.core.management.utils.get_random_secret_key. '
                'Do not use an empty value, a django-insecure- prefix, '
                'or change-me-generate-a-strong-secret-key.'
            ),
            id='authapi.E001',
        )
    ]


@register(deploy=True, tags='security')
def shared_cache_recommended_for_multi_worker(app_configs, **kwargs):
    if settings.DEBUG:
        return []
    backend = settings.CACHES.get('default', {}).get('BACKEND', '')
    if 'locmem' not in backend.lower():
        return []
    workers = int(getattr(settings, 'GUNICORN_WORKERS', 2) or 2)
    if workers <= 1:
        return []
    return [
        Warning(
            'LocMemCache is not shared across Gunicorn workers. Rate limits would be per worker.',
            hint='Set REDIS_URL so auth and company rate limits are shared.',
            id='authapi.W001',
        )
    ]
