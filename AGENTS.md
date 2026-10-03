# Agent notes

## Shellui guidelines

When writing or reviewing user-facing copy in this repo (templates, docs, UI strings, emails, changelogs that ship to users), follow the versioned Shellui handbooks. Read the markdown. Do not scrape the HTML.

- Writing: https://shellui.com/guidelines/writing.md
- Building a page / chrome: https://shellui.com/design.md and https://shellui.com/guidelines/web-design.md
- Hub: https://shellui.com/guidelines/

Canonical source lives in `shellui/website` (`content/guidelines/`, `skills/writing-guidelines/`). Prefer fetching the published `.md` URLs above when working outside that repo.

### Hard rules from writing guidelines

- Product name is **Shellui** only
- Ban em dashes (`—`) and en dashes (`–`) used as punctuation; prefer a hyphen (`-`) or split the sentence
- Straight quotes in markdown source; ellipsis `…`, not three dots `...`
- Avoid banned filler: easy, simple, quick, seamless, robust, powerful, just, very, really, simply
- Say **Shellui Actions** (not bare "Actions") in user-facing docs for webhook rules and delivery

When writing or reviewing user-facing copy in this repo, follow https://shellui.com/guidelines/writing.md (no em or en dashes; product is always **Shellui**).

## This service

`email-service` sends mail for Shellui products. Other services integrate through [docs/integration.md](docs/integration.md). That file is the contract. Do not invent a second client shape in a sibling repo.

- Direct `POST /api/v1/send` is for messages the caller must send (identity magic links and invitations, `auth` lane). It does not consult email rules.
- `POST /api/v1/events` queues one message per enabled email rule. Auth-lane events have a built-in rule that cannot be deleted or disabled. No matching rule returns `skipped_reason: no_rule`. Catalog `default_enabled` does not send mail.
- Provider code stays behind `apps/providers`. Resend and SMTP are active. Mailjet is one new adapter module. See [docs/providers.md](docs/providers.md).
- API JSON uses `error_code` and optional `field_errors`. Do not put translated sentences in JSON.
- Suggested templates live in `apps/email/catalog.py` and are exported under `defaults/`.
