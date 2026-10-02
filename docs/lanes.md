# Lanes

Three lanes. A template's `lane_class` is fixed. `/send` cannot move a transactional template onto `auth`.

| Lane | Who uses it | From address | Retry | TTL |
| --- | --- | --- | --- | --- |
| `auth` | Direct send: magic link, invitation | `no-reply@shellui.com`, or the company `from_email` | 2s, then 6s, then one platform SMTP failover | Default 120s (magic link) or 300s (invitation). Max 300s. |
| `transactional` | Event ingest and other direct product mail | Same as auth | 30s * 2^(attempt-1), cap 1 hour, 8 attempts | None |
| `bulk` | Not accepted in this version | `news@news.shellui.com` | n/a | n/a |

`/send` with lane `bulk`, or a bulk template, returns `400 lane_requires_campaign`. Campaign scheduling is a later phase.

## Auth TTL

The worker checks `expires_at` before provider handoff. An expired auth message becomes `expired`, the variables are deleted, and `email.message.expired` is emitted. That increments `shellui_email_auth_ttl_expiries`.

A hard bounce suppresses the address for auth mail for 30 days and returns `422 recipient_suppressed` on the next auth send. Complaints and unsubscribes do not block the auth lane.

Auth rate limit: 5 messages per recipient per company per 10 minutes (`429 recipient_rate_limited`).

## Pause

`POST /api/v1/lanes/{lane}/pause` and `/resume` are staff-only. A provider HTTP 401 or 403 also pauses the lane (`reason` `provider_unauthorized`) so a bad key does not burn the queue. New sends receive `409 lane_paused` until resume.

## Workers

`python manage.py run_email_worker` polls every second and claims due rows. On Postgres it also issues `NOTIFY email_lane_{lane}` when a message is queued. The worker does not `LISTEN`. The notify is reserved for a later wake-up. SQLite (local) has no notify.

`python manage.py sweep_email_queue` runs one pass. Use it from cron if you do not keep a resident worker.

Production must set `EMAIL_DELIVER_SYNC=false`. The sync flag delivers inside the request and is for tests.
