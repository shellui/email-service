"""SMTP adapter. Connects company relays only to a public address."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from django.conf import settings

from apps.actions.ssrf import SSRFError, resolve_public_host
from apps.providers.base import ProviderMessage, ProviderResult, sanitize_header_value

logger = logging.getLogger(__name__)

SMTP_TIMEOUT_SECONDS = 15


class _PinnedSMTPSSL(smtplib.SMTP_SSL):
    """TLS to a pinned IP with the certificate hostname kept on the original host."""

    def __init__(self, *args, tls_hostname: str, **kwargs):
        self._tls_hostname = tls_hostname
        super().__init__(*args, **kwargs)

    def _get_socket(self, host, port, timeout):
        new_socket = smtplib.SMTP._get_socket(self, host, port, timeout)
        return self.context.wrap_socket(new_socket, server_hostname=self._tls_hostname)


def _is_settings_relay(creds: dict) -> bool:
    """True when these credentials are the operator relay, not a company copy of the flag."""
    platform_host = (settings.EMAIL_HOST or '').strip()
    if not platform_host or host_of(creds) != platform_host:
        return False
    try:
        port = int(creds.get('port') or 0)
    except (TypeError, ValueError):
        return False
    return (
        port == int(settings.EMAIL_PORT or 0)
        and str(creds.get('username') or '') == str(settings.EMAIL_HOST_USER or '')
        and str(creds.get('password') or '') == str(settings.EMAIL_HOST_PASSWORD or '')
    )


def host_of(creds: dict) -> str:
    return sanitize_header_value(creds.get('host') or '').strip()


def _open_client(hostname: str, connect_host: str, port: int, *, use_ssl: bool):
    if use_ssl:
        client = _PinnedSMTPSSL(timeout=SMTP_TIMEOUT_SECONDS, tls_hostname=hostname)
        client.connect(connect_host, port)
        client._host = hostname
        return client
    client = smtplib.SMTP(timeout=SMTP_TIMEOUT_SECONDS)
    client.connect(connect_host, port)
    client._host = hostname
    return client


class SmtpProvider:
    name = 'smtp'

    def send(self, message: ProviderMessage, credentials: dict) -> ProviderResult:
        creds = credentials or {}
        host = sanitize_header_value(creds.get('host') or '').strip()
        if not host:
            return ProviderResult(ok=False, retryable=False, error_code='provider_not_configured')
        try:
            port = int(creds.get('port') or 587)
        except (TypeError, ValueError):
            return ProviderResult(ok=False, retryable=False, error_code='provider_rejected')
        username = creds.get('username') or ''
        password = creds.get('password') or ''
        use_tls = bool(creds.get('use_tls', True))
        use_ssl = bool(creds.get('use_ssl', False))
        # A flag inside company credentials cannot skip the public-host pin.
        # Only the relay from settings (host, port, username, password) is trusted.
        trusted_platform = _is_settings_relay(creds)
        if not trusted_platform and not settings.EMAIL_ALLOW_COMPANY_SMTP:
            return ProviderResult(ok=False, retryable=False, error_code='company_smtp_disabled')
        if trusted_platform:
            connect_host = host
        else:
            try:
                connect_host = resolve_public_host(host, port)
            except SSRFError:
                return ProviderResult(ok=False, retryable=False, error_code='provider_host_not_public')

        from_name = sanitize_header_value(message.from_name)
        from_email = sanitize_header_value(message.from_email)
        try:
            email = EmailMessage()
            email['From'] = f'{from_name} <{from_email}>' if from_name else from_email
            email['To'] = sanitize_header_value(message.to_email)
            email['Subject'] = sanitize_header_value(message.subject)
            email['Message-ID'] = f'<{sanitize_header_value(message.message_id)}@email.shellui.com>'
            for key, value in (message.headers or {}).items():
                email[sanitize_header_value(key)] = sanitize_header_value(value)
            email.set_content(message.text or '')
            if message.html:
                email.add_alternative(message.html, subtype='html')
        except (ValueError, UnicodeError):
            return ProviderResult(ok=False, retryable=False, error_code='provider_rejected')

        try:
            client = _open_client(host, connect_host, port, use_ssl=use_ssl)
            try:
                client.ehlo()
                if use_tls and not use_ssl:
                    client.starttls()
                    client.ehlo()
                if username:
                    client.login(username, password)
                client.send_message(email)
            finally:
                try:
                    client.quit()
                except Exception:
                    client.close()
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
        except Exception:
            logger.exception('smtp send failed')
            return ProviderResult(ok=False, retryable=False, error_code='provider_error')
        return ProviderResult(ok=True, provider_message_id=message.message_id)
