# Lanes

Three lanes. A template's `lane_class` is fixed. `/send` cannot move a transactional template onto `auth`.

| Lane | Who uses it | From address | Retry | TTL |
| --- | --- | --- | --- | --- |
| `auth` | Direct send: magic link, invitation | `no-reply@shellui.com`, or the company `from_email` | 2s, then 6s, then one platform SMTP failover | Default 120s (magic link) or 300s (invitation). Max 300s. |
| `transactional` | Event ingest and other direct product mail | Same as auth | 30s * 2^(attempt-1), cap 1 hour, 8 attempts | None |
| `bulk` | [Broadcasts](broadcasts.md) | The company Bulk From, or `news@news.shellui.com` for Shellui companies | Same as transactional | None |

`/send` with lane `bulk`, or a bulk template, returns `400 lane_requires_campaign`. Bulk mail only goes out as a broadcast. Bulk messages carry one-click `List-Unsubscribe` headers and skip the company's bulk unsubscribes.

## Auth TTL

The worker checks `expires_at` before provider handoff. An expired auth message becomes `expired`, the variables are deleted, and `email.message.expired` is emitted. That increments `shellui_email_auth_ttl_expiries`.

A hard bounce suppresses the address for auth mail for 30 days and returns `422 recipient_suppressed` on the next auth send. Complaints and unsubscribes do not block the auth lane.

Auth rate limit: 5 messages per recipient per company per 10 minutes (`429 recipient_rate_limited`).

## Pause

`POST /api/v1/lanes/{lane}/pause` and `/resume` are staff-only and pause that lane for every company. A provider HTTP 401 or 403 pauses only the company whose key was rejected (`reason` `provider_unauthorized`). Other companies keep sending. New sends for a paused company receive `409 lane_paused` until that pause is cleared.

Auth mail also has a company-wide cap (`EMAIL_COMPANY_AUTH_LIMIT`, default 30 per `EMAIL_COMPANY_AUTH_WINDOW_SECONDS`, default 60). Over the cap the response is `429 company_rate_limited`. The per-recipient cap is unchanged.

## Workers

`python manage.py run_email_worker` polls every second and claims due rows. On Postgres it also issues `NOTIFY email_lane_{lane}` when a message is queued. The worker does not `LISTEN`. The notify is reserved for a later wake-up. SQLite (local) has no notify.

`python manage.py sweep_email_queue` expires overdue messages and releases send leases left by a worker that stopped mid-send. The container runs it every minute, see [Workers and scheduled jobs](scheduled-jobs.md).

The bulk worker (`--lane bulk`) and the sweep also move [broadcasts](broadcasts.md) forward.

Production must set `EMAIL_DELIVER_SYNC=false`. The sync flag delivers inside the request and is for tests.
