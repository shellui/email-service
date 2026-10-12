---
description: What the email-service container runs next to the web server - the lane workers and the scheduled jobs - and how to split or turn them off.
---

# Workers and scheduled jobs

The email-service Docker image runs everything it needs to deliver mail in one container: the web server, one delivery worker per lane, and the scheduled jobs. With `REDIS_URL` set (required in production), there is nothing else to set up: no extra worker containers and no cron.

## What the container runs

| Process | What it does |
| --- | --- |
| gunicorn | Serves the API on port 8000 |
| `run_email_worker --lane auth` | Sends magic links, invitations, and staff notices |
| `run_email_worker --lane transactional` | Sends event mail |
| `run_email_worker --lane bulk` | Sends bulk mail and moves [broadcasts](broadcasts.md) forward |
| Celery worker with embedded beat | Runs the scheduled jobs below. Redis is the broker |

The container starts with database migrations, then starts every process. A `SIGTERM` (for example `docker stop` or a redeploy) is passed to all of them. If one process exits, the others are stopped and the container exits, so Docker or Coolify restarts it.

## Scheduled jobs

| Job | Schedule | What it does |
| --- | --- | --- |
| `retry_webhooks` | Every minute | Retries pending [Shellui Actions](actions.md) webhook deliveries |
| `sweep_email_queue` | Every minute | Expires overdue messages, releases send leases left by a worker that stopped mid-send, and moves broadcasts forward |
| `purge_expired_data` | Every hour, at minute 17 | Deletes messages, send requests, event log rows, finished deliveries, and unconfirmed newsletter sign-ups past their retention |

Each job is the management command of the same name, so its output is the same whether the scheduler or your own cron runs it. Every run logs one summary line, for example:

```text
INFO [config.scheduled_tasks] sweep_email_queue: expired=0 released=0 broadcasts=0 run_id=1
```

