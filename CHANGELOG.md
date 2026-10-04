# Change Log

Notable changes to this project. Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- Email rules are a list per company. Several rules may share one event. The per-event toggle is removed.
- Auth-lane events (`identity.auth.magic_link.requested`, `identity.user.invited`) get a built-in rule that cannot be deleted or disabled.
- `POST /api/v1/events` with no enabled rule returns `skipped_reason: no_rule`. Catalog `default_enabled` no longer sends mail.
- Company templates have `name`, optional `event_type`, and a generated `company.<hex>` key when created from a rule.
- Themes: `barebone`, `matte`, `protocol`, `arcane`, `studio`. `GET /api/v1/themes`, `GET /api/v1/themes/{key}/preview`, `GET/PUT /api/v1/settings`. Versions stored as `shellui` migrate to `barebone`.
- Block documents support inline formatting and two new blocks. `heading`, `text`, `footer`, and list items take optional `content` runs with `bold`, `italic`, `underline`, and `href`. New `list` (bulleted or `ordered`) and `divider` blocks. Both renderers output them, inline links with an unsafe scheme render as plain text, and auth-lane checks apply the button link rules to inline links. Python renderer version is `shellui-email-3`.
- The node renderer's plain-text part keeps heading case, so `{{ token }}` in a heading still substitutes.
- `{{ token|default:"…" }}` substitutes in node-rendered HTML. React Email writes the quotes as `&quot;`, which the HTML substitution now accepts. Before, the placeholder was sent as written.
- The node renderer builds the five templates with React Email and Tailwind in `renderer/email.mjs`. Colors are Tailwind tokens filled from the theme or `theme_palette`, so any palette works on any template. The Shellui admin vendors the same file for its live preview. Arcane's outline button now has padding (`button_pad` `12px 20px`). Barebone keeps a 16px card-colored frame around its inset panel, as in the React Email demo, so the white card shows again (and the background color of a custom palette).

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
