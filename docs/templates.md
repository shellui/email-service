# Templates

Suggested templates ship in `apps/email/catalog.py` for every webhook event identity, storage, and hosting emit on `develop`. English and French. A company can leave them unused, start a rule from one, or publish its own version.

Catalog template keys are the sibling event ids (`identity.auth.magic_link.requested`, not a shortened alias). Direct `POST /api/v1/send` still accepts those keys. A company template created from a rule gets a generated key, `company.` plus 12 hex characters, plus a plain-text `name` (max 120) and an optional `event_type` (the event it was created for).

Login events `identity.auth.login.succeeded` and `identity.auth.login.failed` are event-log only upstream (`webhook: false`). They have no email template.

## Document

A template is a small block document, not raw HTML from the caller:

- `heading`
- `text`
- `button` (`text`, `href`)
- `footer`

Suggested catalog documents are minimal: one heading, one short paragraph, and a button only when the event carries an action URL (magic link, invitation). No footer on those suggested documents. Company drafts may still use a footer.

Placeholders: `{{ token }}` and `{{ token|default:"fallback" }}`. Django template tags (`{%`) are rejected.

The product name in suggested copy is Shellui.

`EMAIL_RENDERER=python` (local and tests) renders those blocks to HTML. `EMAIL_RENDERER=node` (the Docker image) renders the same document with React Email in `renderer/render.mjs`, using the Tailwind templates in `renderer/email.mjs`. Both renderers take a theme key (`barebone`, `matte`, `protocol`, `arcane`, `studio`) and leave placeholders intact. An optional `theme_palette` overrides the theme colors. `{}` keeps the theme palette. Substitution happens at send time, with HTML escaping. URL tokens are checked before they are inserted.

Theme layout, the MIT notice, and the settings API are in [themes.md](themes.md).

Rendered bodies are not stored on the message and are not returned by status APIs. Sensitive variables stay encrypted until the provider accepts the message, then the ciphertext is cleared.

## Versions

`POST /api/v1/templates` with a catalog `template_key` copies the suggested document into a company draft. The row's `name` is the event label and `event_type` is that key. The draft's `theme_name` is the company theme (`barebone` when the company has not chosen one).

Creating a rule with `content.mode: suggested` does the same copy and publishes it, so the rule can send immediately. The editor can still add drafts on that template.

`POST /api/v1/templates/{id}/versions` saves another draft (`subject`, `preheader`, `document`, optional `theme_name` and `theme_palette`). An omitted `theme_name` uses the company theme. An unknown `theme_name` is `400 theme_unknown`. `GET` on the version list or one version returns those fields so the editor can reopen the company copy. `POST .../publish` renders it in the stored theme and sets `active_version`.

`GET /api/v1/templates?event_type=` returns only company templates whose tokens fit that event. Every `{{ token }}` must be declared on the event, or be `recipient_email`, or start with `system.`. The same check runs when a rule is saved (`400 template_variables_mismatch`, plus `missing_variables`).

List and detail items include `name`, `event_type`, `theme`, and `uses_company_theme`. `theme` is the active published version's theme, otherwise the latest version. `uses_company_theme` is false when that theme differs from the company setting, which is the badge for a mismatch warning.

`DELETE /api/v1/templates/{id}` returns `204`, or `409 template_in_use` when a rule still points at the template.

Direct sends by catalog key prefer:

1. The company's published version for the requested language
2. The company's published English version
3. A platform published version (staff, `company_id` null)
4. The suggested catalog document

Event ingest sends the rule's company template. An unedited suggested document follows the requested language. After the subject, preheader, or document is edited, that stored document is what sends.

`GET /api/v1/catalog` returns subjects and variable lists. `GET /api/v1/templates/defaults` returns the full documents.

## Variables

Each catalog entry lists tokens, types, and whether they are required. `description` is an i18n key (`email.var.<token>`), not a sentence, so clients can translate it.

`system.message_id` is filled by the worker. On non-auth mail, `system.unsubscribe_url` and `system.preferences_url` are the signed `POST /u/{token}` URL for that recipient. Auth mail does not set them. Callers must not send these tokens.

Auth URL hosts are limited by `EMAIL_AUTH_LINK_HOSTS` (default `id.shellui.com`, `localhost`, `127.0.0.1`).