`run_id` is the stored run. A failed run is logged as an error and reported to Sentry when `SENTRY_DSN` is set. See [Monitoring](#monitoring).

## Several containers

Lane workers claim each message with a row lock, so several containers can run the same lanes without sending a message twice.

Every scheduled job takes a Redis lock before it starts (`SET NX` with an expiry: 5 minutes for `retry_webhooks` and `sweep_email_queue`, 15 minutes for `purge_expired_data`). When another container already runs the same job, the run is skipped and logs `skipped, another run is in progress`. The lock expires on its own if a container dies mid-run. Jobs use their own queue (`email-service`) and lock keys (`email-service:scheduler:…`), so one Redis can serve email-service and other Shellui services.

## Settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `REDIS_URL` | unset | Redis for rate limits and the job broker. Required when `DEBUG=false`. With `DEBUG=true` and no Redis, the scheduled jobs do not run and the container logs a warning |
| `CELERY_BROKER_URL` | `REDIS_URL` | Use a different Redis for the jobs |
| `SCHEDULER_ENABLED` | `true` | `false` keeps the scheduled jobs out of this container |
| `EMAIL_WORKERS_ENABLED` | `true` | `false` keeps the lane workers out of this container |
| `CELERY_WORKER_CONCURRENCY` | `2` | Threads in the Celery worker, so an hourly purge never delays the minute jobs |

## Run the workers in their own container

The image takes a mode as its command:

| Command | Starts |
| --- | --- |
| `web` (default) | Migrations, then gunicorn, the lane workers, and the scheduler |
| `worker` | The lane workers and the scheduler only. No migrations, no web server |
| anything else | That command, for example `python manage.py create_service_key …` |

Docker Compose example with the same image and environment:

```yaml
services:
  email-service:
    image: shellui/email-service:latest
    env_file: .env
    environment:
      SCHEDULER_ENABLED: "false"
      EMAIL_WORKERS_ENABLED: "false"
  email-worker:
    image: shellui/email-service:latest
    command: worker
    env_file: .env
    restart: unless-stopped
```

The web container runs migrations; the worker container only needs the same database and Redis.

## Use your own scheduler

Set `SCHEDULER_ENABLED=false` and run the commands with the same image, environment, and database as the web service:

```text
*  * * * * python manage.py retry_webhooks
*  * * * * python manage.py sweep_email_queue
17 * * * * python manage.py purge_expired_data
```

## Monitoring

Staff can see whether the three jobs are running, what the last run did, and which Shellui Actions webhook attempts a run made. Company owners cannot. The data is platform-wide.

Each finished run stores one `ScheduledJobRun` row for 7 days, the latest timestamps in `ScheduledJobState`, monotonic counters for Prometheus, and one staff-only platform event (`email.scheduled_job.succeeded` or `email.scheduled_job.failed`). Those events have no company, are not in the webhook catalog, and are not delivered to Shellui Actions rules. `GET /api/v1/actions/event-log?scope=platform` lists them for staff. A company event log does not.

`counts` for `retry_webhooks`: `webhook_deliveries_attempted`, `webhook_deliveries_succeeded`, `webhook_deliveries_failed` (will be retried), `webhook_deliveries_given_up` (now `dead`).

`counts` for `sweep_email_queue`: `expired`, `released`, `broadcasts`.

`counts` for `purge_expired_data`: rows deleted (`messages`, `send_requests`, `events`, `webhook_deliveries`, `newsletter_pending`, `newsletter_cleared`, `scheduled_job_runs`) and `complete`.

Some runs are not stored:

- Skipped runs: when another container holds the lock, the run only increments the `skipped_locked` counter
- Dry runs: `retry_webhooks --dry-run` changes nothing, so it is not recorded

`purge_expired_data` deletes runs older than 7 days. The latest timestamps per job and the metric counters are kept. A `running` row older than the job's lock expiry (5 minutes for `retry_webhooks` and `sweep_email_queue`, 15 minutes for `purge_expired_data`) belongs to a process that died, so the next run marks it `failed` with `error_key=interrupted`.

### Health

A job is overdue when its last successful run is older than 3 times its interval:

| Job | Interval | Overdue after |
| --- | --- | --- |
| `retry_webhooks` | 1 minute | 3 minutes |
| `sweep_email_queue` | 1 minute | 3 minutes |
| `purge_expired_data` | 1 hour | 2 hours 15 minutes |

Before the first successful run, the clock starts when the monitoring tables were created (the migration). Each job gets one `health` value:

- `disabled`: `SCHEDULER_ENABLED=false` and no run was ever recorded
- `failing`: the last finished run failed
- `overdue`: no successful run within the limit above
- `healthy`: none of the above

External cron is monitored like the in-container scheduler, so `SCHEDULER_ENABLED=false` with a working cron reports `healthy`.

Two more signals cover the scheduler itself:

- `redis_reachable`: email-service answers a `PING` on the broker (`null` without a broker)
- beat heartbeat: each time beat publishes a job, it stores the time in Redis (`email-service:scheduler:beat:heartbeat`). `beat_stale` is true when the scheduler is enabled and beat published nothing for 3 minutes

### Staff API

The endpoints use the same Bearer JWT as other admin endpoints. They need `is_staff`. Other callers get `403`, including company owners, and calls without a token get `401`. A personal access token with global metrics access is not enough here. No `company_id` is needed. The success body uses keys and enums only (`health`, `status`, `error_key`, count names), which the admin panel translates. Failed calls use this service's `error_code` (`unauthorized`, `forbidden`, `not_found`, `validation_failed`).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/scheduled-jobs` | `scheduler_enabled`, `redis_reachable`, `beat_last_seen_at`, `beat_stale`, and per job: `health`, `overdue`, `last_run`, `last_success_at`, `last_failure_at`, `last_skipped_at`, `last_duration_ms`, `last_counts`, `next_expected_at`, `last_24h`, `skipped_locked_total` |
| `GET` | `/api/v1/scheduled-jobs/{job}/runs?limit=20&status=failed` | Recent runs, newest first. `limit` 1 to 100, `status` optional |
| `GET` | `/api/v1/scheduled-jobs/runs/{id}` | One run, plus the webhook attempts it made. `email_events` is an empty list |

`email_events` is present so the admin panel can reuse the view it uses for identity-service, hosting-service, and storage-service. email-service does not post events to itself, so that list stays empty. Webhook attempts are filled in.

### From a run to its webhooks

Every delivery attempt stores a `trigger`:

- `dispatch`: the first try, right after the event
- `automatic_retry`: a `retry_webhooks` run, with `scheduled_job_run_id`

While `retry_webhooks` runs, webhook POSTs send `X-Request-ID: sjr-{run_id}`. Log lines for that run use the same request id.

Staff can open `GET /api/v1/scheduled-jobs/runs/{id}` for the attempts (every company). `GET /api/v1/actions/deliveries?scheduled_job_run_id={id}` lists webhook deliveries of the token company that this run touched. Each attempt on `GET /api/v1/actions/deliveries/{id}` includes `scheduled_job_run_id` for staff.

Company owners see `trigger` on their own delivery attempts, so they know a retry was automatic, and they never see `scheduled_job_run_id` or any run detail. Filtering by `scheduled_job_run_id` as an owner returns `403`.

Run records and error text never store a token or a sign-in link. Query strings, bearer tokens, and sign-in, unsubscribe, and confirm URLs are removed before the row or the log line is written.

### Failed runs

A failed run:

- is stored with `status=failed` and its `error_key`
- logs one ERROR line with the job, run id, trigger and sanitized error, plus the traceback
- is reported to Sentry when `SENTRY_DSN` is set, tagged `scheduled_job`, `scheduled_job_trigger` and `scheduled_job_run_id`
- increments `shellui_email_scheduled_job_runs_total{status="failed"}`
- makes the command exit with status 1, so an external cron that alerts on a failed command still works

If Redis is down when a Celery run tries to take its lock, that run is recorded as `failed` with `error_key=redis_error`. The Celery task returns `failed` after the run is stored, so the error is logged once.

### Prometheus metrics

`GET /api/v1/metrics/all` includes the scheduled job metrics. It needs staff, or a personal access token with `access_global_metrics` (`pat_agm`). `GET /api/v1/metrics` never includes them. Values come from the database, so they are the same whichever gunicorn worker answers and survive restarts.

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `shellui_email_scheduled_job_runs_total` | counter | `job`, `status` | Finished runs: `succeeded`, `failed`, `skipped_locked` |
| `shellui_email_scheduled_job_items_total` | counter | `job`, `kind` | Items processed, `kind` is a `counts` name |
| `shellui_email_scheduled_job_last_success_timestamp_seconds` | gauge | `job` | Unix time of the last successful run (0 before the first one) |
| `shellui_email_scheduled_job_last_run_timestamp_seconds` | gauge | `job` | Unix time the last run started (0 before the first one) |
| `shellui_email_scheduled_job_last_run_duration_seconds` | gauge | `job` | Duration of the last finished run |
| `shellui_email_scheduled_job_overdue` | gauge | `job` | 1 when overdue (see the table above) |
| `shellui_email_scheduler_enabled` | gauge | none | 1 when `SCHEDULER_ENABLED` is true |
| `shellui_email_scheduler_redis_up` | gauge | none | 1 when the broker answers `PING`. Absent without a broker |
| `shellui_email_scheduler_beat_last_seen_timestamp_seconds` | gauge | none | Unix time beat last published a job. Absent before the first one |

Suggested alert rules:

```yaml
groups:
  - name: email-scheduled-jobs
    rules:
      - alert: EmailScheduledJobOverdue
        expr: shellui_email_scheduled_job_overdue == 1
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "email-service job {{ $labels.job }} has no recent successful run"
      - alert: EmailScheduledJobFailing
        expr: increase(shellui_email_scheduled_job_runs_total{status="failed"}[15m]) >= 3
        labels:
          severity: warning
      - alert: EmailSchedulerRedisDown
        expr: shellui_email_scheduler_redis_up == 0
        for: 2m
        labels:
          severity: critical
      - alert: EmailWebhooksGivenUp
        expr: increase(shellui_email_scheduled_job_items_total{kind="webhook_deliveries_given_up"}[1h]) > 0
        labels:
          severity: info
```

The overdue alert covers a stopped beat, a stuck worker, and a missing external schedule alike.

## Related

- [Lanes](lanes.md)
- [Broadcasts](broadcasts.md)
- [Configuration](configuration.md)
