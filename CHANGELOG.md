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
