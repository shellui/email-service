---
title: Configuration
sidebar_label: Configuration
description: Environment variables for email-service, including production requirements, secrets, and the settings callers use.
---

# Configuration

Copy [`.env.example`](../.env.example) to `.env`. Names and defaults below match `config/settings.py`.

Production (`DEBUG=false`) refuses to boot without:

- `SECRET_KEY`
- `IDENTITY_ISSUER` and `IDENTITY_AUDIENCE`
- `IDENTITY_JWKS` or `IDENTITY_JWKS_FILE` (a pinned document, not a runtime JWKS URL)
- `POSTGRES_DATABASE_URL`
- `REDIS_URL` (rate limits and the scheduled jobs)
- `EMAIL_CREDENTIALS_KEY`, `EMAIL_VARIABLES_KEY`, `EMAIL_HASH_PEPPER`

## Generate the env

`python manage.py generate_env --host email.example.com` prints every production setting with a fresh `SECRET_KEY`, `EMAIL_CREDENTIALS_KEY`, `EMAIL_VARIABLES_KEY` and `EMAIL_HASH_PEPPER`. It runs with no env, database or Redis. Replace each `change-me` value. Add `--optional` to also list the optional settings with their defaults. The output holds secrets, so store it in a secret manager and never commit it.

`.env.example` is generated from `config/env_registry.py` with `python manage.py generate_env --example > .env.example`. A test fails when the registry, the code, this page or `.env.example` drift apart.

With `DEBUG=false`, a `SECRET_KEY` shorter than 50 characters or a placeholder also stops startup. Errors name the variable and never print its value. The container runs `manage.py check` before migrations when `DEBUG=false`, and the check also refuses a key that starts with `django-insecure-`.

## Secrets

Generate `SECRET_KEY` with:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Generate each mail secret with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`EMAIL_CREDENTIALS_KEY` encrypts provider credentials. `EMAIL_VARIABLES_KEY` encrypts template variables until the provider accepts the message. `EMAIL_HASH_PEPPER` is the HMAC secret for recipient addresses. The Fernet command is the right generator for all three.

When `DEBUG=true`, those three are derived from `SECRET_KEY` if you leave them empty. Do not use that derivation in production.

## Database and Redis

| Variable | Default | Role |
| --- | --- | --- |
| `POSTGRES_DATABASE_URL` | empty | Postgres URL. Required when `DEBUG=false`. Empty uses SQLite (`SQLITE_PATH`, default `db.sqlite3`, `/app/data/db.sqlite3` in the image) |
| `POSTGRES_SSL_REQUIRE` | true when `DEBUG=false`, otherwise false | Set false for an internal Postgres that does not speak TLS |
| `REDIS_URL` | empty | Shared cache and the default Celery broker. Required when `DEBUG=false`. Compose sets `redis://redis:6379/0` |
| `CELERY_BROKER_URL` | `REDIS_URL` | Optional separate Redis for the scheduled jobs |

Compose starts Redis. It does not start Postgres.

## First superuser

With `DEBUG=true`, an empty database shows a one-time form on `/`. In production, leave `SETUP_TOKEN` empty and create the first superuser with:

```bash
uv run python manage.py createsuperuser
```

The web form stays closed when `DEBUG=false` and `SETUP_TOKEN` is empty. Set `SETUP_TOKEN` only when you need that form once, then open `/?setup_token=your_setup_token_here`.

## Identity (admin JWTs)

