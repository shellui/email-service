# Change Log

Notable changes to this project. Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- Email rules are a list per company. Several rules may share one event. The per-event toggle is removed.
- Auth-lane events (`identity.auth.magic_link.requested`, `identity.user.invited`) get a built-in rule that cannot be deleted or disabled.
- `POST /api/v1/events` with no enabled rule returns `skipped_reason: no_rule`. Catalog `default_enabled` no longer sends mail.
- Company templates have `name`, optional `event_type`, and a generated `company.<hex>` key when created from a rule.
- Themes: `barebone`, `matte`, `protocol`, `arcane`, `studio`. `GET /api/v1/themes`, `GET /api/v1/themes/{key}/preview`, `GET/PUT /api/v1/settings`. Versions stored as `shellui` migrate to `barebone`.

## [0.1.0] - 2026-10-02

### Feature

- First email-service release: Django API for direct sends and event-driven mail
- Provider adapters for Resend (default) and SMTP, with a documented Mailjet slot
- Per-company encrypted provider credentials, masked responses, and a test-send endpoint
- Platform fallback provider from the environment for Shellui's own mail
- Suggested English and French templates for identity, storage, and hosting webhook events
- Company email rules, catalog, template versions, and per-company stats
- Prometheus gauges for queue depth, queue age, send latency, provider errors, and auth TTL expiries
- Shellui Actions outbound webhooks for `email.message.*` and unsubscribe
- Auth lane for magic links and invitations (TTL, idempotency, hard-bounce suppression)
- Integration contract in [docs/integration.md](docs/integration.md)
