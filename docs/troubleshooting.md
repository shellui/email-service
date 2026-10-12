---
title: Troubleshooting
sidebar_label: Troubleshooting
description: What to check when email-service accepts a call but no message goes out, or the process will not start.
---

# Troubleshooting

Start from the HTTP status and `error_code`. The JSON body has no translated sentence. Field problems are in `field_errors`.

`GET /api/v1/health` only reports that the process is up. It does not check Postgres, Redis, or the lane workers.

## The process will not start

`DEBUG=false` raises `ImproperlyConfigured` and exits when any of these are missing:

- `SECRET_KEY`
- `IDENTITY_ISSUER` or `IDENTITY_AUDIENCE`
- `IDENTITY_JWKS` or `IDENTITY_JWKS_FILE` (a URL in `IDENTITY_JWKS_URL` is not enough)
- `POSTGRES_DATABASE_URL`
- `REDIS_URL`
- `EMAIL_CREDENTIALS_KEY`, `EMAIL_VARIABLES_KEY`, or `EMAIL_HASH_PEPPER`

`JWT_HS256_FALLBACK_SECRET` with `DEBUG=false` also refuses to start, unless `ALLOW_JWT_HS256_FALLBACK` is set. Leave the HS256 secret unset in production.

Postgres SSL errors against an internal database: `POSTGRES_SSL_REQUIRE` defaults to true when `DEBUG=false`. Set `POSTGRES_SSL_REQUIRE=false` for a database that does not speak TLS.

## The caller cannot reach email-service

| What you see | What to change |
| --- | --- |
| storage or hosting logs a private-address refusal | Set `EMAIL_SERVICE_ALLOW_PRIVATE=true` on that caller. `localhost` and `host.docker.internal` are private. identity-service has no such setting |
| `401 unauthorized` | The `esk_` key is missing, revoked, or not the value printed once by `create_service_key` |
| `403 lane_not_allowed` | The key's `--lanes` omit this lane. Identity needs `auth,transactional` |
| `403 forbidden` | The template key does not start with one of the key's prefixes |
| `403 company_mismatch` | The key's `allowed_company_ids` omit this company, or the JWT company does not match |

## The call succeeded and nothing was sent

`202` with `skipped_reason` is finished. Do not retry it.

| `skipped_reason` | Meaning |
| --- | --- |
| `no_rule` | No enabled email rule for that company and event. `default_enabled` on the catalog does not send mail. Auth-lane events create a built-in rule on the first rules list or the first event. `identity.auth.magic_link.staff_blocked` has no rule: use `/send` |
| `no_recipients` | The rule is enabled and has no address |

A message stuck in `queued` means the lane worker is not running. The image starts one worker per lane. A checkout does not. Broadcasts need `--lane bulk`. See [Scheduled jobs](scheduled-jobs.md).

`library_not_synced` (`503`) means migrations have not synced the built-in designs. Run `manage.py migrate`.

`renderer_unavailable` (`503`) means Node or `renderer/compose.mjs` failed or hit `EMAIL_COMPOSE_TIMEOUT_SECONDS`. Run `npm ci` in the service root.

## The provider refused the message

| `error_code` | Meaning |
| --- | --- |
| `provider_not_configured` | This send has no usable credentials |
| `platform_sender_not_allowed` | Non-auth mail, the company has no provider, and the company is not in `EMAIL_PLATFORM_COMPANY_IDS`. Auth mail still uses the platform fallback. The same code is HTTP 403 when a company sets `from_email` to the platform From address |
| `company_smtp_disabled` | Company SMTP is off (`EMAIL_ALLOW_COMPANY_SMTP` defaults to false) |
| `provider_host_not_public` | The company SMTP host is missing, private, or not a public address |
| `provider_not_available` | The name is not `resend` or `smtp`. Mailjet is not registered |
| `lane_paused` | Staff paused the lane, or this company's provider returned 401 or 403. Other companies keep sending. Resume with `POST /api/v1/lanes/{lane}/resume` |
| `provider_unauthorized` on a broadcast | The Resend key is Sending access. Broadcasts need Full access. See [Providers](providers.md) |
| `provider_test_failed` | The test send was refused. Read the provider response in the admin, not a sentence in this JSON |

Resend webhook `401`: the signing secret does not match. The platform endpoint uses `RESEND_WEBHOOK_SECRET`. `?company_id=` uses that company's secret and does not fall back to the platform secret.

Statuses that stay at "handed to the provider": the Resend webhook is missing `email.sent`, `email.delivered`, or the failure events. Unsubscribes from a Resend broadcast page need `contact.updated`.

## Auth mail was rejected

| `error_code` | Meaning |
| --- | --- |
| `auth_single_recipient` | More than one address on an auth send, batch item, or event |
| `auth_event_rule_forbidden` | A company rule on an auth-lane event |
| `rule_built_in` | Delete, disable, or extra recipients on a built-in auth rule |
| `auth_link_missing` | The copy dropped `magic_link_url` or another required link variable |
| `auth_link_host_not_allowed` | The link host is not on `EMAIL_AUTH_LINK_HOSTS`. `DEBUG=false` drops `localhost`, `127.0.0.1`, and `::1` |
| `auth_link_misplaced` | A link variable sits in an image `src`, a `style`, or an `alt` |
| `auth_literal_link` | A literal URL in the subject, preheader, or document text |
| `variable_url_not_allowed` | A URL variable is not `https`, `mailto`, or `tel`, or the host is not allowlisted |
| `recipient_suppressed` | Auth lane, hard bounce. Do not retry that address. HTTP 422 |
| `recipient_rate_limited` | 5 auth messages per recipient per company per 10 minutes |
| `company_rate_limited` | Auth: `EMAIL_COMPANY_AUTH_LIMIT` (default 30) per `EMAIL_COMPANY_AUTH_WINDOW_SECONDS` (default 60). Transactional: 1000 per company per hour |

An auth message that becomes `expired` missed its TTL before the provider accepted it. The variables are deleted. `shellui_email_auth_ttl_expiries` counts those rows.

## Broadcasts and newsletters

`audience_too_large`: the audience is over `EMAIL_BROADCAST_MAX_RECIPIENTS` (default 10000).

`newsletter_unavailable`: the company cannot send the confirmation email because it has no provider.

`turnstile_failed`: the token is missing or rejected. `turnstile_unavailable`: Cloudflare did not answer.

`origin_not_allowed`: the browser `Origin` is not in the list's `allowed_origins`.

`subscriber_unsubscribed`: a `consented` import tried to add an address that already unsubscribed.

Public sign-up returns `202` for a new address, a pending address, and an address that is already confirmed. That is intentional. The form cannot tell those cases apart.
