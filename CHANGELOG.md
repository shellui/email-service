# Change Log

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/)
and this project adheres to [Semantic Versioning](https://semver.org/).

<!---
## [x.y.z] - yyyy-mm-dd

### ✨ Feature - for new features
### 🛠 Improvements - for general improvements
### 🚨 Changed - for changes in existing functionality
### ⚠️ Deprecated - for soon-to-be removed features
### 📚 Documentation - for documentation update
### 🗑 Removed - for removed features
### 🐛 Bug Fixes - for any bug fixes
### 🔒 Security - in case of vulnerabilities
### 🏗 Chore - for tidying code

See for sample https://raw.githubusercontent.com/favoloso/conventional-changelog-emoji/master/CHANGELOG.md
-->

## [0.9.0] - 2026-11-24

### ✨ Feature

- OpenTelemetry traces, metrics, and log correlation via `shellui-django[otel]==0.7.0`. `service.name` defaults to `email-service`. Export stays off until `OTEL_EXPORTER_OTLP_ENDPOINT` is set. Gunicorn, the Celery worker and beat, and `manage.py run_email_worker` (auth, transactional, and bulk) all call the setup. A message keeps the W3C `traceparent` of the request that queued it, so the provider send (Resend, Amazon SES, or SMTP) stays on that trace. Span attributes are lane, template id, provider, and status. Recipient addresses and message content are not recorded. Logs still carry `X-Request-ID` and now include `trace_id` and `span_id`.

### 🚨 Changed

- Prometheus gauges are now OpenTelemetry instruments with the same meaning. Names drop a trailing `_total` and use `.` instead of `_`. Queue depth, age, 24 hour send latency, provider errors, auth TTL expiries, fallback sent and refused, and the scheduled-job and lane-worker series are observable instruments read from the database and Redis. One process publishes each export. `company_id` is not a metric attribute. Per-company counts stay on `GET /api/v1/stats`.

### 🗑 Removed

- `GET /api/v1/metrics` and `GET /api/v1/metrics/all` (breaking). The `prometheus-client` dependency is gone. The `pat_agm` claim (`access_global_metrics`) is no longer read. No database column was dropped.

### 🛠 Improvements

- Shellui MCP on `shellui-django` 0.7.0 still verifies the bearer with this service's Identity JWKS check and the local HS256 fallback, and runs tools as the company owner on that token.

### 📚 Documentation

- `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`, `OTEL_EXPORTER_OTLP_PROTOCOL`, `OTEL_SERVICE_NAME`, and `OTEL_RESOURCE_ATTRIBUTES` are in the env example, Compose, and [Metrics](docs/metrics.md).

## [0.8.0] - 2026-11-18

### ✨ Feature

- Shellui MCP for company-owner tools at `POST /mcp`, with `GET /.well-known/oauth-protected-resource`. Agents use an Identity JWT whose audience is `{EMAIL_PUBLIC_URL}/mcp`. See [docs/mcp.md](docs/mcp.md).
- OpenAPI descriptions and `x-mcp` markers so agents can discover email tools. Only operations registered with `mcp_tool` are exposed.
- Template render-preview is available to MCP agents.
- MCP calls to Identity (revocation, approvals, and delegated authority) need `EMAIL_SERVICE_SIGNING_KEY` or `EMAIL_SERVICE_SIGNING_KEY_FILE`, and `EMAIL_SERVICE_ISSUER`. Without them, MCP fails closed and agents are refused. Mail delivery does not read these variables. The public key is at `GET /.well-known/jwks.json`.

### 🛠 Improvements

- MCP tool names match the ai-service gateway (`email_<verb>_<noun>`). OpenAPI operation ids stay `api_v1_*`.
- `tools/list` keeps registry metadata the ai-service gateway reads.
- Tool arguments are flat, with enums for fixed values. Method, path, and the request URL are not inputs.
- Deletes, sends, secret rotations, and other hard-to-undo tools always ask for a fresh approval.
- Delegated agents get live authority from identity-service. email-service does not create a second approval row for them.
- Production verifies admin JWTs from a cached `IDENTITY_JWKS_URL`, or from `{IDENTITY_SERVICE_URL}/.well-known/jwks.json`, when keys are not pinned.
- With `DEBUG=true`, templates allow http URLs for allowed hosts, including Tailscale and localhost.

### 🚨 Changed

- Docker Compose maps host port `8004` by default (was `8003`). The container still listens on `8000`. With `DEBUG=true` and no identity URL set, the default origin is `http://localhost:8001` (was `http://localhost:8000`).
- Dependabot version updates now open against develop.

### 📚 Documentation

- Docker Hub overview in `DOCKERHUB.md`. After the image push, the publish workflow updates that overview when the repository secrets `DOCKERHUB_DESCRIPTION_USERNAME` and `DOCKERHUB_DESCRIPTION_TOKEN` are set. Use a personal Docker Hub access token for those two secrets. Organization tokens are rejected. Image push still uses `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`. If the description secrets are unset, the overview step is skipped and the image push still succeeds.
- Local runs are documented against the Shellui dev stack in [docs/devstack.md](docs/devstack.md), including the dev service keys and the auth lane for identity's dev key.

### 🗑 Removed

- email-service no longer posts tool-call logs to identity-service. The ai-service gateway records `tools/call`. Token checks, revocation, and approvals still call identity-service. A direct `POST /mcp` does not write a call log.

### 🐛 Bug Fixes

- After an approval is consumed for the same agent, company, tool, and arguments, that call runs once and is not consumed again.
- `email_export_newsletter_subscribers` returns the CSV body. Read-safe mode still masks addresses in that text.
- Newsletter create requires a name. Webhook create and rotate results mask the signing secret.
- MCP revocation lists are verified with keys from `IDENTITY_JWKS_URL` when no JWKS is pinned.
- ReDoc renders without waiting for the Shellui SDK, so the API docs page is not blank.
- `POST /mcp` caches the OpenAPI schema under a lock, so overlapping calls do not return 500.

### 🏗 Chore

- Depend on `shellui-django[mcp]==0.5.1` from PyPI, and sign internal MCP assertions with this service's RS256 key.
- The Docker dev target registers the Shellui dev-stack service keys on start.
- The docs build fetches identity, storage, and hosting docs so links outside email still resolve.

## [0.7.1] - 2026-11-01

### 🐛 Bug Fixes

- Access log: every response carries `X-Request-ID`, access lines never include request headers, and a logging error no longer prints request headers or tokens to stderr.

## [0.7.0] - 2026-11-01

### ✨ Feature

- Company deletion: identity-service flags a deleted company and a Celery job purges all its email data every 60 seconds.

## [0.6.0] - 2026-10-27

### 🐛 Bug Fixes

- **API docs:** ReDoc and Swagger UI load again, served from the service's static files instead of a CDN.
- **Newsletter CORS:** The public sign-up endpoint answers CORS for any website, so browsers show the real `404` or `403` instead of a CORS error.
- **Email background:** The design's page color fills the whole email, and links without their own color use the text color instead of blue.

## [0.5.0] - 2026-10-26

### ✨ Feature

- **Email and webhooks rules:** Newsletter sign-up, confirm and unsubscribe events trigger webhooks and email rules, both filterable by list.
- **Newsletter metadata:** Sign-ups accept an optional `metadata` object, for example for demo requests.

### 🔒 Security

- **Security hardening:** Company-scoped Resend webhooks, verified SMTP TLS, redacted tokens in access logs, protected subscriber metadata and safer client IP handling.

## [0.4.0] - 2026-10-25

### ✨ Feature

- **Amazon SES:** Companies can send through the SES API, which works in regions without SMTP such as Zurich.
- **Fallback provider:** Opt-in `EMAIL_FALLBACK_PROVIDER` (Resend or SES, off by default) sends capped sign-in mail for projects without a provider.

## [0.3.0] - 2026-10-24

### ✨ Feature

- **Health check:** The image has a Docker `HEALTHCHECK` on `/health/live`, with no database or Redis check.
- **Docker Hub:** A published GitHub release pushes a multi-arch image to `shellui/email-service`.
- **generate_env:** `manage.py generate_env` prints the production env with a fresh `SECRET_KEY` and mail keys.

### 🛠 Improvements

- **Errors:** Error pages use the Shellui design, and API errors always return JSON.

### 🐛 Bug Fixes

- **Container stop:** a SIGTERM reaches each child process once, not twice.

### 🔒 Security

- `SECRET_KEY` shorter than 50 characters or a placeholder is refused when `DEBUG=false`.
- `certifi` 2026.10.19 fixes PYSEC-2026-4320.
- `djangorestframework` 3.18.2 fixes CVE-2026-114136, CVE-2026-114137 and CVE-2026-114138.
- **Service keys:** an empty `allowed_company_ids` list allows no company. Only an unset value allows every company.
- **JWT keys:** a token `kid` that is not in the JWKS is rejected, including when the set has one key.
- **Admin cookies:** Secure cookies are named `__Host-sessionid` and `__Host-csrftoken`, set no Domain, and the CSRF cookie is HttpOnly.

### 🏗 Chore

- `.env.example` is generated from `config/env_registry.py` and lists only the required settings.

## [0.2.0] - 2026-10-12

### ✨ Feature

- **Scheduled jobs:** staff read health and recorded runs at `GET /api/v1/scheduled-jobs`, with Prometheus series on `GET /api/v1/metrics/all`.
- **Lane worker heartbeats:** each lane worker reports its last poll on `GET /api/v1/scheduled-jobs`, and a lane goes stale after 60s.
- **Auth link hosts:** a company adds hostnames for auth mail with `PUT /api/v1/auth-link-hosts` or Allowed link hosts in Django admin, in addition to identity application domains and `EMAIL_AUTH_LINK_HOSTS`, and any other host is refused.

### 📚 Documentation

- **Handbook:** the guides in `docs/` are the product handbook, published at [docs.shellui.com/email](https://docs.shellui.com/email).

## [0.1.0] - 2026-10-06

### ✨ Feature

- **Direct send:** `POST /api/v1/send` and `/send/batch` deliver mail the caller must send (identity magic links, invitations, and the staff notice) on the auth lane, with TTL, idempotency, and hard-bounce suppression.
- **Event mail:** `POST /api/v1/events` queues one message per enabled company email rule, from a catalog of suggested identity, storage, and hosting events with English and French subjects. See [docs/events.md](docs/events.md).
- **Email rules:** several rules per event, each sending its own editable copy of a library design, and a built-in rule for auth-lane events that cannot be deleted or disabled. See [docs/rules.md](docs/rules.md).
- **Template library:** 40 React Email designs in five sets (Barebone, Matte, Protocol, Arcane, Studio) plus company templates, edited as React Email editor JSON and composed to HTML and text on save. See [docs/library.md](docs/library.md).
- **Translations:** one layout per template with a subject, preheader, and text per language; each send uses the recipient's language and falls back block by block. See [docs/templates.md](docs/templates.md).
- **Themes:** a library design can be repainted with the colors of a Shellui theme.
- **Self-hosted assets:** library images and fonts are served by email-service from `EMAIL_PUBLIC_URL`, so sent mail loads nothing from third parties.
- **Broadcasts:** one email to an identity audience filtered by groups, role, access, and activity, each recipient in their own language, through Resend Broadcasts or the bulk lane. See [docs/broadcasts.md](docs/broadcasts.md).
- **Newsletters:** public sign-up lists with double opt-in, rate limits, a honeypot, optional Cloudflare Turnstile, CSV import and export, and per-list unsubscribe. See [docs/newsletters.md](docs/newsletters.md).
- **Providers:** Resend (default) and SMTP, with per-company credentials encrypted at rest, masked responses, a test send, and a platform fallback for Shellui's own mail. See [docs/providers.md](docs/providers.md).
- **Delivery lanes:** separate auth, transactional, and bulk queues, each sent by its own worker (`run_email_worker --lane`). See [docs/lanes.md](docs/lanes.md).
- **All-in-one container:** the image applies migrations, then runs gunicorn, the three lane workers, and a Celery scheduler (`retry_webhooks` and `sweep_email_queue` every minute, `purge_expired_data` hourly, Redis-locked across replicas), each switchable off or movable to a `worker` container. See [docs/scheduled-jobs.md](docs/scheduled-jobs.md).
- **Job and worker monitoring:** every scheduled job run is recorded with its counts and a sanitized error, writes a staff-only `email.scheduled_job.succeeded` or `.failed` event, and feeds per-job health (`GET /api/v1/scheduled-jobs`) and Prometheus metrics, with a heartbeat per lane worker. See [docs/scheduled-jobs.md](docs/scheduled-jobs.md#monitoring).
- **Provider webhooks:** Resend delivery, bounce, complaint, and unsubscribe events update message status and the suppression list.
- **Shellui Actions:** outbound webhooks for `email.message.*`, unsubscribes, and newsletter events. See [docs/actions.md](docs/actions.md).
- **Admin API:** rules, templates, library, stats, suppressions, and privacy erase, documented in OpenAPI at `/api/docs/`.
- **Metrics:** Prometheus gauges for queue depth and age, send latency, provider errors, and auth TTL expiries at `GET /api/v1/metrics`. See [docs/metrics.md](docs/metrics.md).

### 🔒 Security

- **Sign-in links go only to the requester:** auth-lane mail takes exactly one recipient on `/send`, `/send/batch`, and `/events`, and companies cannot add rules or static recipients on auth-lane events.
- **No link leaks through designs:** auth-lane copies accept link variables in link targets only (`400 auth_link_misplaced`), and direct sends check the copy again at send time.
- **Staff notice:** staff accounts get the built-in `identity.auth.magic_link.staff_blocked` notice instead of a magic link, with Shellui's own copy that companies cannot edit.
- **Encryption at rest:** provider credentials and variables are encrypted with Fernet keys, and addresses are hashed with an HMAC pepper.
- **Sentry scrubbing:** error events never include request bodies, frame locals, or query strings.

### 📚 Documentation

- **Integration contract:** [docs/integration.md](docs/integration.md) describes the API other Shellui services call, with guides for configuration, security, and the Resend setup required before going live.
- **Docs site:** the guides in `docs/` are published at [docs.shellui.com/email](https://docs.shellui.com/email) by [shellui/shellui](https://github.com/shellui/shellui), and CI builds them and fails on broken links.
