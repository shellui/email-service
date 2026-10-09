---
title: Metrics
sidebar_label: Metrics
description: Admin statistics and the Prometheus gauges on GET /api/v1/metrics.
---

# Metrics

## Admin stats

`GET /api/v1/stats?company_id=`

Auth: staff or company owner.

Counts: `sent`, `delivered`, `bounced`, `complained`, `expired`, `failed`, `queued`, `suppressed`, `cancelled`.

Grouped by `by_lane`, `by_event` (catalog `event_type`), and `by_day`. Query `from`, `to`, `lane`, and `event_type` narrow the window. The default window is 30 days.

`sent` includes every status the provider accepted. A later `delivered` or `bounced` row still counts as `sent`.

`skipped` counts accepted events that queued no message: `no_recipients`, `no_rule`, and `rule_disabled` (always 0).

## Prometheus

`GET /api/v1/metrics`

Auth: identity JWT, same scope as storage-service metrics (not public). Optional `company_id`.

The body is Prometheus text (`text/plain; version=0.0.4`). `Accept: text/plain` is accepted, including when it is listed with `application/json`. A missing `Accept` header is also accepted. `401` and `403` still return the JSON `error_code` body.

Gauges, computed from the database at scrape time:

| Name | Labels | Meaning |
| --- | --- | --- |
| `shellui_email_queue_depth` | `lane` | `queued` and `retrying` messages |
| `shellui_email_queue_oldest_age_seconds` | `lane` | Age of the oldest queued message |
| `shellui_email_send_latency_seconds` | `lane`, `quantile` (`0.5`, `0.95`) | Accept-to-handoff over the last 24 hours |
| `shellui_email_provider_errors` | `provider`, `error_code` | Errors recorded in the last 24 hours |
| `shellui_email_auth_ttl_expiries` | none | Auth messages that expired before handoff |

Empty lanes are reported as 0 for depth and age so a scrape still lists `auth`, `transactional`, and `bulk`.

The global scrape (no `company_id`, staff or `access_global_metrics`) also includes the scheduled job and lane worker metrics (`shellui_email_scheduled_job_*`, `shellui_email_scheduler_*`, `shellui_email_lane_worker_*`). See [Workers and scheduled jobs](scheduled-jobs.md#prometheus-metrics).
