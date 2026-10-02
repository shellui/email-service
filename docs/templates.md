# Templates

Suggested templates ship in `apps/email/catalog.py` for every webhook event identity, storage, and hosting emit on `develop`. English and French. The admin app can leave them as-is or publish a company version.

Template keys are the sibling event ids (`identity.auth.magic_link.requested`, not a shortened alias). A service can post the same `event_type` string it already emits.

Login events `identity.auth.login.succeeded` and `identity.auth.login.failed` are event-log only upstream (`webhook: false`). They have no email template.

## Document

A template is a small block document, not raw HTML from the caller:

- `heading`
- `text`
- `button` (`text`, `href`)
- `footer`

Placeholders: `{{ token }}` and `{{ token|default:"fallback" }}`. Django template tags (`{%`) are rejected.

`EMAIL_RENDERER=python` (local and tests) renders those blocks to HTML. `EMAIL_RENDERER=node` (the Docker image) renders the same document with React Email in `renderer/render.mjs` (`@react-email/components`). Both renderers leave placeholders intact. Substitution happens at send time, with HTML escaping. URL tokens are checked before they are inserted.

Rendered bodies are not stored on the message and are not returned by status APIs. Sensitive variables stay encrypted until the provider accepts the message, then the ciphertext is cleared.

## Versions

`POST /api/v1/templates` copies the suggested document into a company draft. `POST /api/v1/templates/{id}/versions` saves another draft. `POST /api/v1/templates/{id}/versions/{number}/publish` renders it and sets `active_version`. Sends prefer:

1. The company's published version for the requested language
2. The company's published English version
3. A platform published version (staff, `company_id` null)
4. The suggested catalog document

`GET /api/v1/catalog` returns subjects and variable lists. `GET /api/v1/templates/defaults` returns the full documents.

## Variables

Each catalog entry lists tokens, types, and whether they are required. `description` is an i18n key (`email.var.<token>`), not a sentence, so clients can translate it.

`system.message_id`, `system.unsubscribe_url`, and `system.preferences_url` are filled by the worker. Callers must not send them.

Auth URL hosts are limited by `EMAIL_AUTH_LINK_HOSTS` (default `id.shellui.com`, `localhost`, `127.0.0.1`).
