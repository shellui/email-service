# email-service

`email-service` sends mail for Shellui. Identity, storage, and hosting post events or direct sends. Companies bring their own provider credentials. A platform fallback covers Shellui's own mail when a company has none.

The integration contract is [docs/integration.md](docs/integration.md).

## Features

- Direct send (`POST /api/v1/send`) for auth mail: magic links and invitations, with TTL and idempotency
- Event ingest (`POST /api/v1/events`): a company email rule chooses the template, the language, and whether to send
- Suggested English and French templates for identity, storage, and hosting webhook events
- Provider adapters: Resend (default) and SMTP. Mailjet is the next adapter. See [docs/providers.md](docs/providers.md).
- Per-company credentials encrypted at rest. API responses return a masked hint and `configured`.
- Admin API for rules, templates, stats, test sends, and suppressions
- Prometheus metrics at `GET /api/v1/metrics` (identity JWT, same scope as storage-service)
- Shellui Actions outbound webhooks for `email.message.*`
- OpenAPI at `/api/docs/` and `/api/docs/redoc/`

## Project structure

- `config/` Django settings and URL routing
- `apps/authapi/` JWKS JWT authentication and service keys
- `apps/email/` catalog, queue, rules, stats
- `apps/providers/` Resend, SMTP, and the planned Mailjet module
- `apps/actions/` Shellui Actions outbox
- `defaults/` exported suggested templates
- `docs/` guides, including the integration contract
- `renderer/` React Email renderer used when `EMAIL_RENDERER=node`

## Main endpoints

| Area | Path |
| --- | --- |
| Health | `GET /api/v1/health` |
| Send | `POST /api/v1/send`, `POST /api/v1/send/batch` |
| Events | `POST /api/v1/events` |
| Messages | `GET /api/v1/messages`, `GET /api/v1/messages/{id}`, `POST /api/v1/messages/{id}/cancel` |
| Catalog | `GET /api/v1/catalog` |
| Provider | `GET/PUT /api/v1/provider`, `POST /api/v1/provider/test-send` |
| Rules | `GET/POST/PATCH /api/v1/rules` |
| Templates | `/api/v1/templates` |
| Stats | `GET /api/v1/stats` |
| Metrics | `GET /api/v1/metrics` |
| Shellui Actions | `/api/v1/actions/*` |
| OpenAPI | `/api/docs/`, `/api/docs/redoc/` |

Service calls use `Authorization: Bearer <esk_ key>`. Admin calls use an identity-service JWT.

## Local setup

```bash
# Requires https://docs.astral.sh/uv/
uv sync
cp .env.example .env
# Set SECRET_KEY. Local identity JWKS URL is already in .env.example
npm ci && npm run build:css
uv run python manage.py migrate
uv run python manage.py runserver 8003
```

The landing page (`templates/home.html`) uses Tailwind compiled into `static/css/site.css`. With `DEBUG=true`, `runserver` runs `npm run build:css` once at startup (and runs `npm ci` if `node_modules` is missing). For live CSS edits, use a second terminal: `npm run watch:css`.

Open `http://localhost:8003/`. With `DEBUG=true` (local default), an empty database shows a one-time web form there to create the first superuser. In production (`DEBUG=false`), leave `SETUP_TOKEN` empty and create that user with `uv run python manage.py createsuperuser`.

Dependencies live in `pyproject.toml` and are locked in `uv.lock`.

### Identity wiring

Admin tokens come from identity-service. In production, copy the public JWKS once:

```bash
curl -sS https://id.shellui.com/.well-known/jwks.json
```

```bash
IDENTITY_JWKS={"keys":[]}
IDENTITY_ISSUER=https://id.shellui.com
IDENTITY_AUDIENCE=shellui
```

Local development can use `IDENTITY_SERVICE_URL=http://localhost:8000`. For identity `DEBUG` HS256, set `JWT_HS256_FALLBACK_SECRET` to the same `SECRET_KEY` as identity-service.

### Workers

HTTP and delivery are separate processes:

```bash
uv run python manage.py run_email_worker
uv run python manage.py retry_webhooks
uv run python manage.py purge_expired_data
```

Schedule `retry_webhooks` every minute and `purge_expired_data` every hour. Leave `EMAIL_DELIVER_SYNC=false` outside tests.

### Service key

```bash
uv run python manage.py create_service_key --service identity --lanes auth,transactional --prefixes identity.
```

Put the printed `esk_` value in the caller as `EMAIL_SERVICE_API_KEY`. `EMAIL_SERVICE_URL` defaults to `https://email.shellui.com`.

## Docker

```bash
cp .env.example .env
docker compose up --build
```

Host port: `8003` (container listens on `8000`). Identity uses 8000, storage 8001, and hosting 8002.

Publishing the image is manual. See [PUBLISH.md](PUBLISH.md).

## Documentation site

Guides in `docs/` are built with Docusaurus:

```bash
./tools/generate-docs.sh
```

GitHub Pages uses the CNAME `email.docs.shellui.com` so it does not take over the API host `email.shellui.com`.