| Variable | Default | Role |
| --- | --- | --- |
| `IDENTITY_JWKS` or `IDENTITY_JWKS_FILE` | empty | Pinned public keys. Required when `DEBUG=false` |
| `IDENTITY_SERVICE_URL` | `http://localhost:8000` when `DEBUG=true`, otherwise empty | Dev base URL. JWKS is `{url}/.well-known/jwks.json`. Broadcasts also call `{url}/api/v1/users/audience` |
| `IDENTITY_JWKS_URL` | derived | Explicit JWKS URL. Not enough on its own when `DEBUG=false` |
| `IDENTITY_ISSUER` | empty | Required when `DEBUG=false`. Match identity `JWT_ISSUER` |
| `IDENTITY_AUDIENCE` | empty | Required when `DEBUG=false`. Match identity `JWT_AUDIENCE`, typically `shellui` |
| `JWT_HS256_FALLBACK_SECRET` | empty | Local only, when identity is in `DEBUG` with HS256. Refused when `DEBUG=false` unless `ALLOW_JWT_HS256_FALLBACK` is set |
| `JWKS_CACHE_TTL` | `900` | Seconds to cache a fetched JWKS |
| `JWT_ALGORITHMS` | `RS256` | Accepted algorithms |

## Callers

These variables live on identity-service, storage-service, and hosting-service, not on email-service.

| Variable | Default | Role |
| --- | --- | --- |
| `EMAIL_SERVICE_URL` | `https://email.shellui.com` | Origin only. Callers append `/api/v1/send` or `/api/v1/events`. Local Compose is `http://localhost:8003` |
| `EMAIL_SERVICE_API_KEY` | empty | The `esk_` key. Sent as `Authorization: Bearer` |
| `EMAIL_SERVICE_ALLOW_PRIVATE` | `false` | storage-service and hosting-service only. Allow a URL that resolves to a private or loopback address. Set it for `localhost` or `host.docker.internal`. identity-service does not have this setting |

identity-service also has `EMAIL_SERVICE_TIMEOUT_SECONDS` (default 5), `EMAIL_SERVICE_SEND_ATTEMPTS` (default 3), and `EMAIL_SERVICE_RETRY_MAX_SLEEP_SECONDS` (default 1) for direct sends. Event posts use the caller's webhook outbox.

## Addresses

| Variable | Default | Role |
| --- | --- | --- |
| `EMAIL_SERVICE_PORT` | `8003` | Host port for Docker Compose. The container listens on `8000` |
| `PUBLIC_BASE_URL` | `https://email.shellui.com` | Unsubscribe links and the public origin |
| `EMAIL_PUBLIC_URL` | `PUBLIC_BASE_URL` | Library images and fonts load from `{EMAIL_PUBLIC_URL}/static/library/` |
| `DEFAULT_FROM_EMAIL` | `no-reply@shellui.com` | Auth and transactional fallback |
| `DEFAULT_FROM_NAME` | `Shellui` | Display name |
| `BULK_FROM_EMAIL` | `news@news.shellui.com` | Broadcast sender for companies in `EMAIL_PLATFORM_COMPANY_IDS` that have no Bulk From |
| `BULK_FROM_NAME` | `Shellui` | Display name for `BULK_FROM_EMAIL` |
| `EMAIL_AUTH_LINK_HOSTS` | `id.shellui.com`, plus `localhost` and `127.0.0.1` when `DEBUG=true` | Platform hostnames allowed in every company's auth mail. Company application domains and Allowed link hosts are added per company. `DEBUG=false` drops `localhost`, `127.0.0.1`, and `::1` |
| `EMAIL_PLATFORM_COMPANY_IDS` | empty | Comma-separated company ids that may use the platform From for non-auth mail |
| `EMAIL_ALLOW_COMPANY_SMTP` | `false` | Allow a company to store an SMTP relay |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Django host header allowlist |
| `CSRF_TRUSTED_ORIGINS` | empty | Origins trusted for Django admin CSRF |

`email.shellui.com` is the API host. Mail is not sent from that domain.

## Provider fallback

