# Configuration

Copy `.env.example` to `.env`. Production (`DEBUG=false`) refuses to boot without:

- `SECRET_KEY`
- `IDENTITY_ISSUER` and `IDENTITY_AUDIENCE`
- `POSTGRES_DATABASE_URL`
- `REDIS_URL` (rate limits)
- `EMAIL_CREDENTIALS_KEY`, `EMAIL_VARIABLES_KEY`, `EMAIL_HASH_PEPPER` (Fernet keys and the HMAC pepper)

Generate a Fernet key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

When `DEBUG=true`, those three keys are derived from `SECRET_KEY` so local runs need no extra secrets. Do not use that derivation in production.

## Addresses

| Variable | Default | Role |
| --- | --- | --- |
| `PUBLIC_BASE_URL` | `https://email.shellui.com` | Unsubscribe links and the public origin |
| `DEFAULT_FROM_EMAIL` | `no-reply@shellui.com` | Auth and transactional fallback |
| `DEFAULT_FROM_NAME` | `Shellui` | Display name |
| `BULK_FROM_EMAIL` | `news@news.shellui.com` | Reserved for later bulk mail |
| `EMAIL_AUTH_LINK_HOSTS` | `id.shellui.com,localhost,127.0.0.1` | Hosts allowed in auth URL variables |

`email.shellui.com` is the API host. Mail is not sent from that domain.

## Provider fallback

| Variable | Default | Role |
| --- | --- | --- |
| `EMAIL_FALLBACK_PROVIDER` | `resend` | `resend`, `smtp`, or `none` |
| `RESEND_API_KEY` | empty | Platform Resend key |
| `RESEND_WEBHOOK_SECRET` | empty | Svix secret for the platform Resend account |
| `EMAIL_HOST` and related | empty | Platform SMTP |

## Workers and retention

| Variable | Default |
| --- | --- |
| `EMAIL_RENDERER` | `python` locally, `node` in the Docker image |
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
