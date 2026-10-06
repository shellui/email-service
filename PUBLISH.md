# Publish and deploy

How to build, publish, and run the `shellui/email-service` Docker image on Docker Hub.

Publishing to Docker Hub is manual. There is no CI workflow that pushes the image.

## Image overview

| Item | Value |
| --- | --- |
| Registry | Docker Hub |
| Repository | `shellui/email-service` |
| Listen port | `8000` (Compose maps host `${EMAIL_SERVICE_PORT:-8003}`) |
| Data volume | `/app/data` |

The image contains application code, Node with the React Email compose script (`renderer/compose.mjs`), and collected static files, including the library images and fonts under `/static/library/`. Secrets come from the environment at start (see `.env.example`).

The image command selects what runs:

| Command | Starts |
| --- | --- |
| `web` (default) | Database migrations, then gunicorn on port 8000, one delivery worker per lane (`auth`, `transactional`, `bulk`), and a Celery worker with beat for the scheduled jobs |
| `worker` | The lane workers and the scheduled jobs only, for a dedicated worker container |
| anything else | That command, for example `python manage.py create_service_key …` |

One container sends mail with no cron and no extra workers. See [docs/scheduled-jobs.md](docs/scheduled-jobs.md).

## Pre-release checklist

```bash
./tools/pre-release-check.sh
./tools/pre-release-check.sh --image shellui/email-service:release-check
```

The script checks that `uv.lock` exists, `.env` is not tracked, and `manage.py check` passes. With `--image`, it also builds the image.

GitHub Actions [`.github/workflows/pre-release.yml`](.github/workflows/pre-release.yml) runs it on pull requests targeting `main`. CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs tests, the CSS drift check, gitleaks, pip-audit, the link check, and a Docker build.

### Version alignment

Keep these on the release version:

- `version` in `pyproject.toml` (returned by `GET /api/v1/health`)
- a dated entry in `CHANGELOG.md` (`## [x.y.z] - YYYY-MM-DD`)
- the git tag `vX.Y.Z`
- CI green on the release commit

### Smoke test

```bash
VERSION=0.1.0
docker build -t "shellui/email-service:${VERSION}" .

docker run --rm -d --name email-release-smoke -p 18003:8000 \
  -e SECRET_KEY=smoke-test-only \
  -e DEBUG=true \
  -e IDENTITY_JWKS_URL=http://localhost:8000/.well-known/jwks.json \
  "shellui/email-service:${VERSION}"

sleep 10
curl -sS http://127.0.0.1:18003/api/v1/health
docker stop email-release-smoke
```

The container runs the migrations on SQLite, then answers `{"status": "ok", "version": "…"}`. `GET /api/v1/health` does not open the database, so check the logs for `Applying …` lines or errors too.

The logs also show `starting the auth lane worker` (and the other two lanes). Without `REDIS_URL` they show a warning that the scheduled jobs are not running, which is expected for this test.

## Publish to Docker Hub

### Prerequisites

1. The `shellui/email-service` repository exists on Docker Hub, and your account can push to the `shellui` organization.
2. Docker CLI logged in: `docker login`.
3. A clean git tree at the release commit on `main`.

### Tags

| Tag | Purpose |
| --- | --- |
| `x.y.z` | Exact release (pin in production) |
| `latest` | Newest published release |

### Build and push (multi-arch)

On Apple Silicon a plain `docker build` produces `linux/arm64` only, and most servers expect `linux/amd64`. Publish both with buildx:

```bash
VERSION=0.1.0
IMAGE=shellui/email-service

docker buildx create --use --name multi 2>/dev/null || docker buildx use multi

docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t "${IMAGE}:${VERSION}" \
  -t "${IMAGE}:latest" \
  --push .
```

### Git tag

```bash
git tag -a "v${VERSION}" -m "Release ${VERSION}"
git push origin "v${VERSION}"
```

Pushes to `main` run [`.github/workflows/deploy-docs.yml`](.github/workflows/deploy-docs.yml) and publish the docs site. The Pages CNAME is `email.docs.shellui.com`. The API host `email.shellui.com` is not a GitHub Pages name.

## Deploy

### Production settings

Set the production variables from `.env.example`. These are required with `DEBUG=false`. Run `./tools/prod-config-check.sh` with the production environment loaded: it fails when one of them is missing, except the JWKS.

