"""Django system checks for the email app."""

from django.conf import settings
from django.core.checks import Warning, register


@register()
def fallback_provider_configured(app_configs, **kwargs):
    """The fallback stays off, with a warning, when its provider has no credentials. Values are never printed."""
    from apps.email.service import platform_fallback_configured

    name = settings.EMAIL_FALLBACK_PROVIDER
    if settings.DEBUG or name in {'none', 'fake'} or platform_fallback_configured():
        return []
    needed = {
        'resend': 'RESEND_API_KEY',
        'ses': 'SES_ACCESS_KEY_ID and SES_SECRET_ACCESS_KEY',
        'smtp': 'EMAIL_HOST',
    }.get(name)
    hint = f'Set {needed}, or set EMAIL_FALLBACK_PROVIDER=none.' if needed else 'Use resend, ses, smtp, or none.'
    return [
        Warning(
            f'EMAIL_FALLBACK_PROVIDER={name} has no usable credentials. Auth mail for companies without '
            'their own provider fails with provider_not_configured.',
            hint=hint,
            id='email.W001',
        )
    ]
