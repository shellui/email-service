---
title: Providers
sidebar_label: Providers
description: Resend and SMTP, the platform fallback, company credentials, and the Resend account steps before mail can leave.
---

# Providers

Sending goes through `apps/providers`. Workers call `EmailProvider.send` and do not import a vendor SDK.

```text
ProviderMessage -> EmailProvider.send(message, credentials) -> ProviderResult
```

`ProviderResult.ok` means the provider accepted the message. `retryable` schedules another attempt. `error_code` `provider_unauthorized` (HTTP 401 or 403) fails the message and pauses that company's lane until staff resume it.

Active names (`ACTIVE_PROVIDER_NAMES`): `resend` (default) and `smtp`. `mailjet` is in the tree and is not registered, so the API rejects `provider: "mailjet"`.

## Resend

Company credentials: `{"api_key": "re_example"}`.

The adapter POSTs `https://api.resend.com/emails` with `Authorization: Bearer`, `User-Agent: shellui-email-service/1.0`, and `Idempotency-Key` set to the message id. HTTP 429 and 5xx are retryable. 401 and 403 pause the lane.

Platform fallback: `EMAIL_FALLBACK_PROVIDER=resend` and `RESEND_API_KEY`.

Inbound events: `POST /api/v1/provider-webhooks/resend/{stream}` with Svix verification. The `{stream}` segment is a label stored on the event. It does not pick a lane.

## Resend account, before mail leaves

Do this once for the platform Resend account, and again for every company that brings its own account.

An API key alone is not enough. Without the steps below, bounced and complained addresses keep receiving mail, delivery statuses stay at "handed to the provider", broadcasts fail, and an unsubscribe on Resend's page is never recorded here.

### Verify two sending domains

In **Resend > Domains**, add both and publish the DNS records Resend shows (DKIM, plus SPF and MX for the return path). Resend refuses to send from a domain that is not verified.

| Domain | Sends | Setting |
| --- | --- | --- |
| Main domain, for example `shellui.com` | Sign-in links, invitations, notifications | `DEFAULT_FROM_EMAIL=no-reply@shellui.com` |
| News subdomain, for example `news.shellui.com` | Broadcasts and other bulk mail | `BULK_FROM_EMAIL=news@news.shellui.com` |

Complaints about news then stay off the domain that sends sign-in mail.

### Create a Full access API key

In **Resend > API Keys**, create a key with **Full access** and put it in `RESEND_API_KEY`.

A **Sending access** key sends single emails. It cannot create the segments, contacts, and broadcasts that Resend Broadcasts need. Every broadcast then fails with `provider_unauthorized`.

### Add the webhook

In **Resend > Webhooks**, add an endpoint:

- **URL:** `{PUBLIC_BASE_URL}/api/v1/provider-webhooks/resend/all`, for example `https://email.shellui.com/api/v1/provider-webhooks/resend/all`
- **Events:** `email.sent`, `email.delivered`, `email.delivery_delayed`, `email.bounced`, `email.complained`, `email.failed`, `email.suppressed`, and `contact.updated`. Leave `email.opened` and `email.clicked` off. email-service ignores them
- **Signing secret:** copy the `whsec_` value into `RESEND_WEBHOOK_SECRET`

| Events | What breaks without them |
| --- | --- |
| `email.sent`, `email.delivered`, `email.delivery_delayed`, `email.failed`, `email.suppressed` | Statuses stop at "handed to the provider". Statistics and the `email.message.*` webhooks are missing |
| `email.bounced`, `email.complained` | Bad addresses and spam complaints are not suppressed |
| `contact.updated` | Someone who unsubscribes from a Resend broadcast is not added to the unsubscribe list here |

Resend must reach that URL. A local email-service needs a tunnel. Each delivery on the Resend webhook page shows the response: `200` is accepted, `401` means the signing secret does not match.

### Leave tracking off

In each domain's settings, leave open and click tracking off. Click tracking rewrites every link through Resend, sign-in links included.

### Plan limits

Each broadcast adds its recipients as Resend contacts, in one segment per language named `{name} ({language}) #{id}`. Contacts count toward the Resend marketing plan and are not deleted after the send.

### A company with its own Resend account

The company repeats the steps on its own account, then:

- Saves its Full access key, From address, and Bulk From address in the admin under **Email > Provider**, with provider **Resend**
- Points its Resend webhook at `https://email.shellui.com/api/v1/provider-webhooks/resend/all?company_id=42`. That URL accepts only the company's signing secret
- Stores the signing secret through the API. The admin form has no field for it yet

`from_email` is required on every `PUT`. The stored API key is kept when `credentials` is omitted. The request uses an identity JWT of a staff member or the company owner:

```bash
curl -X PUT "https://email.shellui.com/api/v1/provider?company_id=42" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"provider":"resend","from_email":"no-reply@acme.com","webhook_secret":"whsec_example"}'
```

## SMTP

Company SMTP is off unless `EMAIL_ALLOW_COMPANY_SMTP=true`. Saving `provider: "smtp"` while it is off returns `company_smtp_disabled`. When it is on, `host` is resolved and rejected unless the address is public (`provider_host_not_public`). The worker connects to that public address. The platform relay in `EMAIL_HOST` is operator configuration and is not gated by this flag. A `trusted_platform` flag, or any key starting with `_`, in company credentials is ignored and is not stored. Only credentials that match the configured relay (host, port, username, and password) skip the public-host pin.

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

`Message-ID` is `<{message_id}@email.shellui.com>` so a retry stays the same message. Platform fallback uses `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, and `EMAIL_USE_SSL`.

After the auth lane exhausts its short retries, the worker tries the platform SMTP credentials once (`smtp_failover`) if they are set and the company provider was not already SMTP.

## Per company and platform fallback

Each company stores one provider on `PUT /api/v1/provider`. Secrets are Fernet-encrypted with `EMAIL_CREDENTIALS_KEY`. Responses include `configured` and `credentials_hint` (a mask plus the last four characters). They never include the secret.

If the company has no configured provider, auth mail uses the platform fallback (Resend or SMTP from the environment, From `DEFAULT_FROM_EMAIL`). Non-auth mail does that only when the company id is listed in `EMAIL_PLATFORM_COMPANY_IDS`. Every other company receives `platform_sender_not_allowed` and must save its own provider. A company that is not on that list cannot set its From address to `DEFAULT_FROM_EMAIL` or `BULK_FROM_EMAIL`.

## Add Mailjet

Mailjet is the next provider. The module `apps/providers/mailjet.py` is present and not registered.

To enable it:

1. Implement `MailjetProvider.send` in `apps/providers/mailjet.py`. POST `https://api.mailjet.com/v3.1/send` with HTTP basic auth (`api_key` and `secret_key` from the company credentials). Read the provider message id from `Messages[0].To[0].MessageID`. Return `ProviderResult`. Treat HTTP 429 and 5xx as retryable. Treat 401 and 403 as `provider_unauthorized`.
2. Register the instance in `apps.providers.registry.PROVIDERS`.
3. Add `mailjet` to `ACTIVE_PROVIDER_NAMES` in that same module.
4. If Mailjet signs webhooks, add a parser next to `apps/providers/webhooks` and a route under `/api/v1/provider-webhooks/mailjet/`.

No worker, template, or rule changes. Callers only see the provider name.
