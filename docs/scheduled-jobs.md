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
INFO [config.scheduled_tasks] sweep_email_queue: expired=0 released=0 broadcasts=0
```

A failed run is logged as an error and reported to Sentry when `SENTRY_DSN` is set.

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

## Related

- [Lanes](lanes.md)
- [Broadcasts](broadcasts.md)
- [Configuration](configuration.md)
