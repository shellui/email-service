# Configuration

Copy `.env.example` to `.env`. Production (`DEBUG=false`) refuses to boot without:

- `SECRET_KEY`
- `IDENTITY_ISSUER` and `IDENTITY_AUDIENCE`
- `IDENTITY_JWKS` or `IDENTITY_JWKS_FILE` (a pinned document, not a runtime JWKS URL)
- `POSTGRES_DATABASE_URL`
- `REDIS_URL` (rate limits)
- `EMAIL_CREDENTIALS_KEY`, `EMAIL_VARIABLES_KEY`, `EMAIL_HASH_PEPPER` (Fernet keys and the HMAC pepper)

Generate a Fernet key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

When `DEBUG=true`, those three keys are derived from `SECRET_KEY` so local runs need no extra secrets. Do not use that derivation in production.

## First superuser

With `DEBUG=true`, an empty database shows a one-time form on `/`. In production, leave `SETUP_TOKEN` empty and create the first superuser with:

```bash
uv run python manage.py createsuperuser
```

The web form stays closed when `DEBUG=false` and `SETUP_TOKEN` is empty. Set `SETUP_TOKEN` only when you need that form once, then open `/?setup_token=<token>`.

## Addresses

| Variable | Default | Role |
| --- | --- | --- |
| `EMAIL_SERVICE_PORT` | `8003` | Host port for Docker Compose. The container still listens on `8000`. Identity is 8000, storage 8001, hosting 8002. |
| `PUBLIC_BASE_URL` | `https://email.shellui.com` | Unsubscribe links and the public origin |
| `EMAIL_PUBLIC_URL` | `PUBLIC_BASE_URL` | Origin recipients load library images from (`{EMAIL_PUBLIC_URL}/static/library/`). It must be reachable from the internet. |
| `DEFAULT_FROM_EMAIL` | `no-reply@shellui.com` | Auth and transactional fallback |
| `DEFAULT_FROM_NAME` | `Shellui` | Display name |
| `BULK_FROM_EMAIL` | `news@news.shellui.com` | Broadcast sender for Shellui companies without a Bulk From |
| `BULK_FROM_NAME` | `Shellui` | Display name for `BULK_FROM_EMAIL` |
| `EMAIL_AUTH_LINK_HOSTS` | `id.shellui.com` plus localhost only when `DEBUG=true` | Hosts allowed in auth URL variables. `DEBUG=false` drops `localhost`, `127.0.0.1`, and `::1`. |
| `EMAIL_PLATFORM_COMPANY_IDS` | empty | Company ids that may use the platform From for non-auth mail |
| `EMAIL_ALLOW_COMPANY_SMTP` | `false` | Allow a company to store an SMTP relay |
| `EMAIL_COMPANY_AUTH_LIMIT` | `30` | Auth messages per company per window |
| `EMAIL_COMPANY_AUTH_WINDOW_SECONDS` | `60` | Window for the company auth cap |
| `CORS_ALLOW_ALL_ORIGINS` | `true` when `DEBUG=true`, otherwise `false` | Set explicitly to widen browser origins |

`email.shellui.com` is the API host. Mail is not sent from that domain.

## Provider fallback

| Variable | Default | Role |
| --- | --- | --- |
| `EMAIL_FALLBACK_PROVIDER` | `resend` | `resend`, `smtp`, or `none` |
| `RESEND_API_KEY` | empty | Platform Resend key |
| `RESEND_WEBHOOK_SECRET` | empty | Svix secret for the platform Resend account |
| `EMAIL_HOST` and related | empty | Platform SMTP |

## Broadcasts

| Variable | Default | Role |
| --- | --- | --- |
| `IDENTITY_SERVICE_URL` | `http://localhost:8000` when `DEBUG=true`, otherwise empty | Identity origin. Broadcasts read their audience from `/api/v1/users/audience` with the caller's JWT. |
| `EMAIL_BROADCAST_MAX_RECIPIENTS` | `10000` | Largest audience one broadcast may have |
| `EMAIL_RESEND_BROADCAST_RPS` | `4` | Resend calls per second while preparing a broadcast. Resend allows 10 per team, shared with every other call on the key. |
| `EMAIL_BROADCAST_STEP_SECONDS` | `20` | Time one worker pass spends on broadcasts before it returns to messages |

See [broadcasts.md](broadcasts.md).

## Newsletters

| Variable | Default | Role |
| --- | --- | --- |
| `EMAIL_NEWSLETTER_IP_PER_HOUR` | `10` | Public sign-ups per IP per hour |
| `EMAIL_NEWSLETTER_ADDRESS_PER_DAY` | `3` | Confirmation emails per address and list per day |
| `EMAIL_NEWSLETTER_LIST_PER_HOUR` | `500` | Public sign-ups per list per hour |
| `EMAIL_NEWSLETTER_RESEND_MINUTES` | `10` | Minimum wait before a pending address gets another confirmation email |
| `EMAIL_NEWSLETTER_CONFIRM_HOURS` | `48` | How long a confirmation link works |
| `EMAIL_NEWSLETTER_PENDING_DAYS` | `7` | Unconfirmed sign-ups are deleted, and addresses of unsubscribed people cleared, after this many days (`purge_expired_data`) |
| `EMAIL_CLIENT_IP_HEADER` | empty | Header carrying the visitor IP behind a proxy, for example `CF-Connecting-IP`. Empty uses the connection address. |
| `EMAIL_TURNSTILE_VERIFY_URL` | Cloudflare siteverify | Turnstile verification endpoint |

See [newsletters.md](newsletters.md).

## Composing

| Variable | Default | Role |
| --- | --- | --- |
| `EMAIL_NODE_BINARY` | `node` | Node used to run `renderer/compose.mjs` on every save. Run `npm ci` in the service root first. |
| `EMAIL_COMPOSE_TIMEOUT_SECONDS` | `60` | A compose that runs longer fails with `503 renderer_unavailable` |
| `EMAIL_MAX_DOCUMENT_BYTES` | `524288` | Largest editor document accepted, as JSON (`too_large` above it) |

## Workers and retention

| Variable | Default |
| --- | --- |
| `EMAIL_DELIVER_SYNC` | `false` |
| `EMAIL_AUTH_DEFAULT_TTL_SECONDS` | `120` |
| `EMAIL_AUTH_MAX_TTL_SECONDS` | `300` |
| `EMAIL_MESSAGE_RETENTION_DAYS` | `30` |
| `EMAIL_IDEMPOTENCY_HOURS` | `24` |
| `EMAIL_COMPANY_TRANSACTIONAL_PER_HOUR` | `1000` |
| `EMAIL_RECIPIENT_AUTH_LIMIT` | `5` |
| `EMAIL_RECIPIENT_AUTH_WINDOW_SECONDS` | `600` |
| `EVENT_LOG_RETENTION_DAYS` | `7` |
| `ACTIONS_WEBHOOK_TIMEOUT_SECONDS` | `5` |
| `ACTIONS_OUTBOX_MAX_ATTEMPTS` | `8` |

Processes:

- Gunicorn serves HTTP (the image entrypoint).
- `manage.py run_email_worker` sends queued mail.
- `manage.py retry_webhooks` every minute.
- `manage.py purge_expired_data` every hour.
