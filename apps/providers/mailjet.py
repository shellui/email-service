"""Mailjet adapter (planned, not enabled).

Adding Mailjet is one module plus a registry line:

1. Implement ``MailjetProvider.send`` here.
   ``POST https://api.mailjet.com/v3.1/send`` with HTTP basic auth
   (``api_key`` and ``secret_key`` from the company credentials).
   Map the provider message id from ``Messages[0].To[0].MessageID``.
   Return ``ProviderResult`` the same way ``ResendProvider`` does.
   Treat HTTP 429 and 5xx as retryable. Treat 401 and 403 as
   ``provider_unauthorized`` (the worker pauses the lane).
2. Register it in ``apps.providers.registry.PROVIDERS``.
3. Add ``mailjet`` to ``ACTIVE_PROVIDER_NAMES`` in that module so the admin
   API accepts ``provider: "mailjet"``.
4. If Mailjet sends signed webhooks, add a parser next to
   ``apps.providers.webhooks`` and a route under
   ``/api/v1/provider-webhooks/mailjet/<stream>``.

No worker, template, or rule code changes. Callers only see the provider name.
"""

from __future__ import annotations

from apps.providers.base import ProviderMessage, ProviderResult


class MailjetProvider:
    name = 'mailjet'

    def send(self, message: ProviderMessage, credentials: dict) -> ProviderResult:
        del message, credentials
        return ProviderResult(ok=False, retryable=False, error_code='provider_not_implemented')
