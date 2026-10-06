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
| `retry_webhooks` | Every minute | Sends due [Shellui Actions](actions.md) webhook deliveries, first tries and retries |
| `sweep_email_queue` | Every minute | Expires overdue messages, releases send leases left by a worker that stopped mid-send, and moves broadcasts forward |
| `purge_expired_data` | Every hour, at minute 17 | Deletes messages, send requests, event log rows, finished deliveries, unconfirmed newsletter sign-ups, and scheduled job runs past their retention |

Each job is the management command of the same name, so its output is the same whether the scheduler or your own cron runs it. Every run logs one summary line, for example:

```text
INFO [config.scheduled_tasks] [req=-] sweep_email_queue: expired=0 released=0 broadcasts=0 run_id=1842
```

Every run is also recorded, see [Monitoring](#monitoring).

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

Every run of the three jobs is recorded, whether the in-container beat or your own cron started it, and each lane worker reports a heartbeat. Django staff can read both through the API and Prometheus. Company owners never see them.

### What each run records

The management command records its own run, so the Celery path and the cron path give the same data. Each run writes one `ScheduledJobRun` row:

| Field | Content |
| --- | --- |
| `job` | `retry_webhooks`, `sweep_email_queue`, or `purge_expired_data` |
| `trigger` | `celery` (in-container beat) or `command` (your scheduler, or a manual run) |
| `status` | `running`, `succeeded`, or `failed` |
| `started_at`, `finished_at`, `duration_ms` | Timing |
| `counts` | Items processed, see below |
| `error_key`, `error_class`, `error_message` | On failure: a stable key (`database_error`, `redis_error`, `timeout`, `network_error`, `interrupted`, `unexpected_error`), the exception class, and a short message without URL query strings, credentials, or tokens |
| `host` | Host name and process id |
| `event_log_id` | The platform event written for this run |

`counts` per job:

- `retry_webhooks`: `webhook_deliveries_attempted`, `webhook_deliveries_succeeded`, `webhook_deliveries_failed` (will be retried), and `webhook_deliveries_given_up` (now `dead`)
- `sweep_email_queue`: `messages_expired`, `send_leases_released`, and `broadcasts_pending` (broadcasts that still need work after the run)
- `purge_expired_data`: rows deleted per type (`messages`, `send_requests`, `event_log`, `webhook_deliveries`, `newsletter_pending`, `newsletter_cleared`, `scheduled_job_runs`)

Some runs are not stored:

- **Skipped runs**: when another container holds the lock, the run only increments the `skipped_locked` counter. With two replicas, one run in two is skipped, and a row for each would double the writes
- **Dry runs**: `retry_webhooks --dry-run` changes nothing, so it is not recorded

Runs are kept 7 days: `purge_expired_data` deletes older ones. The latest timestamps per job and the metric counters are never purged. A `running` row older than the job's lock expiry (5 minutes for `retry_webhooks` and `sweep_email_queue`, 15 minutes for `purge_expired_data`) belongs to a process that died, so the next run marks it `failed` with `error_key=interrupted`.

While a run executes, its log lines carry `[req=sjr-{run_id}]`, so one search finds everything a run logged.

### Events

Each finished run writes one platform event without a company: `email.scheduled_job.succeeded` or `email.scheduled_job.failed`. The data holds `run_id`, `job`, `trigger`, `duration_ms`, `counts`, `host`, `error_key`, and `error_class` (never the error message). Staff list them with `GET /api/v1/actions/event-log?scope=platform`. Company owners get `403` on that scope and on the detail of a platform event. These events are not in the Shellui Actions catalog, so no company can subscribe a webhook to them. They follow `EVENT_LOG_RETENTION_DAYS` like other event log rows.

### Health and overdue jobs

A job is overdue when its last successful run is older than 3 times its interval:

| Job | Interval | Overdue after |
| --- | --- | --- |
| `retry_webhooks` | 1 minute | 3 minutes |
| `sweep_email_queue` | 1 minute | 3 minutes |
| `purge_expired_data` | 1 hour | 2 hours 15 minutes |

Before the first successful run, the clock starts when the monitoring tables were created. Each job gets one `health` value:

- `disabled`: `SCHEDULER_ENABLED=false` and no run was ever recorded. Set up your cron (see [Use your own scheduler](#use-your-own-scheduler)); the first recorded run turns monitoring on
- `failing`: the last finished run failed
- `overdue`: no successful run within the limit above
- `healthy`: none of the above

Cron runs are monitored like in-container runs, so `SCHEDULER_ENABLED=false` with a working cron reports `healthy`.

More signals cover the processes themselves:

- `redis_reachable`: email-service answers a `PING` on the broker (`null` without a broker)
- beat heartbeat: each time beat publishes a job, it stores the time in Redis (`email-service:scheduler:beat:heartbeat`). `beat_stale` is true when the scheduler is enabled and beat published nothing for 3 minutes. A fresh heartbeat with an overdue job means the Celery worker is stuck or down
- lane worker heartbeat: each `run_email_worker` stores the time of its last poll in Redis at most every 15 seconds (`email-service:scheduler:worker:{lane}:heartbeat`). A lane is `stale` when no worker polled for 60 seconds, wherever the workers run. Mail on that lane is not sent until a worker is back

### Admin API (staff only)

The endpoints use the same Bearer JWT as other admin endpoints and need Django `is_staff`: other callers get `403`, including company owners and tokens with `access_global_metrics`, and calls without a token get `401`. No `company_id` is needed. Responses contain keys and enums only (`health`, `status`, `error_key`, count names).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/scheduled-jobs` | `scheduler_enabled`, `redis_reachable`, `beat_last_seen_at`, `beat_stale`, `workers_enabled`, `workers` (per lane: `last_seen_at`, `stale`), and per job: `health`, `overdue`, `last_run`, `last_success_at`, `last_failure_at`, `last_skipped_at`, `last_duration_ms`, `last_counts`, `next_expected_at`, `last_24h`, `skipped_locked_total` |
| `GET` | `/api/v1/scheduled-jobs/{job}/runs?limit=20&status=failed` | Recent runs, newest first. `limit` 1 to 100, `status` optional |
| `GET` | `/api/v1/scheduled-jobs/runs/{id}` | One run with the webhook delivery attempts it made, across companies |
| `GET` | `/api/v1/actions/event-log?scope=platform` | The platform events of the runs |

Example job entry:

```json
{
  "job": "sweep_email_queue",
  "health": "healthy",
  "overdue": false,
  "interval_seconds": 60,
  "overdue_after_seconds": 180,
  "last_success_at": "2026-10-06T13:21:02.511+00:00",
  "next_expected_at": "2026-10-06T13:22:01.904+00:00",
  "last_counts": {"messages_expired": 0, "send_leases_released": 1, "broadcasts_pending": 0},
  "last_24h": {"succeeded": 1439, "failed": 1},
  "skipped_locked_total": 0
}
```

Every Shellui Actions delivery attempt made by a `retry_webhooks` run stores the run id, so `GET /api/v1/scheduled-jobs/runs/{id}` lists the attempts of every company for that run.

### Failed runs

A failed run:

- is stored with `status=failed` and its `error_key`
- logs one ERROR line with the job, run id, trigger, and sanitized error, plus the traceback. The original exception message is replaced by the sanitized one, so a URL query string or credential in it never reaches the logs
- is reported to Sentry when `SENTRY_DSN` is set, tagged `scheduled_job`, `scheduled_job_trigger`, and `scheduled_job_run_id`
- increments `shellui_email_scheduled_job_runs_total{status="failed"}`
- makes the command exit with status 1 and print `retry_webhooks failed: OperationalError: … (run_id=1234)`, so an external scheduler that alerts on failed jobs still works

If Redis is down when a Celery run tries to take its lock, that run is recorded as `failed` with `error_key=redis_error`.

### Prometheus metrics

The global `GET /api/v1/metrics` (Django staff, or a token with `access_global_metrics`, without `company_id`) includes these metrics. A company scrape never does. Values come from the database and Redis, so they are the same whichever gunicorn worker answers and survive restarts.

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `shellui_email_scheduled_job_runs_total` | counter | `job`, `status` | Finished runs: `succeeded`, `failed`, `skipped_locked` |
| `shellui_email_scheduled_job_items_total` | counter | `job`, `kind` | Items processed, `kind` is a `counts` name (`broadcasts_pending` excluded) |
| `shellui_email_scheduled_job_last_success_timestamp_seconds` | gauge | `job` | Unix time of the last successful run (0 before the first one) |
| `shellui_email_scheduled_job_last_run_timestamp_seconds` | gauge | `job` | Unix time the last run started (0 before the first one) |
| `shellui_email_scheduled_job_last_run_duration_seconds` | gauge | `job` | Duration of the last finished run |
| `shellui_email_scheduled_job_overdue` | gauge | `job` | 1 when overdue (see the table above) |
| `shellui_email_scheduler_enabled` | gauge | none | 1 when `SCHEDULER_ENABLED` is true |
| `shellui_email_scheduler_redis_up` | gauge | none | 1 when the broker answers `PING`. Absent without a broker |
| `shellui_email_scheduler_beat_last_seen_timestamp_seconds` | gauge | none | Unix time beat last published a job. Absent before the first one |
| `shellui_email_lane_worker_up` | gauge | `lane` | 1 when a worker of the lane polled within 60 seconds. Absent without a broker |
| `shellui_email_lane_worker_last_seen_timestamp_seconds` | gauge | `lane` | Unix time a worker of the lane last polled. Absent before the first poll |

Suggested alert rules:

```yaml
groups:
  - name: email-service
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
      - alert: EmailLaneWorkerDown
        expr: shellui_email_lane_worker_up == 0
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "no email-service worker is sending the {{ $labels.lane }} lane"
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

The overdue alert covers a stopped beat, a stuck Celery worker, and a missing cron line alike. `shellui_email_queue_oldest_age_seconds` (see [Metrics](metrics.md)) is a second signal for stuck lanes. Scrape every 30 to 60 seconds: each scrape runs a few indexed queries and two Redis calls.

## Related

- [Lanes](lanes.md)
- [Broadcasts](broadcasts.md)
- [Configuration](configuration.md)
- [Metrics](metrics.md)
