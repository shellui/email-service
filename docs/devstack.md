---
title: Shellui local dev stack
sidebar_label: Shellui dev stack
description: Environment variables and ports when email-service runs in shellui/shellui dev/docker-compose.yml.
---

# Shellui local dev stack

The shared compose file lives in [shellui/shellui](https://github.com/shellui/shellui) at `dev/docker-compose.yml`. It builds this repo with `target: dev`, bind-mounts the checkout at `/app`, and maps host port **8004** to container port **8000**.

Start the full stack from the shellui repo:

```bash
cd shellui/dev
cp .env.local.example .env.local
docker compose --env-file .env.local up
```

This page lists what email-service expects in that stack. For a standalone clone of email-service only, see [Run email-service](getting-started.md).

## Ports and URLs

| Role | Value |
| --- | --- |
| Browser and public origin | `http://localhost:8004` |
| Compose service name (internal) | `email` |
| Container listen port | `8000` |
| Mailpit SMTP (platform fallback) | `mailpit:1025` (host UI on `8025`) |

Callers on the host use `EMAIL_SERVICE_URL=http://localhost:8004`. Other containers use `http://email:8000`.

## Required variables

Set these on the email container (compose maps prefixed names from `dev/.env.local`).

| Variable | Example in dev stack | Notes |
| --- | --- | --- |
| `SECRET_KEY` | generated | Any non-empty value when `DEBUG=true` |
| `DEBUG` | `true` | Enables local defaults and Django autoreload in the dev target |
| `POSTGRES_DATABASE_URL` | `postgres://shellui:shellui@postgres:5432/email` | One database per service on shared Postgres |
| `REDIS_URL` | `redis://redis:6379/3` | Shared Redis; use a distinct DB index from other services |
| `PUBLIC_BASE_URL` | `http://localhost:8004` | Links in mail and unsubscribe URLs |
| `EMAIL_PUBLIC_URL` | `http://localhost:8004` | Library static assets; defaults to `PUBLIC_BASE_URL` |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1,email` | Include the compose service name |
| `CSRF_TRUSTED_ORIGINS` | `http://localhost:8004,http://localhost:5173` | This service plus admin (5173) |
| `CORS_ALLOW_ALL_ORIGINS` | `true` | Local only; unset in production |
| `IDENTITY_ISSUER` | `http://localhost:8001` | Public issuer; must match identity `JWT_ISSUER` |
| `IDENTITY_AUDIENCE` | `shellui` | Must match identity `JWT_AUDIENCE` |
| `IDENTITY_JWKS_URL` | `http://identity:8000/.well-known/jwks.json` | Server-to-server JWKS fetch inside the network |
| `IDENTITY_SERVICE_URL` | `http://identity:8000` | Audience and other identity API calls from the container |
| `EMAIL_CREDENTIALS_KEY` | Fernet key | Encrypt provider credentials at rest |
| `EMAIL_VARIABLES_KEY` | Fernet key | Encrypt template variables |
| `EMAIL_HASH_PEPPER` | random string | HMAC pepper for recipient addresses |

Generate Fernet keys and a pepper with `python manage.py generate_env` (see [Configuration](configuration.md)).

## Identity: public issuer, internal JWKS

Admin JWTs use `IDENTITY_ISSUER` as the expected `iss` claim (browser-facing `http://localhost:8001`). Verification loads keys from `IDENTITY_JWKS_URL` on the internal network (`http://identity:8000/.well-known/jwks.json`), not from the public host port. Pinning `IDENTITY_JWKS` or `IDENTITY_JWKS_FILE` is optional in dev.

## Platform mail through Mailpit

To deliver platform fallback mail into Mailpit without Resend:

| Variable | Dev stack value |
| --- | --- |
| `EMAIL_FALLBACK_PROVIDER` | `smtp` |
| `EMAIL_HOST` | `mailpit` |
| `EMAIL_PORT` | `1025` |
| `EMAIL_USE_TLS` | `false` |
| `EMAIL_USE_SSL` | `false` |
| `EMAIL_SMTP_VERIFY_CERT` | `false` |

Open [http://localhost:8025](http://localhost:8025) to read captured messages. Production keeps verified TLS; do not set `EMAIL_SMTP_VERIFY_CERT=false` outside local dev.

## Workers and scheduled jobs

The dev Docker target runs `migrate` and `runserver` only. The Shellui compose file may start lane workers and Celery in separate services or overrides. Set `REDIS_URL` so scheduled jobs can run when those processes are enabled.

For the production image, lane workers and the scheduler start in the same container via [tools/docker-entrypoint.sh](https://github.com/shellui/email-service/blob/develop/tools/docker-entrypoint.sh).

## Service keys for callers

Issue an `esk_` key after the stack is up:

```bash
docker compose --env-file .env.local exec email python manage.py create_service_key --service identity
```

Point identity, storage, and hosting at `EMAIL_SERVICE_URL=http://email:8000` inside the network (host port `8004` from your machine).

## Dockerfile targets

| Target | Use |
| --- | --- |
| `dev` | Shellui dev compose: deps in `/opt/venv` and `/opt/node`, source bind-mounted at `/app` |
| `production` | Default build and Docker Hub publish (unchanged behaviour) |

Build the dev target locally:

```bash
docker build --target dev -t shellui/email-service:dev .
```
