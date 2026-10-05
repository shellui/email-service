# Change Log

Notable changes to this project. Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- Email rules are a list per company. Several rules may share one event. The per-event toggle is removed.
- Auth-lane events (`identity.auth.magic_link.requested`, `identity.user.invited`) get a built-in rule that cannot be deleted or disabled.
- `POST /api/v1/events` with no enabled rule returns `skipped_reason: no_rule`. Catalog `default_enabled` no longer sends mail.
- Company templates have `name`, optional `event_type`, and a generated `company.<hex>` key when created from a rule.
- Template library: 40 built-in designs vendored from the React Email demos (Barebone, Matte, Protocol, Arcane, Studio sets, MIT, `renderer/demos/LICENSE`), imported to editor JSON by `npm run import:demos` and synced on migrate. Companies add their own templates with `GET/POST /api/v1/library` and `GET/PUT/DELETE /api/v1/library/{id}`. Built-ins are read-only (`409 library_built_in`).
- `POST /api/v1/rules` takes `content.library_id` and creates the rule's own copy, published, with the catalog subject and preheader and `{{ action_url }}` mapped to the event link. Deleting a rule deletes its copy. `PATCH` no longer accepts `template_id`.
- Documents are React Email editor JSON, checked against a node, mark, link, and image allowlist. `renderer/compose.mjs` composes HTML and text on every save, and sends substitute variables into the stored HTML. `POST /api/v1/templates/{id}/versions` with `library_id` starts a copy over and keeps its subject and preheader.
- Catalog events carry `link_token` and `default_template`. Built-in auth rules and direct sends with no copy use the default design.
- New settings `EMAIL_PUBLIC_URL`, `EMAIL_NODE_BINARY`, `EMAIL_COMPOSE_TIMEOUT_SECONDS`, `EMAIL_MAX_DOCUMENT_BYTES`. React is pinned to 18.3.1 to match the admin preview.
- `{{ token|default:"…" }}` substitutes in composed HTML, where React Email writes the quotes as `&quot;`.
- Template versions carry `translations`: per language, a subject, a preheader, and the text of blocks matched by `attrs.textId`, over one shared layout. Every language is validated, composed on save, and checked on publish. Sends use the send language's translation, falling back to the main text block by block. Migration `0006_template_translations`.
- Email themes: library design colors are stored as theme roles (`var(--email-<role>,<original>)`, see `apps/email/theming.py`). Template versions and company library templates carry `theme` (`{name, label, colors}` or `{}`), and composing resolves the roles to the theme's colors or the originals, so sent HTML is unchanged without a theme. `POST /api/v1/rules` takes `content.theme`, used when the library template has none. Versions keep the latest theme when `theme` is omitted. Send-test takes `theme` with a document. Migration `0007_email_themes` tokenizes existing rows and copies.
- Library fonts are served by email-service from `static/library/fonts/` (`{{ system.assets_url }}/fonts/…`) instead of Google Fonts, so sent mail loads every asset from `EMAIL_PUBLIC_URL`. `tools/host-fonts.mjs` copies them, and the demo import runs it. The Inter 400 file of Arcane, Matte, and Protocol no longer exists on Google and is replaced by the one the other sets use. Migration `0008_hosted_fonts` clears composed HTML so library rows and copies compose again with the new heads. WhiteNoise also serves static files under `runserver`, with the CORS header fonts need, and `.ttf` as `font/ttf`.

### Removed

- Themes, theme palettes, and company settings (`/api/v1/themes`, `/api/v1/settings`), block documents and the block renderers, `EMAIL_RENDERER`, `POST /api/v1/render`, `POST /api/v1/templates`, and `GET /api/v1/templates/defaults`.
- Migration `0005_library_templates` deletes existing rules, company templates, and versions.

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
