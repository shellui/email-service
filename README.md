# email-service

email-service sends mail for Shellui. identity-service, storage-service, and hosting-service call it. Companies bring their own Resend or SMTP account. A platform fallback covers Shellui's own mail, and sign-in mail, when a company has none.

## Documentation

The handbook is in [`docs/`](docs/index.md). It is published on [docs.shellui.com](https://docs.shellui.com) at `docs.shellui.com/email`.

Start with the [overview](docs/index.md), then [Run email-service](docs/getting-started.md) and [Configuration](docs/configuration.md). Service keys, the events catalog, the [integration contract](docs/integration.md), providers, email rules, templates, the design library, newsletters, broadcasts, lanes, webhooks, security, metrics, and scheduled jobs each have a page in that sidebar.

[shellui/shellui](https://github.com/shellui/shellui) builds the published site from this `docs/` folder. To preview it, clone `shellui` next to this repository, then run `pnpm install` and `DOCS_SERVICES=email pnpm docs:start` in `../shellui`. See [Build the docs site](https://github.com/shellui/shellui/blob/main/docs/docs-site.md). The **Docs build** job runs that build on every pull request.

The contract other services implement is [docs/integration.md](docs/integration.md). Do not invent a second client shape.

## Features

- Direct send (`POST /api/v1/send`) for magic links, invitations, and the staff notice. Each sign-in link goes to one address
- Event ingest (`POST /api/v1/events`): one message per enabled email rule. No rule returns `skipped_reason: no_rule`
- 40 React Email designs in five sets (Barebone, Matte, Protocol, Arcane, Studio), plus company templates
- Resend and SMTP. Mailjet is a module that is not registered yet
- Per-company credentials encrypted at rest
- Broadcasts and newsletters (double opt-in, CSV import and export, optional Turnstile)
- Prometheus metrics at `GET /api/v1/metrics`
- Shellui Actions webhooks for `email.message.*`, unsubscribes, and newsletter events
- OpenAPI at `/api/docs/` and `/api/docs/redoc/`

## Main endpoints

| Area | Path |
| --- | --- |
| Health | `GET /api/v1/health` |
| Send | `POST /api/v1/send`, `POST /api/v1/send/batch` |
| Events | `POST /api/v1/events` |
| Messages | `GET /api/v1/messages`, `GET /api/v1/messages/{id}`, `POST /api/v1/messages/{id}/cancel` |
| Catalog | `GET /api/v1/catalog` |
| Provider | `GET/PUT /api/v1/provider`, `POST /api/v1/provider/test-send` |
| Library | `GET/POST /api/v1/library`, `GET/PUT/DELETE /api/v1/library/{id}` |
| Rules | `GET/POST /api/v1/rules`, `GET/PATCH/DELETE /api/v1/rules/{id}` |
| Templates | `/api/v1/templates` |
| Broadcasts | `/api/v1/broadcasts` |
| Newsletters | `/api/v1/newsletters`, `POST /api/v1/public/newsletters/{public_key}/subscribe` |
| Stats | `GET /api/v1/stats` |
| Metrics | `GET /api/v1/metrics` |
| Shellui Actions | `/api/v1/actions/*` |
| OpenAPI | `/api/docs/`, `/api/docs/redoc/` |

Service calls use `Authorization: Bearer` and an `esk_` key. Admin calls use an identity-service JWT.

## Docker

```bash
cp .env.example .env
docker compose up --build
```

Host port: `8003` (the container listens on `8000`). Set `SECRET_KEY` in `.env` first. Compose starts Redis. Postgres is required when `DEBUG=false` and is not part of the Compose file. The local path, workers, and the production secret list are in [Run email-service](docs/getting-started.md).

## Tests

```bash
uv run python manage.py test
```

Pull requests and pushes to `main` / `develop` run [`.github/workflows/ci.yml`](.github/workflows/ci.yml): Django tests, lockfile check, dependency audit (`pip-audit`), secret scan (gitleaks), markdown link check (lychee), a docs build against [shellui/shellui](https://github.com/shellui/shellui), and a Docker image build.

Pull requests **to `main`** also run the pre-release checklist ([`.github/workflows/pre-release.yml`](.github/workflows/pre-release.yml)).

## Releases

Releases are published by hand to Docker Hub as `shellui/email-service:<version>` and `latest`, for `linux/amd64` and `linux/arm64`. [PUBLISH.md](PUBLISH.md) has the checklist and the production settings. Changes are listed in [CHANGELOG.md](CHANGELOG.md).
