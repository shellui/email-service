"""SMTP adapter. At-least-once delivery with a deterministic Message-ID."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from apps.providers.base import ProviderMessage, ProviderResult


class SmtpProvider:
    name = 'smtp'

    def send(self, message: ProviderMessage, credentials: dict) -> ProviderResult:
        creds = credentials or {}
        host = (creds.get('host') or '').strip()
        if not host:
            return ProviderResult(ok=False, retryable=False, error_code='provider_not_configured')
        port = int(creds.get('port') or 587)
        username = creds.get('username') or ''
        password = creds.get('password') or ''
        use_tls = bool(creds.get('use_tls', True))
        use_ssl = bool(creds.get('use_ssl', False))

        email = EmailMessage()
        email['From'] = (
            f'{message.from_name} <{message.from_email}>' if message.from_name else message.from_email
        )
        email['To'] = message.to_email
        email['Subject'] = message.subject
        email['Message-ID'] = f'<{message.message_id}@email.shellui.com>'
        for key, value in (message.headers or {}).items():
            email[key] = value
        email.set_content(message.text or '')
        if message.html:
            email.add_alternative(message.html, subtype='html')
        try:
            if use_ssl:
                client = smtplib.SMTP_SSL(host, port, timeout=15)
            else:
                client = smtplib.SMTP(host, port, timeout=15)
            with client:
                client.ehlo()
                if use_tls and not use_ssl:
                    client.starttls()
                    client.ehlo()
                if username:
                    client.login(username, password)
                client.send_message(email)
        except smtplib.SMTPAuthenticationError:
            return ProviderResult(ok=False, retryable=False, error_code='provider_unauthorized')
        except (TimeoutError, smtplib.SMTPServerDisconnected, OSError):
            return ProviderResult(ok=False, retryable=True, error_code='provider_unreachable')
        except smtplib.SMTPResponseException as exc:
            retryable = exc.smtp_code >= 400 and exc.smtp_code not in {550, 551, 552, 553, 554}
            code = 'provider_unavailable' if retryable else 'provider_rejected'
            return ProviderResult(ok=False, retryable=retryable, error_code=code)
        except smtplib.SMTPException:
            return ProviderResult(ok=False, retryable=True, error_code='provider_unavailable')
        return ProviderResult(ok=True, provider_message_id=message.message_id)
