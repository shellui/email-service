---
title: Design library
sidebar_label: Design library
description: The 40 React Email designs in five sets, and how a company template is copied onto a rule.
---

# Design library

The library is the set of designs an event email starts from. It holds 40 built-in designs and the company's own templates.

A library template is never sent as is. When a company creates an email rule, email-service copies the chosen design onto that event (see [templates.md](templates.md)). Editing a library template later does not change copies that already exist.

## Built-in designs

The built-ins are the official React Email demo emails, five sets of eight. Set names are proper nouns and are not translated.

| Set | Keys |
| --- | --- |
| Barebone | `barebone.activation`, `barebone.feature-announcement`, `barebone.password-reset`, `barebone.product-update`, `barebone.subscription-confirmation`, `barebone.subscription-update`, `barebone.text-only`, `barebone.welcome` |
| Matte | Same eight names as Barebone, prefixed `matte.` |
| Protocol | Same eight names as Barebone, prefixed `protocol.` |
| Arcane | `arcane.abandoned-cart`, `arcane.activation`, `arcane.newsletter`, `arcane.order-confirmation`, `arcane.order-shipping`, `arcane.password-reset`, `arcane.promo`, `arcane.welcome` |
| Studio | Same eight names as Arcane, prefixed `studio.` |

Built-ins are read-only (`409 library_built_in` on `PUT` or `DELETE`). Duplicate one to change it.

The demo copy stays as written, in English, and is meant to be edited. The import passes `{{ company_name }}` as each demo's company name and `{{ action_url }}` as its link, and points unsubscribe links at `{{ system.unsubscribe_url }}`. `company_name` is filled at send time. On a copy, `{{ action_url }}` becomes the event's link variable (`link_token` in the catalog), or `https://example.com` when the event has no link.

Images and fonts are served by email-service from `static/library/` and referenced as `{{ system.assets_url }}/…`, images in the document and fonts in the set's `head.css`. `system.assets_url` is `EMAIL_PUBLIC_URL` plus `/static/library` and is filled at send time. Sent mail loads nothing from another host, which receivers such as Gmail treat as a sign of trust when it matches the sending domain. Point `EMAIL_PUBLIC_URL` at your sending domain or a subdomain of it. WhiteNoise serves the fonts with `Access-Control-Allow-Origin: *`, which mail clients and the admin preview need for web fonts.

The fonts are the sets' Google Fonts files (Inter, Geist, Instrument Serif, IBM Plex Sans Condensed, all under the SIL Open Font License), copied into `static/library/fonts/` with their Google path. `node tools/host-fonts.mjs` inlines any `fonts.googleapis.com` import of a `head.css`, downloads each `fonts.gstatic.com` file it uses, and rewrites the CSS to `{{ system.assets_url }}/fonts/…`. The demo import runs it on every import.

### Source

The demos are vendored under `renderer/demos/` with their MIT notice ([renderer/demos/LICENSE](https://github.com/shellui/email-service/blob/main/renderer/demos/LICENSE), copyright 2024 Plus Five Five, Inc., from [resend/react-email](https://github.com/resend/react-email/tree/canary/apps/demo/emails)). Community templates from that repository are not used, because they copy real brands.

`npm run import:demos` renders each demo, imports the HTML into a React Email editor document, and writes `renderer/library/<set>/<name>.json` (`key`, `set`, `name`, `subject`, `preheader`, `document`) plus one `head.css` per set (fonts and mobile rules). `post_migrate` syncs those files into `LibraryTemplate` rows and composes their HTML.

## Company templates

A company template is a library row with `built_in: false` and the company's `company_id`. Its key is `company.` plus 12 hex characters. It starts blank, or as a duplicate of any library template (`source_id`), and keeps that template's set so the set's `head.css` still applies. A blank template has no set and no head CSS.

Company templates are visible only to their company. Deleting one does not touch the event copies made from it.

A company template can save a [theme](templates.md#themes). Its HTML is composed with it, duplicates keep it, and event copies made from it start with it. Built-ins have none.

## Endpoints

Auth: staff or company owner, `company_id` query or the company on the JWT.

`GET /api/v1/library?company_id=42`

```json
{
  "sets": [{"key": "barebone", "name": "Barebone"}, {"key": "matte", "name": "Matte"}],
  "templates": [
    {
      "id": 3,
      "key": "barebone.welcome",
      "set": "barebone",
      "name": "Welcome",
      "built_in": true,
      "company_id": null,
      "subject": "Welcome to {{ company_name }}",
      "preheader": "…",
      "updated_at": "2026-10-03T12:00:00Z",
      "html": "<!DOCTYPE html …>"
    }
  ]
}
```

`html` is the composed email with placeholders intact, for previews in a sandboxed iframe.

`GET /api/v1/library/{id}?company_id=42` adds `document`, `theme` (`{}` when none), `text`, `head` (the set's CSS, read-only), and `variables` (`company_name` and `action_url`, the only tokens a library design uses).

`POST /api/v1/library?company_id=42` creates a company template and returns it at `201` with the detail shape:

```json
{"name": "Acme welcome", "source_id": 3}
```

All fields are optional except that a blank template needs `name`. With `source_id`, an omitted `name` becomes the source name plus ` copy`. `subject`, `preheader`, `document`, and `theme` override the source.

`PUT /api/v1/library/{id}?company_id=42` updates `name`, `subject`, `preheader`, `document`, or `theme` and recomposes the HTML.

`DELETE /api/v1/library/{id}?company_id=42` returns `204`.

| `error_code` | HTTP | When |
| --- | --- | --- |
| `library_not_found` | 404 | The id is not a built-in or one of this company's templates |
| `library_built_in` | 409 | `PUT` or `DELETE` on a built-in |
| `validation_failed` | 400 | Blank `name`, or a document that fails the checks in [templates.md](templates.md#document) |
| `library_not_synced` | 503 | An event's default design is missing because migrations have not run |
