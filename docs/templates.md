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
- the catalog subject and preheader in the rule's language (English when the rule has none);
- the library template's [theme](#themes), or else `content.theme` (the admin sends the user's current Shellui theme).

`POST /api/v1/templates/{id}/versions` adds a draft. Send `subject`, `preheader`, `document`, and optionally `translations` (see below) and `theme` (omitted, the latest version's are kept), or `library_id` to start over from another library template: the document is replaced (adapted to the event the same way), the subject and preheader stay, translations keep only their subject and preheader, and the theme becomes the new template's when it has one. The copy's `source_key` and `set` follow the new template. `201` `{number, state: "draft"}`.

`GET /api/v1/templates/{id}/versions` and `GET /api/v1/templates/{id}/versions/{number}` return `{number, state, subject, preheader, document, translations, theme, published_at}`. `POST /api/v1/templates/{id}/versions/{number}/publish` runs the lane checks on every language and sets `active_version`.

## Languages

A copy is one layout with text per language. `document`, `subject`, and `preheader` are in the copy's `language` (the main language, `en` by default). `translations` holds each other language:

```json
{"fr": {"subject": "Bienvenue", "preheader": "", "blocks": {"k3v9q2xa": {"content": [{"type": "text", "text": "Bonjour"}], "source": "1x9f0c"}}}}
```

- Text blocks (`paragraph`, `heading`, `button`, `codeBlock`) carry `attrs.textId`, a stable id the editor assigns. A translated block replaces that block's inline content (`text` and `hardBreak` nodes only) and nothing else, so images, columns, alignment, and button links are shared by every language.
- A block with no translation keeps the main text. An empty subject or preheader falls back to the main one, or to the catalog suggestion for that language while the main one is unedited.
- `source` is the admin's fingerprint of the main text the block was translated from. email-service stores it as is; the admin compares it to flag translations whose main text changed since.
- Languages are `en` and `fr`, and never the main language: `400 language_not_available`. A malformed entry is `400 validation_failed` with `field_errors.translations`.
- Each language is composed on save (stored in `rendered`) and validated like the main document. A failure in one language adds `language` to the error body.

The admin edits every language in the same editor and publishes them together. Translators, or an AI pass later, only need `translations`: the block ids and main texts are in `document`.

## Themes

A theme repaints a library design with a Shellui theme's colors. The built-in designs keep their colors inline, and `apps/email/theming.py` maps each color of a set to a role: `background`, `foreground`, `card`, `muted`, `muted_foreground`, `primary`, `primary_foreground`, `border`. Library sync (and migration `0007` for existing rows and copies) rewrites those colors as CSS variables that keep the original as fallback:

```text
color:rgb(20,23,30)  becomes  color:var(--email-foreground,rgb(20,23,30))
```

A theme is `{"name": "ocean", "label": "Ocean", "colors": {"primary": "#0a66c2", …}}` with `#rrggbb` colors for any of the roles, or `{}` for the design's own colors. Anything else is `400 validation_failed` with `field_errors.theme`. It is stored on the version and every language composes with it. Composing always replaces the variables, by the theme's color or the fallback, so stored and sent HTML never holds `var()`.

The admin lists "Template colors" and every theme of Settings > Appearance, using each theme's light colors. Switching sets the variables on the editor canvas, so the design repaints live, and the preview composes with the theme. A design without roles (a blank template, or colors the map does not know) is unaffected.

`POST /api/v1/templates/{id}/send-test` sends the posted `subject`, `preheader`, `document`, and `theme` (composed on the fly) or, without a document, the latest draft, to the caller or to `to` for staff.

`GET /api/v1/templates?event_type=` lists the company's copies, optionally only those whose tokens fit that event. Every `{{ token }}` must be declared on the event, or be `recipient_email`, or start with `system.`. List and detail items carry `name`, `event_type`, `language`, `active_version`, `source_key`, `set`, and `head`.

`DELETE /api/v1/templates/{id}` returns `204`, or `409 template_in_use` when a rule still points at the copy. Deleting a rule deletes its copy.

## Which copy sends

Event ingest sends each enabled rule's copy. Direct sends by catalog key use, in order:

1. The copy of the built-in rule for that event (auth events)
2. The company's published copy for the event in the requested language
3. Its published English copy, then any published copy
4. The event's `default_template` design with the catalog subject and preheader, composed on the fly

A copy sends in the send language when it has a translation for it: that language's blocks, subject, and preheader, with the fallbacks above. Otherwise it sends the main language, where an unedited catalog subject and preheader still follow the send language.

## Variables

Each catalog entry lists tokens, types, and whether they are required. `description` is an i18n key (`email.var.<token>`), not a sentence, so clients can translate it.

When a caller omits `company_name`, email-service uses the name a previous caller stored for the company, then the provider From name. A variable with no value and no default renders empty.

email-service fills these itself, and callers must not send them:

- `system.message_id`.
- `system.assets_url`, `EMAIL_PUBLIC_URL` plus `/static/library`, where library images load from.
- On non-auth mail, `system.unsubscribe_url` and `system.preferences_url`, the signed `POST /u/{token}` URL for that recipient. Auth mail does not set them.

Auth URL hosts are limited by `EMAIL_AUTH_LINK_HOSTS` (default `id.shellui.com`, plus `localhost` and `127.0.0.1` when `DEBUG=true`).
