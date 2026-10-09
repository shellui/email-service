---
title: Broadcasts
sidebar_label: Broadcasts
description: One email to many company members or to a newsletter list, each recipient in their language.
---

# Broadcasts

A broadcast is one email a company writes once and sends to many of its users: product news, a launch, a policy change. Company owners and staff create them in the admin under **Email > Broadcasts**.

## Lifecycle

1. **Create** from a library design. email-service makes a company template with `kind: broadcast`, publishes it, and keeps it out of the event template list. It adds an unsubscribe link when the design has none.
2. **Write** with the same editor as event copies: versions, translations, themes, and test sends all work. The variables are `company_name`, `first_name`, `last_name`, and `recipient_email`. Publishing requires `{{ system.unsubscribe_url }}`, like every bulk template.
3. **Choose the audience** (below) and preview it.
4. **Send.** email-service asks identity for the audience, skips unsubscribed and suppressed addresses, stores one recipient row per address, and hands the broadcast to the bulk worker. A sent broadcast cannot be edited or sent again.

States: `draft`, `queued`, `preparing` (recipients are being handed to the provider), `sending`, `sent`, `failed` (with `last_error_code`).

## Audience

```json
{
  "mode": "filter",
  "group_ids": [4],
  "roles": ["member"],
  "access": "enabled",
  "joined_after": "2026-01-01",
  "joined_before": "",
  "seen_after": "2026-06-01",
  "seen_before": "",
  "user_ids": [],
  "emails": []
}
```

`filter` sends to every company member who matches all the given filters. `group_ids` counts nested groups. `roles` is any of `owner`, `staff`, `member`. `access` is `enabled` (default), `disabled`, or `any`. The `*_before` dates are inclusive, and `seen_before` also matches users who never signed in.

`pick` sends to `user_ids` (company members) and `emails` (addresses outside the directory, up to 1000).

`newsletter` sends to the confirmed subscribers of one of the company's lists: `{"mode": "newsletter", "list_id": 12}`. It does not call identity. Each subscriber gets their sign-up language, and the bulk-lane unsubscribe link removes them from that list only. See [newsletters.md](newsletters.md).

email-service relays the caller's identity JWT to `GET {IDENTITY_SERVICE_URL}/api/v1/users/audience`, so identity decides who the caller may see. The JWT company must be the broadcast's company (`403 company_mismatch` otherwise). Users without an email or with a deactivated account are never included. More than `EMAIL_BROADCAST_MAX_RECIPIENTS` returns `400 audience_too_large`.

## Languages

Each user's language comes from their identity preferences. A recipient gets the content in their language when the broadcast has that translation, and in the main language otherwise. Pasted addresses get the main language. The preview shows the count per send language.

## Sender

Broadcasts go out from the bulk address, ideally on a subdomain kept for marketing mail (for example `news@news.acme.com`), so complaints about news do not hurt sign-in and receipt mail on the main domain.

- A company with its own provider sets **Bulk From** in **Email > Provider**. Without it, sending returns `409 bulk_sender_required`.
- Shellui companies (`EMAIL_PLATFORM_COMPANY_IDS`) fall back to `BULK_FROM_EMAIL` and `BULK_FROM_NAME`.
- Other companies without a provider get `409 platform_sender_not_allowed`.

## Delivery

The delivery is picked from the company provider when the broadcast is sent.

**`resend`**: [Resend Broadcasts](https://resend.com/docs/api-reference/broadcasts/create-broadcast). For each send language email-service creates a segment, adds each recipient as a contact, then creates one broadcast with `send: true`. Contact fields stay placeholders that Resend fills in (`{{{contact.first_name|fallback}}}`), and the unsubscribe link becomes `{{{RESEND_UNSUBSCRIBE_URL}}}`. Calls are throttled to `EMAIL_RESEND_BROADCAST_RPS` (Resend allows 10 per second per team), and a `429` waits for `Retry-After` before the next pass. The contact address and names are deleted from email-service once Resend has them.

**`bulk_lane`** (every other provider): one bulk-lane message per recipient in their language, with one-click `List-Unsubscribe` headers. The worker retries like transactional mail.

`run_email_worker --lane bulk` moves broadcasts forward on every poll, and the `sweep_email_queue` job does the same every minute. See [Workers and scheduled jobs](scheduled-jobs.md).

## Unsubscribes and webhooks

Both deliveries honor the company's bulk unsubscribes and the suppressions that apply to the bulk lane. A bulk unsubscribe also unsubscribes the address from every newsletter list of the company.

For Resend, subscribe the webhook to `email.*` and `contact.updated` events. Events carrying `broadcast_id` update the matching recipient (`sent`, `delivered`, `bounced`, `complained`, `failed`). A hard bounce suppresses the address for 30 days and a complaint suppresses it for bulk and transactional mail. `contact.updated` with `unsubscribed: true` (someone used Resend's unsubscribe link) records a bulk unsubscribe for the company of the webhook, or for every Shellui company on the platform webhook.

## Endpoints

Auth: staff or company owner. Errors use the usual `{error_code, field_errors}` body.

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/v1/broadcasts` | `{broadcasts: [...]}`, newest first |
| `POST` | `/api/v1/broadcasts` | `{name, source_key, language?, audience?}`. Returns `201` with the broadcast. |
| `GET` | `/api/v1/broadcasts/{id}` | Adds `sender: {from_email, from_name, delivery, error_code}` |
| `PATCH` | `/api/v1/broadcasts/{id}` | `{name?, audience?}`. Drafts only (`409 broadcast_not_draft`). |
| `DELETE` | `/api/v1/broadcasts/{id}` | Drafts only. Deletes the content too. |
| `POST` | `/api/v1/broadcasts/{id}/preview` | `{audience?}` (default: the saved one). Returns `{total, sendable, unsubscribed, suppressed, languages, samples}`. |
| `POST` | `/api/v1/broadcasts/{id}/send` | Returns `202` with the broadcast. |

The content is edited through `/api/v1/templates/{template_id}/versions` like any company template.

After sending, `counts` holds `total`, `pending`, `skipped_unsubscribed`, `skipped_suppressed`, `queued`, `sent`, `delivered`, `bounced`, `complained`, and `failed`.

| Error | Status | When |
| --- | --- | --- |
| `bulk_sender_required` | 409 | The company provider has no Bulk From |
| `broadcast_not_draft` | 409 | Editing, deleting, or sending a broadcast that was already sent |
| `audience_empty` | 400 | Nobody matches |
| `audience_too_large` | 400 | More than `EMAIL_BROADCAST_MAX_RECIPIENTS` (`limit` in the body) |
| `identity_not_configured` | 503 | `IDENTITY_SERVICE_URL` is not set |
| `identity_unavailable` | 502 | Identity did not answer |
| `lane_paused` | 409 | The bulk lane is paused for the company |