| Variable | Default | Role |
| --- | --- | --- |
| `EMAIL_FALLBACK_PROVIDER` | `none` | Off by default. `resend`, `ses` or `smtp` turns it on and needs that provider's keys |
| `RESEND_API_KEY` | empty | Platform Resend key, only for `EMAIL_FALLBACK_PROVIDER=resend`. Broadcasts need Full access |
| `SES_ACCESS_KEY_ID` | empty | Platform SES access key, for `EMAIL_FALLBACK_PROVIDER=ses` |
| `SES_SECRET_ACCESS_KEY` | empty | Platform SES secret key |
| `SES_REGION` | `eu-central-2` | Platform SES region |
| `SES_CONFIGURATION_SET` | empty | Optional SES configuration set |
| `EMAIL_FALLBACK_COMPANY_DAILY_LIMIT` | `50` | Fallback mail per company per UTC day. `0` means no limit |
| `EMAIL_FALLBACK_GLOBAL_DAILY_LIMIT` | `500` | Fallback mail across all companies per UTC day. `0` means no limit |
| `EMAIL_FALLBACK_RECIPIENT_HOURLY_LIMIT` | `5` | Fallback mail per recipient per company per hour |
| `RESEND_WEBHOOK_SECRET` | empty | Svix secret for the platform Resend account |
| `EMAIL_HOST` | empty | Platform SMTP host. Not gated by `EMAIL_ALLOW_COMPANY_SMTP` |
| `EMAIL_PORT` | `587` | Platform SMTP port |
| `EMAIL_HOST_USER` | empty | Platform SMTP username |
| `EMAIL_HOST_PASSWORD` | empty | Platform SMTP password |
| `EMAIL_USE_TLS` | `true` | STARTTLS |
| `EMAIL_USE_SSL` | `false` | Implicit TLS |
| `EMAIL_SMTP_VERIFY_CERT` | `true` | Verify the `EMAIL_HOST` certificate and hostname on STARTTLS and implicit TLS. Set `false` only for a private relay with a self-signed certificate; a warning is logged on each send. Company SMTP relays are always verified |
| `EMAIL_ALLOW_FAKE_PROVIDER` | true when `DEBUG=true` | Test double. Off in production unless set |

When `DEBUG=false` and the chosen fallback has no credentials, `manage.py check` warns (`email.W001`) and the service still starts. Auth mail for companies without their own provider then fails with `provider_not_configured`. The warning never prints key values. With `none`, no provider key is needed. Companies in `EMAIL_PLATFORM_COMPANY_IDS` without their own provider need a fallback configured, otherwise their mail, newsletters and broadcasts fail with `provider_not_configured` (`manage.py check` warns with `email.W002`).

## Rate limits and retention

| Variable | Default | Role |
| --- | --- | --- |
| `EMAIL_AUTH_DEFAULT_TTL_SECONDS` | `120` | Magic-link TTL when the caller omits `ttl_seconds` |
| `EMAIL_AUTH_MAX_TTL_SECONDS` | `300` | Cap on `ttl_seconds` |
| `EMAIL_RECIPIENT_AUTH_LIMIT` | `5` | Auth messages per recipient per company |
| `EMAIL_RECIPIENT_AUTH_WINDOW_SECONDS` | `600` | Window for that cap |
| `EMAIL_COMPANY_AUTH_LIMIT` | `30` | Auth messages per company |
| `EMAIL_COMPANY_AUTH_WINDOW_SECONDS` | `60` | Window for the company auth cap |
| `EMAIL_COMPANY_TRANSACTIONAL_PER_HOUR` | `1000` | Transactional messages per company per hour |
| `EMAIL_MESSAGE_RETENTION_DAYS` | `30` | Message rows deleted by `purge_expired_data` |
| `EMAIL_IDEMPOTENCY_HOURS` | `24` | How long a repeated `idempotency_key` replays |
| `EMAIL_MAX_RECIPIENTS` | `50` | Recipients on one `/send` |
| `EMAIL_MAX_BATCH_ITEMS` | `500` | Items on one `/send/batch` |
| `EMAIL_SEND_LEASE_SECONDS` | `120` | How long a worker may hold a row mid-send |
| `EMAIL_DELIVER_SYNC` | `false` | Deliver inside the request. Leave false outside tests |
| `EVENT_LOG_RETENTION_DAYS` | `7` | Event log and finished webhook deliveries |

