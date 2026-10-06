# email-service

`email-service` sends mail for Shellui. Identity, storage, and hosting post events or direct sends. Companies bring their own provider credentials. A platform fallback covers Shellui's own mail when a company has none.

The integration contract is [docs/integration.md](docs/integration.md).

## Features

- Direct send (`POST /api/v1/send`) for auth mail: magic links, invitations, and the staff notice, with TTL and idempotency. Each sign-in link goes to one recipient only.
- Event ingest (`POST /api/v1/events`): every enabled email rule for that company, service, and event queues a message. Auth-lane events have a built-in rule.
- Template library: 40 React Email demo designs in five sets (Barebone, Matte, Protocol, Arcane, Studio), plus company templates. See [docs/library.md](docs/library.md) and `renderer/demos/LICENSE`.
- Each email rule sends its own editable copy of a library design, with suggested English and French subjects for identity, storage, and hosting events
- Provider adapters: Resend (default) and SMTP. Mailjet is the next adapter. See [docs/providers.md](docs/providers.md).
- Per-company credentials encrypted at rest. API responses return a masked hint and `configured`.
- Admin API for rules, templates, stats, test sends, and suppressions
- Broadcasts: one email to many company users, filtered by group, role, and activity, each in their own language. See [docs/broadcasts.md](docs/broadcasts.md).
- Newsletters: lists anyone can join from a website form, with a confirmation email (double opt-in), rate limits, a honeypot, and optional Turnstile. Issues are broadcasts sent to a list. See [docs/newsletters.md](docs/newsletters.md).
- Prometheus metrics at `GET /api/v1/metrics` (identity JWT, same scope as storage-service)
- Shellui Actions outbound webhooks for `email.message.*`
- OpenAPI at `/api/docs/` and `/api/docs/redoc/`

## Resend setup (required before going live)

> [!IMPORTANT]
> An API key alone is not enough. Without the steps below, bounced and complained addresses keep receiving mail, delivery statuses and broadcast counts never move past "handed to the provider", broadcasts fail outright, and unsubscribes made on Resend's page are never recorded in email-service.