| Variable | Notes |
| --- | --- |
| `SECRET_KEY` | Django secret |
| `POSTGRES_DATABASE_URL` | Postgres. SQLite is not allowed in production |
| `REDIS_URL` | Rate limits and the broker for the scheduled jobs |
| `IDENTITY_ISSUER`, `IDENTITY_AUDIENCE` | Must match identity-service `JWT_ISSUER` and `JWT_AUDIENCE` |
| `IDENTITY_JWKS` or `IDENTITY_JWKS_FILE` | A pinned copy of identity `/.well-known/jwks.json`, not a runtime URL. Update it when identity rotates its signing key |
| `EMAIL_CREDENTIALS_KEY`, `EMAIL_VARIABLES_KEY` | Fernet keys for provider credentials and stored variables |
| `EMAIL_HASH_PEPPER` | HMAC pepper for address hashes |

Generate the keys once and keep them: changing a Fernet key makes stored credentials unreadable.

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Also set `ALLOWED_HOSTS`, `PUBLIC_BASE_URL`, `IDENTITY_SERVICE_URL`, `EMAIL_AUTH_LINK_HOSTS` (the identity host, for example `id.shellui.com`), and the platform provider (`RESEND_API_KEY`, `DEFAULT_FROM_EMAIL`, `BULK_FROM_EMAIL`). See [docs/configuration.md](docs/configuration.md).

### Run the containers

```bash
docker pull shellui/email-service:0.1.0
```

```bash
docker volume create email-service-data

docker run -d --name email-service -p 8003:8000 \
  -v email-service-data:/app/data \
  --env-file .env \
  shellui/email-service:0.1.0
```

The container applies migrations, which also sync the built-in library designs, then starts gunicorn, the three lane workers, and the scheduled jobs (`retry_webhooks` and `sweep_email_queue` every minute, `purge_expired_data` every hour). There is no cron to set up. If you had Coolify Scheduled Tasks for these commands, remove them. To run the workers in their own container, see [docs/scheduled-jobs.md](docs/scheduled-jobs.md#run-the-workers-in-their-own-container).

### Connect identity-service

1. Create a service key on email-service:

   ```bash
   python manage.py create_service_key --service identity --lanes auth,transactional --prefixes identity.
   ```

2. On identity-service, set `EMAIL_SERVICE_API_KEY` to the printed `esk_` value, and `EMAIL_SERVICE_URL` when email-service is not at `https://email.shellui.com`.
3. Check that `EMAIL_AUTH_LINK_HOSTS` on email-service contains the host of identity `JWT_ISSUER`, otherwise magic links are refused with `auth_link_host_not_allowed`.
4. Request a magic link for a non-staff address and check that it arrives.

## Resend checklist (before the first real send)

> [!IMPORTANT]
> Mail looks fine on day one without these, then quietly degrades: bounces are not suppressed, statuses stay at "handed to the provider", broadcasts fail, and Resend unsubscribes are lost. The full steps are in [README: Resend setup](README.md#resend-setup-required-before-going-live).

- [ ] Main domain (`DEFAULT_FROM_EMAIL`) and news subdomain (`BULK_FROM_EMAIL`) are both **verified** in Resend > Domains.
- [ ] `RESEND_API_KEY` is a **Full access** key. A Sending access key makes every broadcast fail with `provider_unauthorized`.
- [ ] A Resend webhook points at `{PUBLIC_BASE_URL}/api/v1/provider-webhooks/resend/all`.
- [ ] The webhook sends `email.sent`, `email.delivered`, `email.delivery_delayed`, `email.bounced`, `email.complained`, `email.failed`, `email.suppressed`, and **`contact.updated`**.
- [ ] Its signing secret is in `RESEND_WEBHOOK_SECRET`, and Resend's webhook page shows `200` responses (`401` means the secret does not match).
- [ ] Open and click tracking are off on both domains.
- [ ] `EMAIL_PLATFORM_COMPANY_IDS`, `PUBLIC_BASE_URL`, and `IDENTITY_SERVICE_URL` are set.
- [ ] The container logs show `starting the bulk lane worker` (or the dedicated worker container is running).
- [ ] Companies with their own Resend account have done the same on their account, with `?company_id={id}` on the webhook URL.

## Rollback

Pull and run a previous tag or digest. Data in Postgres is independent of the image tag; test migrations before downgrading.
