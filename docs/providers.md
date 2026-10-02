# Providers

Sending goes through `apps.providers`. Workers call `EmailProvider.send` and do not import a vendor SDK.

```text
ProviderMessage -> EmailProvider.send(message, credentials) -> ProviderResult
```

`ProviderResult.ok` means the provider accepted the message. `retryable` schedules another attempt. `error_code` `provider_unauthorized` (HTTP 401 or 403) fails the message and pauses the lane until staff resume it.

Active names (`ACTIVE_PROVIDER_NAMES`): `resend` (default), `smtp`.

## Resend

Company credentials: `{"api_key": "re_example"}`.

The adapter `POST`s `https://api.resend.com/emails` with `Authorization: Bearer`, `User-Agent: shellui-email-service/1.0`, and `Idempotency-Key` set to the message id. HTTP 429 and 5xx are retryable. 401 and 403 pause the lane.

Platform fallback: `EMAIL_FALLBACK_PROVIDER=resend` and `RESEND_API_KEY`.

Inbound events: `POST /api/v1/provider-webhooks/resend/{stream}` with Svix verification. See [integration.md](integration.md).

## SMTP

Company SMTP is off unless `EMAIL_ALLOW_COMPANY_SMTP=true`. Saving `provider: "smtp"` while it is off returns `company_smtp_disabled`. When it is on, `host` is resolved and rejected unless the address is public (`provider_host_not_public`). The worker connects to that public address, not to a private or link-local target. The platform relay in `EMAIL_HOST` is operator configuration and is not gated by this flag. A `trusted_platform` flag, or any key starting with `_`, in company credentials is ignored and is not stored. Only credentials that match the configured relay (host, port, username, and password) skip the public-host pin.

Company credentials:

```json
{
  "host": "smtp.example.com",
  "port": 587,
  "username": "mailer",
  "password": "secret",
  "use_tls": true,
  "use_ssl": false
}
```

`Message-ID` is `<{message_id}@email.shellui.com>` so a retry stays the same message. Platform fallback uses `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL`.

After the auth lane exhausts its short retries, the worker tries the platform SMTP credentials once (`smtp_failover`) if they are set and the company provider was not already SMTP.

## Per company and platform fallback

Each company stores one provider on `PUT /api/v1/provider`. Secrets are Fernet-encrypted with `EMAIL_CREDENTIALS_KEY`. Responses include `configured` and `credentials_hint` (`••••` plus the last four characters). They never include the secret.

If the company has no configured provider, auth mail uses the platform fallback (Resend or SMTP from the environment, From `no-reply@shellui.com`). Non-auth mail does that only when the company id is listed in `EMAIL_PLATFORM_COMPANY_IDS`. Every other company receives `platform_sender_not_allowed` and must save its own provider. A company that is not on that list cannot set its From address to `no-reply@shellui.com` or `news@news.shellui.com`.

## Add Mailjet

Mailjet is the next provider. The module `apps/providers/mailjet.py` is present and not registered, so the admin API rejects `provider: "mailjet"` today.

To enable it:

1. Implement `MailjetProvider.send` in `apps/providers/mailjet.py`. `POST https://api.mailjet.com/v3.1/send` with HTTP basic auth (`api_key` and `secret_key` from the company credentials). Read the provider message id from `Messages[0].To[0].MessageID`. Return `ProviderResult`. Treat HTTP 429 and 5xx as retryable. Treat 401 and 403 as `provider_unauthorized`.
2. Register the instance in `apps.providers.registry.PROVIDERS`.
3. Add `mailjet` to `ACTIVE_PROVIDER_NAMES` in that same module.
4. If Mailjet signs webhooks, add a parser next to `apps.providers.webhooks` and a route `/api/v1/provider-webhooks/mailjet/<stream>`.

No worker, template, or rule changes. Callers only see the provider name.