Do this once for the platform Resend account Shellui sends from, and again for every company that brings its own Resend account (see [A company with its own Resend account](#a-company-with-its-own-resend-account)).

### 1. Verify two sending domains

In **Resend > Domains**, add both and publish the DNS records Resend shows (DKIM, plus SPF and MX for the return path). Resend refuses to send from a domain that is not verified.

| Domain | Sends | Setting |
| --- | --- | --- |
| Main domain, for example `shellui.com` | Sign-in links, invitations, notifications | `DEFAULT_FROM_EMAIL=no-reply@shellui.com` |
| News subdomain, for example `news.shellui.com` | Broadcasts and other bulk mail | `BULK_FROM_EMAIL=news@news.shellui.com` |

The subdomain is Resend's advice: spam complaints about news then do not hurt the reputation of sign-in mail.

### 2. Create a Full access API key

In **Resend > API Keys**, create a key with **Full access** and put it in `RESEND_API_KEY`.

> [!WARNING]
> A **Sending access** key sends single emails but cannot create the segments, contacts, and broadcasts that Resend Broadcasts need. Every broadcast then fails with `provider_unauthorized`.

### 3. Add the webhook

In **Resend > Webhooks**, add an endpoint:

- **URL:** `{PUBLIC_BASE_URL}/api/v1/provider-webhooks/resend/all`, for example `https://email.shellui.com/api/v1/provider-webhooks/resend/all`. The last segment is a free label stored on each event.
- **Events:** `email.sent`, `email.delivered`, `email.delivery_delayed`, `email.bounced`, `email.complained`, `email.failed`, `email.suppressed`, and **`contact.updated`**. `email.opened` and `email.clicked` are ignored, so leave them off.
- **Signing secret:** copy the `whsec_…` value into `RESEND_WEBHOOK_SECRET`.

| Events | What breaks without them |
| --- | --- |
| `email.sent`, `email.delivered`, `email.delivery_delayed`, `email.failed`, `email.suppressed` | Message and broadcast statuses stop at "handed to the provider". Statistics and the Shellui Actions `email.message.*` events are missing. |
| `email.bounced`, `email.complained` | Bad addresses and spam complaints are not suppressed and keep receiving mail, which hurts the domain's reputation. |
| `contact.updated` | Someone who unsubscribes with the link in a Resend broadcast is not added to the email-service unsubscribe list. |

Resend must reach that URL. A local email-service needs a tunnel, for example `cloudflared tunnel --url http://localhost:8003`, with the tunnel URL in the endpoint. Each delivery in the Resend webhook page shows the response: `200` is accepted, `401` means the signing secret does not match.

### 4. Leave tracking off

In each domain's settings, leave open and click tracking off. Click tracking rewrites every link through Resend, sign-in links included, and email-service ignores open and click events anyway.

### 5. Check the plan limits

Each broadcast adds its recipients as Resend contacts, in one segment per language named `<broadcast name> (<language>) #<id>`. Contacts count toward the Resend marketing plan limits and are not deleted after the send.

### email-service settings

```bash
EMAIL_FALLBACK_PROVIDER=resend
RESEND_API_KEY=re_...                        # Full access
RESEND_WEBHOOK_SECRET=whsec_...
DEFAULT_FROM_EMAIL=no-reply@shellui.com      # verified main domain
BULK_FROM_EMAIL=news@news.shellui.com        # verified news subdomain
BULK_FROM_NAME=Shellui
EMAIL_PLATFORM_COMPANY_IDS=1                 # companies allowed to send from these addresses
PUBLIC_BASE_URL=https://email.shellui.com    # webhook host and email-service unsubscribe links
IDENTITY_SERVICE_URL=https://id.shellui.com  # broadcast audiences
```

Run the bulk worker (`run_email_worker --lane bulk`). Without it, broadcasts stay queued.

### A company with its own Resend account

The company repeats steps 1 to 5 on its own account, then:

- Saves its Full access key, From address, and Bulk From address in the admin under **Email > Provider**, with provider **Resend**.
- Points its Resend webhook at the URL with its company id: `https://email.shellui.com/api/v1/provider-webhooks/resend/all?company_id=42`. That URL only accepts the company's own signing secret, never the platform one.
- Stores the signing secret through the API. The admin form has no field for it yet:

```bash
curl -X PUT "https://email.shellui.com/api/v1/provider?company_id=42" \
  -H "Authorization: Bearer <identity JWT of a staff member or the company owner>" \
  -H "Content-Type: application/json" \
  -d '{"provider": "resend", "from_email": "no-reply@acme.com", "webhook_secret": "whsec_..."}'
```

`from_email` is required on every `PUT`. The stored API key is kept when `credentials` is omitted.

## Project structure

- `config/` Django settings and URL routing
- `apps/authapi/` JWKS JWT authentication and service keys
- `apps/email/` catalog, queue, rules, stats
- `apps/providers/` Resend, SMTP, and the planned Mailjet module
- `apps/actions/` Shellui Actions outbox
- `defaults/` exported catalog events (subjects, preheaders, link and default design)
- `docs/` guides, including the integration contract
- `renderer/` React Email compose script (`compose.mjs`), library seeds, and the vendored demos with `renderer/demos/LICENSE`

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
uv run python manage.py run_email_worker --lane auth
uv run python manage.py run_email_worker --lane transactional
uv run python manage.py run_email_worker --lane bulk
uv run python manage.py retry_webhooks
uv run python manage.py purge_expired_data
```

Keep one resident worker per lane. The bulk worker also sends broadcasts. Schedule `retry_webhooks` every minute and `purge_expired_data` every hour. Leave `EMAIL_DELIVER_SYNC=false` outside tests.

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

The container applies migrations on start, then runs gunicorn. Workers use the same image with their own command, for example `docker run … shellui/email-service python manage.py run_email_worker --lane auth`.

## Release

Releases are published by hand to Docker Hub as `shellui/email-service:<version>` and `latest`, for `linux/amd64` and `linux/arm64`. [PUBLISH.md](PUBLISH.md) has the pre-release checklist, the build and push commands, the production settings, and how to connect identity-service. Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## Documentation site

Guides in `docs/` are built with Docusaurus:

```bash
./tools/generate-docs.sh
```

GitHub Pages uses the CNAME `email.docs.shellui.com` so it does not take over the API host `email.shellui.com`.
