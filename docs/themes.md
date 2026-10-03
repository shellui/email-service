# Themes

A company picks one theme. New email templates start from it. The five themes are the official React Email demo sets: Barebone, Matte, Protocol, Arcane, and Studio. Names are proper nouns and are not translated.

Sample copy from those demos is not used. Community templates from that repository are not used, because they copy real brands. Shellui keeps its own heading, paragraph, and button text.

## License

Layout, palette, typography, and fonts are adapted from the MIT-licensed demos in [resend/react-email](https://github.com/resend/react-email/tree/canary/apps/demo/emails) (`01-Barebone`, `02-Matte`, `03-Protocol`, `04-Arcane`, `05-Studio`). Copyright 2024 Plus Five Five, Inc. The notice is in [renderer/themes/LICENSE](../renderer/themes/LICENSE).

## Keys

| Key | Name | What the wrapper does |
| --- | --- | --- |
| `barebone` | Barebone | Light gray page (`#F3F4F6`), white card, Inter, centered, solid dark button with 8px radius. Default when the company has not chosen a theme. |
| `matte` | Matte | Off-white page (`#FBFCFB`), green brand (`#103B05`), Arial headings, Inter body, left aligned, square solid button. |
| `protocol` | Protocol | Dark page (`#212121`) and card (`#131313`), white square button, condensed uppercase headings, Inter body. |
| `arcane` | Arcane | White page, dark red card (`#300610`), cream type, serif headings, outline button. |
| `studio` | Studio | Light gray page (`#F6F6F6`), white card, dark top bar, Geist headings, Inter body, outline button. |

A block document (`heading`, `text`, `button`, `footer`) renders inside that wrapper. Fonts load with a fallback stack (`Arial` or `system-ui`) when the remote font file is unavailable.

`EMAIL_RENDERER=node` (the Docker image, `renderer/render.mjs`) applies the theme spec in `renderer/themes/themes.json`. `EMAIL_RENDERER=python` (local and tests) uses the same five keys and the same palette, font stacks, and button style.

`theme_palette` on a template version is an optional accent override. `{}` keeps the theme colors. A full seven-color palette (`background`, `foreground`, `muted`, `mutedForeground`, `primary`, `primaryForeground`, `border`, each `#RRGGBB`) replaces those slots. Fonts, spacing, and button shape stay with the theme. `primary` is the button accent. A partial palette is `400 validation_failed` with `theme_palette: ["invalid_color"]`.

Versions that were stored as `theme_name` `shellui` migrate to `barebone`. There is no production data on the old name. An unknown theme key is `400 theme_unknown`.

## Endpoints

Auth matches the other admin routes (staff or company owner).

`GET /api/v1/themes?company_id=42` returns a JSON array:

```json
[{"key": "barebone", "name": "Barebone", "preview_url": "/api/v1/themes/barebone/preview?language=en"}]
```

`preview_url` is a path. `language=en` is the sample language in that path. Request `fr` on the preview URL for the French sample.

`GET /api/v1/themes/{key}/preview?company_id=42&language=en` returns `text/html`: a heading, one short paragraph, and a button. Unknown key: `400 theme_unknown`. Language other than `en` or `fr`: `400 language_not_available`. Fetch it with the Bearer token and put the HTML in a sandboxed iframe (`srcdoc`).

## Company setting

`GET /api/v1/settings?company_id=42`

```json
{"theme": "barebone", "templates_using_other_theme": 1}
```

`templates_using_other_theme` is how the admin warns before a theme switch. A template counts when its representative theme differs from the company theme. Representative means the active published version, otherwise the latest version.

`PUT /api/v1/settings?company_id=42`

```json
{"theme": "studio", "apply_to_existing": true}
```

Both fields are required. `apply_to_existing` must be a boolean.

`false` changes only the setting. New templates use the new theme. Existing ones stay, and the mismatch count stays above zero until they are updated. Response: `{"theme": "studio", "updated_templates": 0}`.

`true` copies every company template that uses another theme. Same subject, preheader, document, and palette, new `theme_name`. A published template stays published: a new published version is rendered, the previous published version is archived, and `active_version` moves. A draft gets a new draft. Each template counts once. Response: `{"theme": "studio", "updated_templates": 3}`.

Template list and detail items include `theme` and `uses_company_theme` so the admin can badge the mismatched rows. See [templates.md](templates.md).
