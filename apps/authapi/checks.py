"""Django system checks for authapi."""

from django.conf import settings
from django.core.checks import Warning, register


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