## Broadcasts

| Variable | Default | Role |
| --- | --- | --- |
| `EMAIL_BROADCAST_MAX_RECIPIENTS` | `10000` | Largest audience one broadcast may have. Also the CSV import cap |
| `EMAIL_RESEND_BROADCAST_RPS` | `4` | Resend calls per second while preparing a broadcast. Resend allows 10 per team, shared with every other call on the key |
| `EMAIL_BROADCAST_STEP_SECONDS` | `20` | Time one worker pass spends on broadcasts before it returns to messages |

See [Broadcasts](broadcasts.md).

## Newsletters

| Variable | Default | Role |
| --- | --- | --- |
| `EMAIL_NEWSLETTER_IP_PER_HOUR` | `10` | Public sign-ups per IP per hour |
| `EMAIL_NEWSLETTER_ADDRESS_PER_DAY` | `3` | Confirmation emails per address and list per day |
| `EMAIL_NEWSLETTER_LIST_PER_HOUR` | `500` | Public sign-ups per list per hour |
| `EMAIL_NEWSLETTER_RESEND_MINUTES` | `10` | Minimum wait before a pending address gets another confirmation email |
| `EMAIL_NEWSLETTER_CONFIRM_HOURS` | `48` | How long a confirmation link works |
| `EMAIL_NEWSLETTER_PENDING_DAYS` | `7` | Unconfirmed sign-ups are deleted, and addresses of unsubscribed people cleared, after this many days |
| `EMAIL_CLIENT_IP_HEADER` | `X-Forwarded-For` | Header carrying the visitor IP for the public sign-up rate limit. The default fits Coolify, whose Traefik proxy appends the visitor IP. Use a header your proxy sets, for example `X-Real-IP` or `CF-Connecting-IP`. Set it to an empty value (`EMAIL_CLIENT_IP_HEADER=`) to use the connection address (`REMOTE_ADDR`), for example when the service is reached without a proxy. A missing header or a value that is not an IP also falls back to `REMOTE_ADDR` |
| `EMAIL_CLIENT_IP_TRUSTED_PROXIES` | `1` | Only with `X-Forwarded-For`: the number of proxies that append to it. The visitor IP is read that many entries from the right, because the left side is set by the visitor |
| `EMAIL_TURNSTILE_VERIFY_URL` | `https://challenges.cloudflare.com/turnstile/v0/siteverify` | Turnstile siteverify endpoint |

See [Newsletters](newsletters.md). Addresses are read as IPv4 or IPv6, with or without a port (`203.0.113.9:8080`, `[2001:db8::1]:443`); IPv4-mapped IPv6 counts as IPv4. The per-IP limit counts IPv4 per address and IPv6 per `/64`.

## Composing

| Variable | Default | Role |
| --- | --- | --- |
| `EMAIL_NODE_BINARY` | `node` | Node used to run `renderer/compose.mjs` on every save |
| `EMAIL_COMPOSE_TIMEOUT_SECONDS` | `60` | A compose that runs longer fails with `503 renderer_unavailable` |
| `EMAIL_MAX_DOCUMENT_BYTES` | `524288` | Largest editor document, as JSON (`too_large` above it) |
| `EMAIL_MAX_VARIABLES_BYTES` | `8192` | Largest variables object, as JSON |

## HTTP and process

