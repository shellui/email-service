# Templates

An event email is a company template: a copy of a [library](library.md) design made for one event. It is created with an email rule, edited in the admin, and deleted with its rule. Copies are not listed in the library, and a copy never changes when the library template it came from changes.

Catalog events live in `apps/email/catalog.py` for every webhook event identity, storage, and hosting emit on `develop`. Each event carries a suggested subject and preheader in English and French, its variables, `link_token` (the URL variable its link uses, empty when it has none), and `default_template` (the library key used when nothing else is chosen). The body always comes from a library design.

Catalog keys are the sibling event ids (`identity.auth.magic_link.requested`, not a shortened alias). Direct `POST /api/v1/send` accepts those keys. A copy gets a generated key, `company.` plus 12 hex characters, a `name` (the event label, max 120), its `event_type`, `source_key` (the library key it came from), and `set` (whose `head.css` it uses).

Login events `identity.auth.login.succeeded` and `identity.auth.login.failed` are event-log only upstream (`webhook: false`). They have no email.

## Document

A document is React Email editor JSON (TipTap), not raw HTML from the caller:

```json
{"type": "doc", "content": [{"type": "container", "attrs": {"style": "max-width:600px"}, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hi {{ company_name }}"}]}]}]}
```

`validate_document` checks every save:

- The root is `{"type": "doc"}` and the JSON is at most `EMAIL_MAX_DOCUMENT_BYTES` (512 KB): `invalid`, `too_large`.
- Nodes are the editor's text, list, table, layout (`container`, `section`, `div`, columns), `button`, and `image` nodes: `node_not_allowed`. Marks are `link`, `bold`, `italic`, `underline`, `strike`, `code`, `sup`, `uppercase`, and `preservedStyle`: `mark_not_allowed`.
- No attribute holds `javascript:`, `vbscript:`, `expression(`, or `data:text/html`: `unsafe_attribute`.
- Links (link marks, button and image `href`) are `https`, `mailto`, `tel`, or a `{{ token }}`: `unsafe_link`.
- Image sources are `https` or `{{ system.assets_url }}/…`: `unsafe_image`.
- Django template tags (`{%`, `{#`) are rejected anywhere: `template_tags_forbidden`.

The codes come back as `400 validation_failed` with `field_errors.document`.

Placeholders: `{{ token }}` and `{{ token|default:"fallback" }}`.

## Composing and sending

`renderer/compose.mjs` composes a document with the React Email editor's `composeReactEmail`, headless in Node (`EMAIL_NODE_BINARY`). It uses the node set in `renderer/editor.mjs` and the set's `head.css`, and returns HTML and plain text with placeholders intact. Python calls it on every library save and every version save, and stores the result on the row. The admin composes the same document in the browser for its preview, with the same node set and the same package versions (React 18.3.1, `react-email` 6.9.3, `@react-email/editor` 1.7.3), so the preview matches the stored HTML byte for byte. Keep those versions in step on both sides.

Sending does not compose. It substitutes variables into the stored HTML and text, with HTML escaping in the HTML part. URL tokens are checked before they are inserted.

Rendered bodies are not stored on the message and are not returned by status APIs. Sensitive variables stay encrypted until the provider accepts the message, then the ciphertext is cleared.

## Copies and versions

`POST /api/v1/rules` with `content.library_id` creates the copy and its first version, published, so the rule sends at once. The copy takes:

- the library document, with `{{ action_url }}` replaced by the event's link variable (or `https://example.com` when the event has none);
- on auth-lane events, only the links the auth checks accept (see [security.md](security.md#links-in-auth-mail)), so the copy publishes as is;
- the catalog subject and preheader in the rule's language (English when the rule has none).

`POST /api/v1/templates/{id}/versions` adds a draft. Send `subject`, `preheader`, and `document`, or `library_id` to start over from another library template: the document is replaced (adapted to the event the same way) and the subject and preheader stay. The copy's `source_key` and `set` follow the new template. `201` `{number, state: "draft"}`.

`GET /api/v1/templates/{id}/versions` and `GET /api/v1/templates/{id}/versions/{number}` return `{number, state, subject, preheader, document, published_at}`. `POST /api/v1/templates/{id}/versions/{number}/publish` runs the lane checks and sets `active_version`.

`POST /api/v1/templates/{id}/send-test` sends the posted `subject`, `preheader`, and `document` (composed on the fly) or, without a document, the latest draft, to the caller or to `to` for staff.

`GET /api/v1/templates?event_type=` lists the company's copies, optionally only those whose tokens fit that event. Every `{{ token }}` must be declared on the event, or be `recipient_email`, or start with `system.`. List and detail items carry `name`, `event_type`, `language`, `active_version`, `source_key`, `set`, and `head`.

`DELETE /api/v1/templates/{id}` returns `204`, or `409 template_in_use` when a rule still points at the copy. Deleting a rule deletes its copy.

## Which copy sends

Event ingest sends each enabled rule's copy. Direct sends by catalog key use, in order:

1. The copy of the built-in rule for that event (auth events)
2. The company's published copy for the event in the requested language
3. Its published English copy, then any published copy
4. The event's `default_template` design with the catalog subject and preheader, composed on the fly

An unedited catalog subject and preheader follow the send language. Once someone edits them, the stored text is what sends. The body is always the stored copy.

## Variables

Each catalog entry lists tokens, types, and whether they are required. `description` is an i18n key (`email.var.<token>`), not a sentence, so clients can translate it.

When a caller omits `company_name`, email-service uses the name a previous caller stored for the company, then the provider From name. A variable with no value and no default renders empty.

email-service fills these itself, and callers must not send them:

- `system.message_id`.
- `system.assets_url`, `EMAIL_PUBLIC_URL` plus `/static/library`, where library images load from.
- On non-auth mail, `system.unsubscribe_url` and `system.preferences_url`, the signed `POST /u/{token}` URL for that recipient. Auth mail does not set them.

Auth URL hosts are limited by `EMAIL_AUTH_LINK_HOSTS` (default `id.shellui.com`, plus `localhost` and `127.0.0.1` when `DEBUG=true`).
