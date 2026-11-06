# Change Log

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/)
and this project adheres to [Semantic Versioning](https://semver.org/).

<!---
## [Unreleased] - yyyy-mm-dd

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

## [Unreleased]

### 🛠 Improvements

- MCP: Identity approval consume accepts only JSON boolean `true` for `consumed`; HTTP `409` already consumed maps to the same path as approval state ``expired`` (until Identity PR #136 finalizes consume semantics).
- MCP: approval state extras for ``approval_request_id`` use a request-scoped context (``EmailMcpServer._tools_call``) instead of a module global.
- MCP: call log ingest ``access_kind`` uses audit ``tool_destructive`` so destructive OpenAPI tools never forward as ``write`` (covers outcomes such as ``session_used``).
- MCP: optional global delegation claims on agent JWTs are checked when Identity includes a snapshot row for that agent; strict `build_mcp_approval_assertion` kwargs for personal vs delegated agents.
- MCP: call logs to Identity use the ingest shape from ``McpInternalCallLogView`` in identity-service (`POST /mcp` canonical path, `status` for HTTP status, `result` empty; no `company_name`, `arguments`, or `arguments_hash` in the POST body). Local audit rows still carry OpenAPI template paths; forwarding maps those to Identity’s canonical MCP route per `call_log_internal.canonical_call_path`.
- MCP: stricter agent JWT numeric claims (no string `company_id`, no bool coercion) and revocation binding (delegated agents without a snapshot row or ambiguous duplicate rows are forbidden).
- MCP: delegated agents call Identity `POST …/mcp-agent-delegated-authority` with the JWT triple; internal assertion uses `aud` `shellui-mcp-agent-delegated-authority` and scope `mcp.agent.delegated.authority.read` (`target` = Identity issuer). Live authority responses use Identity ``_delegated_authority_fixed_payload`` (15 JSON keys on every HTTP `200`) at [`d7fe351`](https://raw.githubusercontent.com/shellui/identity-service/d7fe351/apps/agents/internal_views.py). The live row is matched to the JWT on `access_mode`, `read_level`, **`global_agent_id`** (must equal live SaaS catalog id), exact **set** equality between normalized `scope` (space-delimited) and `services_allowed`, and `allow_destructive` vs `mcp_destructive`. HTTP `404` maps to `403 authority_binding_missing`. HTTP `401` on the delegated call is a **service assertion** problem and maps to `503 identity_unavailable` (not an agent deny). Revocation snapshots match delegated rows on `agent_id` only (no `id` aliases). Agent JWTs must include `grant_version` and **`mcp_destructive`** (boolean only, no default); delegated tokens must include **`global_agent_id`**. Call-log ingest omits the POST when `grant_version` is missing (no invented `0`). Call logs forward `grant_version` on every ingest POST and include `global_company_id` and `company_binding_version` for delegated agents; rows still forward after a policy-denied live check by rewriting to `outcome` `denied` with `authority_binding_missing` or `authority_mismatch` as appropriate; approval create bodies stay `{}` (fingerprints live in the service assertion only). Delegated approval live checks merge **`verified_agent_claims`** (normalized JWT copy from the gateway) with assertion fields; `forward_call_log` uses the same map for the live delegated check (not echo fields from the outbound entry). Delegated agents do not create approval rows in email-service (refused before Identity `POST`; no redundant live verify on that path). Destructive OpenAPI tools keep **`access_kind`** `destructive` on audit and forwarded rows (approval linkage when present uses `approval_request_id` / Identity `approval_id`). Delegated approval Identity responses with HTTP `409` and `error_code` `out_of_sync` surface as MCP HTTP `409` (routed via AI, no local fallback). Identity-ingest outcomes use `waiting_approval` where Identity expects that canonical label.
- MCP: destructive tools always require a fresh human approval per call, even when `mcp_destructive` is true.
- MCP: stable machine codes on internal exceptions to Identity.
- MCP: `shellui-django` pinned to django-commons `develop` git commit `1f0754b421688ed26b7eb2e76b25b6fa3eea2a37` (`pyproject.toml` / `uv.lock`).

## [0.8.0] - 2026-11-01

### 🛠 Improvements

- Bump `shellui-django[mcp]` to 0.4.0 and sign internal MCP assertions to Identity with this service's own RS256 key (`EMAIL_SERVICE_SIGNING_KEY`, `EMAIL_SERVICE_ISSUER`), public key at `/.well-known/jwks.json`.
- Docker `dev` target for the Shellui local stack (bind-mounted source, `runserver` on port 8000); production publish unchanged.
- Local host port **8004** and [Shellui dev stack](docs/devstack.md) env documentation (identity issuer public, JWKS and SMTP via compose DNS).
- `register_dev_service_keys` registers the dev stack's service keys from `EMAIL_DEV_SERVICE_KEYS` on start (DEBUG only).

- Production may verify admin JWTs from a cached `IDENTITY_JWKS_URL` fetch when keys are not pinned.

### ✨ Feature

- MCP endpoint for Shellui agents at `POST /mcp`.
- OpenAPI schema tags for MCP tool discovery.
- Template render-preview exposed to MCP agents.
- OpenAPI descriptions for admin API operations.

### 🏗 Chore

- Use the shared shellui-django package for MCP instead of a vendored copy.

### 🐛 Bug Fixes

- MCP revocation lists are verified with keys from `IDENTITY_JWKS_URL` when no JWKS is pinned, instead of the public issuer URL.

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
