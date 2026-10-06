# Change Log

Notable changes to this project. Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Security

- A sign-in or invitation link only goes to the address in the request. `POST /api/v1/rules` refuses auth-lane events (`400 auth_event_rule_forbidden`), built-in rules can no longer switch to `static` recipients or list addresses (`409 rule_built_in`), and the catalog marks auth-lane events `rules_allowed: false`. `POST /api/v1/events` sends an auth-lane event through its built-in rule only, to the event's recipient, and auth-lane events and `/send` take exactly one recipient (`400 auth_single_recipient`). Migration `0011_auth_rules_locked` resets built-in rules to `hints` and disables company rules already written on auth-lane events.
- Auth-lane copies cannot put a link variable in an image `src`, a `style`, an `alt`, or any attribute other than a link target (`400 auth_link_misplaced`). Before, a company owner could publish an image whose URL carried `{{ magic_link_url }}`, so the mail client or image proxy of whoever signed in sent the live sign-in link to a host the owner chose. Direct sends use only the built-in copy, check it again at send time, and fall back to the default design.
- New built-in auth email `identity.auth.magic_link.staff_blocked`. Identity no longer sends magic links to staff accounts (a company that sends through its own provider can read every link in that provider's logs), and sends this notice to the address instead. It says magic links are off for staff accounts and to sign in with their usual sign-in method. It has no sign-in link or token, only a plain link to the sign-in page (`sign_in_url`). The copy lives in `apps/email/builtin_auth.py` in English and French: companies cannot edit it, it gets no built-in rule, `POST /api/v1/rules` refuses it, `POST /api/v1/events` never sends it (`skipped_reason: no_rule`), and the catalog does not list it. Direct `/send` takes exactly one recipient, like other auth mail, under the same auth-lane rate limits.
- `POST /api/v1/send/batch` on an auth-lane template (magic link, invitation, staff notice) takes exactly one item with one `to` address, like `/send`, and returns `400 auth_single_recipient` otherwise with nothing queued. Before, a batch could send the same auth mail to several addresses.
- Sentry no longer receives request bodies, frame locals, or query strings, so a `/send` body or the rendered HTML of a sign-in email cannot end up in an error event.

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
- Broadcasts (`/api/v1/broadcasts`, see `docs/broadcasts.md`): a company writes one email from a library design and sends it to an audience read from identity (`GET /api/v1/users/audience` with the caller's JWT), by groups, role, access, join and last-seen dates, or picked users and addresses. Each recipient gets their identity language when the content has it. Unsubscribed and suppressed addresses are skipped. Companies on Resend send through Resend Broadcasts, one segment and one broadcast per language, throttled by `EMAIL_RESEND_BROADCAST_RPS`. Other providers send bulk-lane messages. Broadcasts send from the Bulk From address (`BULK_FROM_EMAIL` for Shellui companies), else `409 bulk_sender_required`. The bulk worker and `sweep_email_queue` move them forward. Resend webhooks map `broadcast_id` events to recipients, and `contact.updated` unsubscribes to the bulk unsubscribe list. New settings `EMAIL_BROADCAST_MAX_RECIPIENTS`, `EMAIL_RESEND_BROADCAST_RPS`, `EMAIL_BROADCAST_STEP_SECONDS`. Templates gain `kind` (`event` or `broadcast`), and the template list leaves broadcast content out. Migration `0009_broadcasts`.
- Newsletters (`/api/v1/newsletters`, see `docs/newsletters.md`): companies create lists anyone can join from a website through `POST /api/v1/public/newsletters/{public_key}/subscribe`. Sign-ups get a confirmation email (`kind: newsletter_confirmation`, editable, English and French) and are subscribed once they press the button on `/n/confirm/<token>` (opening the link alone does not confirm). The endpoint always answers `202`, has a honeypot, per-IP, per-address, and per-list rate limits, optional Cloudflare Turnstile, and per-list allowed origins answered through CORS. Admins add, import (`confirm` or `consented`), export as CSV, and remove subscribers. Broadcasts send to confirmed subscribers with the audience `{"mode": "newsletter", "list_id"}`. On the bulk lane the unsubscribe link leaves that list only. A company-wide unsubscribe, including Resend's `contact.updated`, leaves every list. Events `email.newsletter.confirmed` and `email.newsletter.unsubscribed`. `purge_expired_data` deletes unconfirmed sign-ups and clears addresses of unsubscribed people. Privacy erase deletes subscribers. The unsubscribe and confirmation pages share a styled template. New settings `EMAIL_NEWSLETTER_*`, `EMAIL_CLIENT_IP_HEADER`, `EMAIL_TURNSTILE_VERIFY_URL`. Migration `0010_newsletters`.
- README and PUBLISH list the Resend setup required before going live: verified main and news domains, a Full access API key, the webhook and its events (`contact.updated` included), and tracking off. Workers are documented one per lane (`--lane` is required). Compose passes `RESEND_WEBHOOK_SECRET`, `EMAIL_PLATFORM_COMPANY_IDS`, and `BULK_FROM_NAME` through.

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
