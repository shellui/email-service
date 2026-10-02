"""In-process provider for tests. Enabled only when EMAIL_ALLOW_FAKE_PROVIDER is set."""

from __future__ import annotations

from apps.providers.base import ProviderMessage, ProviderResult


class FakeProvider:
    name = 'fake'
    sent: list[ProviderMessage] = []
    fail_code: str = ''
    retryable: bool = False
    raise_on_send: BaseException | None = None

    def send(self, message: ProviderMessage, credentials: dict) -> ProviderResult:
        del credentials
        self.sent.append(message)
        if self.raise_on_send is not None:
            raise self.raise_on_send
        if self.fail_code:
            return ProviderResult(ok=False, retryable=self.retryable, error_code=self.fail_code)
        return ProviderResult(ok=True, provider_message_id=f'fake_{message.message_id}')
