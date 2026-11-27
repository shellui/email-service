# email-service

email-service sends mail for Shellui. identity-service, storage-service, and hosting-service call it for sign-in links, invitations, and event mail. Pull `shellui/email-service` to run it. Shellui is free to run under the MIT License.

## What the image includes

The production stage is `python:3.15-slim`. Gunicorn listens on port 8000. The entrypoint is `/app/tools/docker-entrypoint.sh`, and the process runs as the non-root user `appuser`.

The default command migrates, then starts Gunicorn, the scheduler, and a worker per mail lane (auth, transactional, bulk). Pass `worker` to run the scheduler and lane workers without Gunicorn or migrations. The image includes Node.js 26 for React Email rendering. The image declares a volume at `/app/data`.

## Start with docker run

This command starts the web process and maps host port 8000 to the container. Replace each placeholder with a real value. Postgres and Redis must accept connections at the URLs you set. The command does not start those databases.

```bash
docker run --rm -p 8000:8000 \
  -e SECRET_KEY=your_secret_key_here \
  -e DEBUG=false \
  -e ALLOWED_HOSTS=email.example.com \
  -e CSRF_TRUSTED_ORIGINS=https://email.example.com \
  -e PUBLIC_BASE_URL=https://email.example.com \
  -e IDENTITY_SERVICE_URL=https://id.example.com \
  -e IDENTITY_ISSUER=https://id.example.com \
  -e IDENTITY_AUDIENCE=shellui \
  -e POSTGRES_DATABASE_URL=postgres://email:your_password_here@db:5432/email \
  -e REDIS_URL=redis://redis:6379/0 \
  -e EMAIL_CREDENTIALS_KEY=your_credentials_key_here \
  -e EMAIL_VARIABLES_KEY=your_variables_key_here \
  -e EMAIL_HASH_PEPPER=your_hash_pepper_here \
  shellui/email-service:latest
```

## Start with Compose

This Compose file sets the same required variables. Point `POSTGRES_DATABASE_URL` and `REDIS_URL` at Postgres and Redis you already run.

```yaml
services:
  email-service:
    image: shellui/email-service:latest
    ports:
      - "8000:8000"
    environment:
      SECRET_KEY: your_secret_key_here
      DEBUG: "false"
      ALLOWED_HOSTS: email.example.com
      CSRF_TRUSTED_ORIGINS: https://email.example.com
      PUBLIC_BASE_URL: https://email.example.com
      IDENTITY_SERVICE_URL: https://id.example.com
      IDENTITY_ISSUER: https://id.example.com
      IDENTITY_AUDIENCE: shellui
      POSTGRES_DATABASE_URL: postgres://email:your_password_here@db:5432/email
      REDIS_URL: redis://redis:6379/0
      EMAIL_CREDENTIALS_KEY: your_credentials_key_here
      EMAIL_VARIABLES_KEY: your_variables_key_here
      EMAIL_HASH_PEPPER: your_hash_pepper_here
```

## Required environment variables

Use these production variables from `config/env_registry.py` when `DEBUG` is `false`:

| Variable | Role |
| --- | --- |
| `SECRET_KEY` | Signs sessions and cross-site request forgery (CSRF) tokens, and must be at least 50 characters. |
| `DEBUG` | Leave this `false` in production. The image defaults to `false`. |
| `ALLOWED_HOSTS` | Public host names, comma-separated, no scheme |
| `CSRF_TRUSTED_ORIGINS` | Full HTTPS origin of this service |
| `PUBLIC_BASE_URL` | Public URL of this service, including links in mail |
| `IDENTITY_SERVICE_URL` | identity-service origin. When keys are not pinned, email-service fetches the JSON Web Key Set (JWKS) from that origin at `/.well-known/jwks.json`. |
| `IDENTITY_ISSUER` | JSON Web Token (JWT) `iss` claim. Match identity-service `JWT_ISSUER`. |
| `IDENTITY_AUDIENCE` | JWT `aud` claim. Match identity-service `JWT_AUDIENCE`. |
| `POSTGRES_DATABASE_URL` | PostgreSQL connection URL |
| `REDIS_URL` | Shared cache and job broker |
| `EMAIL_CREDENTIALS_KEY` | Fernet key for provider credentials |
| `EMAIL_VARIABLES_KEY` | Fernet key for template variables |
| `EMAIL_HASH_PEPPER` | Hash-based message authentication code (HMAC) secret for recipient addresses |

Generate `SECRET_KEY` and the three mail secrets with the commands in the [configuration reference](https://docs.shellui.com/email/configuration/). That page also lists each optional variable and its default.

## Image tags

The publish workflow builds `linux/amd64` and `linux/arm64`. For Git tag `vX.Y.Z`, it pushes these tags:

| Tag | When |
| --- | --- |
| `X.Y.Z` | Every published release |
| `X.Y` | That same release |
| `latest` | Every release except a GitHub prerelease, plus every manual run |

## Health check

The image health check calls `GET /health/live` on port 8000. A `200` means the web process is up, and the check does not query Postgres or Redis. In `worker` mode the check passes while the entrypoint is running. `GET /api/v1/health` is public and returns the process version.

## Project links

Read the source, the handbook, the changelog, the license, and Shellui:

- [email-service on GitHub](https://github.com/shellui/email-service)
- [email-service docs](https://docs.shellui.com/email/)
- [Changelog](https://github.com/shellui/email-service/blob/main/CHANGELOG.md)
- [MIT License](https://github.com/shellui/email-service/blob/main/LICENSE)
- [Shellui](https://shellui.com)
