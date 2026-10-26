"""Django system checks for the email app."""

from django.conf import settings
from django.core.checks import Warning, register


@register()
def fallback_provider_configured(app_configs, **kwargs):
    """The fallback stays off, with a warning, when its provider has no credentials. Values are never printed."""
    from apps.email.service import platform_fallback_configured

    name = settings.EMAIL_FALLBACK_PROVIDER
    if settings.DEBUG or name == 'fake' or platform_fallback_configured():
        return []
    if name == 'none':
        if not settings.EMAIL_PLATFORM_COMPANY_IDS:
            return []
        return [
            Warning(
                'EMAIL_PLATFORM_COMPANY_IDS is set but EMAIL_FALLBACK_PROVIDER=none. Platform companies '
                'without their own provider cannot send (provider_not_configured).',
                hint='Set EMAIL_FALLBACK_PROVIDER and its keys, or give those companies a provider.',
                id='email.W002',
            )
        ]
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


@register()
def client_ip_header_configured(app_configs, **kwargs):
    """Behind a proxy, every visitor shares the proxy address and one sign-up rate limit."""
    if settings.DEBUG or settings.EMAIL_CLIENT_IP_HEADER:
        return []
    return [
        Warning(
            'EMAIL_CLIENT_IP_HEADER is empty. Behind a reverse proxy, all public newsletter sign-ups '
            'share the proxy address and one per-IP rate limit.',
            hint='Set EMAIL_CLIENT_IP_HEADER to a header your proxy sets, for example X-Real-IP or CF-Connecting-IP.',
            id='email.W003',
        )
    ]