| Variable | Default | Role |
| --- | --- | --- |
| `DEBUG` | `false` | Local stacks set `true` in `.env.example` |
| `LOG_LEVEL` | `DEBUG` when `DEBUG=true`, otherwise `INFO` | Root log level |
| `DJANGO_ADMIN_ENABLED` | `true` | Django admin at `/admin/` |
| `CORS_ALLOW_ALL_ORIGINS` | true when `DEBUG=true`, otherwise false | Production stays off unless you set the variable |
| `CORS_ALLOW_CREDENTIALS` | `false` | Startup fails if this is true together with allow-all |
| `CORS_ALLOWED_ORIGINS` | empty | Extra browser origins when allow-all is off |
| `CORS_ALLOW_PRIVATE_NETWORK` | `true` | Chrome private-network preflight |
| `SECURE_SSL_REDIRECT` | true when `DEBUG=false` | Redirect HTTP to HTTPS |
| `SECURE_HSTS_SECONDS` | `31536000` when `DEBUG=false`, otherwise `0` | HSTS max-age |
| `SESSION_COOKIE_SECURE` | true when `DEBUG=false` | Secure flag on the session cookie. When true, the name is `__Host-sessionid`. The cookie has no Domain |
| `CSRF_COOKIE_SECURE` | true when `DEBUG=false` | Secure flag on the CSRF cookie. The cookie is HttpOnly and has no Domain. When this flag is true, the name is `__Host-csrftoken` |
| `GUNICORN_WORKERS` | `2` | Gunicorn worker processes |
| `GUNICORN_THREADS` | `2` | Threads per worker |
| `GUNICORN_TIMEOUT` | `120` | Worker timeout, seconds |
| `SCHEDULER_ENABLED` | `true` | Run Celery beat in this container |
| `EMAIL_WORKERS_ENABLED` | `true` | Run the three lane workers in this container |
| `CELERY_WORKER_CONCURRENCY` | `2` | Threads in the scheduler process |
| `ACTIONS_WEBHOOK_TIMEOUT_SECONDS` | `5` | Outbound webhook HTTP timeout |
| `ACTIONS_OUTBOX_MAX_ATTEMPTS` | `8` | Webhook attempts |
| `ACTIONS_WEBHOOK_ALLOW_PRIVATE` | `false` | Allow webhook targets on private addresses. Local only |
| `ACTIONS_WEBHOOK_SYNC_DELIVERY` | `false` | Deliver webhooks inside the request. Leave false outside tests |
| `SENTRY_DSN` | empty | Optional error reporting |
| `SENTRY_ENVIRONMENT` | `development` or `production` from `DEBUG` | Sentry environment name |
| `SENTRY_TRACES_SAMPLE_RATE` | `0` | Trace sample rate |
| `SENTRY_RELEASE` | the project version | Sentry release name |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | true when `DEBUG=false` | HSTS `includeSubDomains` |
| `SECURE_HSTS_PRELOAD` | `false` | HSTS `preload` |
| `JWKS_TIMEOUT` | `15` | JWKS fetch timeout, seconds (DEBUG only) |
| `JWKS_RETRIES` | `2` | JWKS fetch retries (DEBUG only) |
| `ACTIONS_WEBHOOK_RETRY_LEASE_SECONDS` | `120` | Lease on a webhook retry row |
| `CELERY_BEAT_SCHEDULE_FILE` | `/tmp/celerybeat-schedule` | Beat schedule file. Losing it only resets the schedule |
| `SHELLUI_WEBSITE_URL`, `SHELLUI_DOCS_URL`, `SHELLUI_PLAYGROUND_URL` | shellui.com URLs | Links on the public pages |
| `SHELLUI_EMAIL_DOCS_URL`, `SHELLUI_IDENTITY_DOCS_URL`, `SHELLUI_GITHUB_EMAIL_URL` | shellui.com and GitHub URLs | Links on the public pages |

## Health check

`GET /health/live` returns `200 ok` before host checks, the HTTPS redirect, sessions and the database. The Docker image runs `tools/healthcheck.py` against `http://127.0.0.1:8000/health/live` as its `HEALTHCHECK`, so Coolify and Docker need no extra config. It does not check Postgres or Redis. In worker mode it always passes. `GET /api/v1/health` stays the detailed status endpoint.

See [Scheduled jobs](scheduled-jobs.md) to move workers into their own container.
