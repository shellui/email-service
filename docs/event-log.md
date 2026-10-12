---
title: Event log
sidebar_label: Event log
description: Read recent email-service events, including staff-only scheduled job rows.
---

# Event log

email-service stores each outbound mail event and reads it back from the admin API. The log is the record of what happened. Webhook rules are a separate delivery of the same event. See [Webhooks](actions.md).

Rows are kept for `EVENT_LOG_RETENTION_DAYS` (default 7). `purge_expired_data` deletes older rows every hour.

## What is stored

Company events use the webhook catalog:

- `email.message.sent`, `email.message.delivered`, `email.message.delivery_delayed`
- `email.message.bounced`, `email.message.complained`, `email.message.failed`
- `email.message.expired`, `email.message.suppressed`
- `email.unsubscribe.created`
- `email.newsletter.confirmed`, `email.newsletter.unsubscribed`

`data` for a message event is `message_id`, `template_key`, `lane`, `service`, `to_email`, and `to_user_id`. It does not include the rendered HTML, the template variables, or a sign-in link.

Scheduled job runs write `email.scheduled_job.succeeded` or `email.scheduled_job.failed` with no company. Those types are not in the webhook catalog, so a company cannot subscribe to them.

## Read the log

Auth: staff or company owner, except the platform scope.

| Method | Path | Who |
| --- | --- | --- |
| `GET` | `/api/v1/actions/event-log` | That company's events, newest first, up to 100 |
| `GET` | `/api/v1/actions/event-log/{id}` | One row. Platform rows are staff only |
| `GET` | `/api/v1/actions/event-log/types` | The webhook catalog ids |
| `GET` | `/api/v1/actions/event-log/retention` | `retention_days` for the company |
| `GET` | `/api/v1/actions/event-log?scope=platform` | Staff only. Job events with no company |

Company owners receive `403` on `scope=platform` and on the detail of a platform row.

Each list item is `id`, `event_type`, `data`, and `created_at`. Detail adds `company_id`.
