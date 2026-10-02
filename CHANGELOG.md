# Change Log

Notable changes to this project. Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: [Semantic Versioning](https://semver.org/).

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
