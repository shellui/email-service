# Publish and deploy

How to build and run the `shellui/email-service` Docker image.

Publishing to Docker Hub is manual. There is no CI workflow that pushes the image.

## Image overview

| Item | Value |
| --- | --- |
| Registry | Docker Hub |
| Repository | `shellui/email-service` |
| Listen port | `8000` (Compose maps host `${EMAIL_SERVICE_PORT:-8003}`) |
| Data volume | `/app/data` |

The image contains application code, Node with the React Email compose script (`renderer/compose.mjs`), and collected static files, including the library images under `/static/library/`. Gunicorn listens on port 8000. Secrets come from the environment at start (see `.env.example`).

Delivery is not inside Gunicorn. Run `manage.py run_email_worker` beside the web process, plus `retry_webhooks` and `purge_expired_data`.

## Pre-release checklist

```bash
./tools/pre-release-check.sh
```

The script checks that `uv.lock` exists, `.env` is not tracked, and `manage.py check` passes.

GitHub Actions [`.github/workflows/pre-release.yml`](.github/workflows/pre-release.yml) runs on pull requests targeting `main`.

CI on `main` is [`.github/workflows/ci.yml`](.github/workflows/ci.yml): tests, CSS drift, gitleaks, pip-audit, link check, and a Docker build.

### Version alignment

For a release, keep these on the same version:

- `version` in `pyproject.toml`
- a dated entry in `CHANGELOG.md` (`## [x.y.z] - YYYY-MM-DD`)
- optional git tag `vX.Y.Z`

### Smoke test

```bash
export SECRET_KEY="$(uv run python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())")"
export EMAIL_CREDENTIALS_KEY="$(uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")"
export EMAIL_VARIABLES_KEY="$(uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")"
export EMAIL_HASH_PEPPER="$(uv run python -c "import secrets; print(secrets.token_urlsafe(32))")"

VERSION=0.1.0
docker build -t "shellui/email-service:${VERSION}" .

docker run --rm -d --name email-release-smoke -p 18003:8000 \
  -e SECRET_KEY \
  -e EMAIL_CREDENTIALS_KEY \
  -e EMAIL_VARIABLES_KEY \
  -e EMAIL_HASH_PEPPER \
  -e ALLOWED_HOSTS=localhost,127.0.0.1 \
  -e IDENTITY_JWKS='{"keys":[]}' \
  -e IDENTITY_ISSUER=https://pre-release.test \
  -e IDENTITY_AUDIENCE=shellui \
  -e SECURE_SSL_REDIRECT=false \
  -e POSTGRES_SSL_REQUIRE=false \
  -e REDIS_URL=redis://127.0.0.1:6379/0 \
  -e POSTGRES_DATABASE_URL=postgres://email:email@127.0.0.1:5432/email \
  "shellui/email-service:${VERSION}"
```

`GET /api/v1/health` does not open Postgres. A full boot with `DEBUG=false` still requires the variables above to be set, and a real `POSTGRES_DATABASE_URL` plus `REDIS_URL` before traffic. For a local health check without those services, run the container with `DEBUG=true` and SQLite.

```bash
curl -sS http://127.0.0.1:18003/api/v1/health
docker stop email-release-smoke
```

## Publish to Docker Hub

Log in with an account that can push to the `shellui` organization. From a clean tree:

```bash
VERSION=0.1.0
IMAGE=shellui/email-service

docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t "${IMAGE}:${VERSION}" \
  -t "${IMAGE}:latest" \
  --push .
```

```bash
git tag -a "v${VERSION}" -m "Release ${VERSION}"
git push origin "v${VERSION}"
```

Pushes to `main` run [`.github/workflows/deploy-docs.yml`](.github/workflows/deploy-docs.yml) and publish the docs site. The Pages CNAME is `email.docs.shellui.com`. The API host `email.shellui.com` is not a GitHub Pages name.

## Deploy

```bash
docker pull shellui/email-service:0.1.0
```

Set the production variables in `.env.example`, including Fernet keys, identity issuer and audience, Postgres, and Redis. Put a pinned JWKS document in `IDENTITY_JWKS` or `IDENTITY_JWKS_FILE`.

Compose:

```bash
docker compose up -d
```

Then start a worker with the same image and command `python manage.py run_email_worker`.
