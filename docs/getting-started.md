---
title: Run email-service
sidebar_label: Run email-service
description: Run email-service locally with Docker Compose or uv, then issue a service key and check health.
---

# Run email-service

This page takes you from a clone to a process that can accept mail. You start email-service, point admin calls at identity-service, and issue an `esk_` key for the service that will call it.

Run identity-service first when you need admin JWTs or broadcast audiences. The identity handbook is on [docs.shellui.com/identity](https://docs.shellui.com/identity/).

## What has to be running

| Piece | Local Compose | Production (`DEBUG=false`) |
| --- | --- | --- |
| email-service | This image, host port `8003` | `shellui/email-service`, container port `8000` |
| Redis | Started by Compose | Required. Rate limits and the scheduled jobs use it |
| Postgres | Not in Compose. SQLite on the data volume when `POSTGRES_DATABASE_URL` is empty | Required. The process refuses to start without `POSTGRES_DATABASE_URL` |

Identity is 8000, storage 8001, hosting 8002, email 8003.

## Start with Docker Compose

Docker Compose builds the image, maps port 8003 to Gunicorn on port 8000, starts Redis, and stores SQLite in the `email-service-data` volume. You need Docker and Git:

```bash
git clone https://github.com/shellui/email-service.git
cd email-service
cp .env.example .env
docker compose up --build
```

Set `SECRET_KEY` in `.env` before you start. Generate one with:

```bash
python - <<'PY'
from django.core.management.utils import get_random_secret_key
print(get_random_secret_key())
PY
```

`.env.example` sets `DEBUG=true`. Compose passes `DEBUG` through, so this stack stays in local mode. The image itself defaults to `DEBUG=false` when you run it without that variable.

From inside Compose, identity-service on the host is `http://host.docker.internal:8000`. The Compose file uses that host when `IDENTITY_SERVICE_URL` is unset. If `.env` still says `localhost`, change it to `host.docker.internal` or the container cannot reach identity-service.

The API is at [http://localhost:8003/api/v1/health](http://localhost:8003/api/v1/health). Stop the stack with `docker compose down`. The named volume keeps the SQLite file.

Compose does not start Postgres. For a local Postgres, set `POSTGRES_DATABASE_URL` and point it at a database you run yourself.

## Start from a checkout

Use this path when you are changing the Python code. You need [uv](https://docs.astral.sh/uv/) and Node.js 22:

```bash
uv sync
cp .env.example .env
npm ci
npm run build:css
uv run python manage.py migrate
uv run python manage.py runserver 8003
```

`npm run build:css` writes the landing-page stylesheet. With `DEBUG=true`, `runserver` runs that build once at startup, and runs `npm ci` if `node_modules` is missing. When you edit `templates/` or the Tailwind sources, run `npm run watch:css` in a second terminal.

The Docker image starts the lane workers and the scheduled jobs. With `uv run` you start them yourself:

```bash
uv run python manage.py run_email_worker --lane auth
uv run python manage.py run_email_worker --lane transactional
uv run python manage.py run_email_worker --lane bulk
uv run celery -A config worker --beat --pool threads
```

The Celery command needs `REDIS_URL`. Leave `EMAIL_DELIVER_SYNC=false` outside tests. That flag sends inside the request and is for tests.

## Create the first admin user

With `DEBUG=true` and an empty user table, [http://localhost:8003/](http://localhost:8003/) shows a one-time form for the first Django superuser. After that, the same URL is the landing page, with links to Swagger, ReDoc, and Django admin.

In production (`DEBUG=false`), leave `SETUP_TOKEN` empty and create the superuser from the shell:

```bash
uv run python manage.py createsuperuser
```

Or set `SETUP_TOKEN` and open `/?setup_token=your_setup_token_here` once. The form is refused when the token does not match.

## Check health

`GET /api/v1/health` is public. It returns the process version and does not open the database:

```bash
curl -fsS http://localhost:8003/api/v1/health
```

```json
{"status": "ok", "version": "0.1.0"}
```

A `200` here does not prove Redis, Postgres, or a lane worker is up. Worker and job health is `GET /api/v1/scheduled-jobs` (staff JWT). See [Scheduled jobs](scheduled-jobs.md).

## Issue a service key

Sibling services send `Authorization: Bearer` with an `esk_` key. Create one for identity:

```bash
uv run python manage.py create_service_key \
  --service identity \
  --lanes auth,transactional \
  --prefixes identity.
```

The command prints the key once. email-service stores a SHA-256 hash and a 12-character prefix. Put the printed value in the caller as `EMAIL_SERVICE_API_KEY`. `EMAIL_SERVICE_URL` defaults to `https://email.shellui.com`. Locally it is `http://localhost:8003`.

Recommended scopes:

| Caller | `--lanes` | `--prefixes` |
| --- | --- | --- |
| identity | `auth,transactional` | `identity.` |
| storage | `transactional` | `storage.` |
| hosting | `transactional` | `hosting.` |

storage-service and hosting-service refuse a private or loopback `EMAIL_SERVICE_URL` unless `EMAIL_SERVICE_ALLOW_PRIVATE=true`. Set that on the caller when email-service is on `localhost` or `host.docker.internal`. Leave it unset when the URL is public. identity-service does not have this setting.

## Run it in production

The image defaults to `DEBUG=false`. It will not start until these are set:

- `SECRET_KEY`
- `IDENTITY_ISSUER` and `IDENTITY_AUDIENCE` (match identity-service)
- `IDENTITY_JWKS` or `IDENTITY_JWKS_FILE` (a pinned document, not only a JWKS URL)
- `POSTGRES_DATABASE_URL`
- `REDIS_URL`
- `EMAIL_CREDENTIALS_KEY`, `EMAIL_VARIABLES_KEY`, and `EMAIL_HASH_PEPPER`

Generate each of the last three with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`EMAIL_CREDENTIALS_KEY` and `EMAIL_VARIABLES_KEY` must be Fernet keys. `EMAIL_HASH_PEPPER` is the HMAC secret for addresses. The same command produces a value that works. With `DEBUG=true`, those three are derived from `SECRET_KEY`. Do not rely on that derivation when `DEBUG=false`.

Set `EMAIL_FALLBACK_PROVIDER` to `resend` or `smtp`, and set that provider's credentials, before auth mail can leave the platform. Set it to `none` to disable the fallback. Companies that are not listed in `EMAIL_PLATFORM_COMPANY_IDS` must bring their own provider for non-auth mail. Auth mail still uses the fallback.

Every variable is in [Configuration](configuration.md). Production defaults for HTTPS, CORS, and link hosts are in [Security](security.md). The Resend account steps are in [Providers](providers.md). Image tags are in [PUBLISH.md](../PUBLISH.md).
