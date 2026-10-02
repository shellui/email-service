"""Active providers. Mailjet is documented in ``apps.providers.mailjet`` and not active."""

from __future__ import annotations

from django.conf import settings

from apps.providers.base import EmailProvider
from apps.providers.fake import FakeProvider
from apps.providers.resend import ResendProvider
from apps.providers.smtp import SmtpProvider

ACTIVE_PROVIDER_NAMES = ('resend', 'smtp')
PLANNED_PROVIDER_NAMES = ('mailjet',)

_RESEND = ResendProvider()
_SMTP = SmtpProvider()
_FAKE = FakeProvider()

PROVIDERS: dict[str, EmailProvider] = {
    'resend': _RESEND,
    'smtp': _SMTP,
}


def get_provider(name: str) -> EmailProvider | None:
    if name == 'fake' and getattr(settings, 'EMAIL_ALLOW_FAKE_PROVIDER', False):
        return _FAKE
    return PROVIDERS.get(name)


def fake_provider() -> FakeProvider:
    return _FAKE
