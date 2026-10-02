"""Provider interface. Workers call ``send`` and never a vendor SDK directly."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ProviderMessage:
    message_id: str
    to_email: str
    from_email: str
    from_name: str
    subject: str
    html: str
    text: str
    idempotency_key: str
    headers: dict[str, str] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)
    stream: str = 'transactional'


@dataclass
class ProviderResult:
    ok: bool
    provider_message_id: str = ''
    retryable: bool = False
    retry_after_seconds: float | None = None
    error_code: str = ''


class EmailProvider(Protocol):
    name: str

    def send(self, message: ProviderMessage, credentials: dict) -> ProviderResult:
        """Hand one message to the provider. Must not raise for HTTP errors."""
