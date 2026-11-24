---
title: Metrics
sidebar_label: Metrics
description: Admin statistics and the OpenTelemetry metrics email-service exports.
---

# Metrics

## Admin stats

`GET /api/v1/stats?company_id=`

Auth: staff or company owner.

Counts: `sent`, `delivered`, `bounced`, `complained`, `expired`, `failed`, `queued`, `suppressed`, `cancelled`.

Grouped by `by_lane`, `by_event` (catalog `event_type`), and `by_day`. Query `from`, `to`, `lane`, and `event_type` narrow the window. The default window is 30 days.

`sent` includes every status the provider accepted. A later `delivered` or `bounced` row still counts as `sent`.

`skipped` counts accepted events that queued no message: `no_recipients`, `no_rule`, and `rule_disabled` (always 0).

## OpenTelemetry

Traces, metrics, and log correlation come from `shellui_django.telemetry`. Nothing is exported until `OTEL_EXPORTER_OTLP_ENDPOINT` is set. `OTEL_SDK_DISABLED=true` leaves telemetry off even when an endpoint is set.

`service.name` defaults to `email-service`. `service.version` is the package version. `OTEL_SERVICE_NAME` and `OTEL_RESOURCE_ATTRIBUTES` override those. Set `deployment.environment` and `cloud.region` in `OTEL_RESOURCE_ATTRIBUTES`, for example `deployment.environment=production,cloud.region=eu`.

The protocol is `http/protobuf` (`OTEL_EXPORTER_OTLP_PROTOCOL`). `OTEL_EXPORTER_OTLP_HEADERS` carries collector credentials, such as SigNoz basic auth. Do not wrap the process with `opentelemetry-instrument`. Gunicorn and the Celery thread pool call the setup themselves, and so does each `manage.py run_email_worker` lane.

Outbound `requests` calls, including Resend and Amazon SES, send W3C `traceparent`. Celery task headers do the same. A message stores the `traceparent` of the request that queued it. The lane worker's `email.send` span and the provider span continue that trace. Span attributes are the lane, template id, provider name, and status, plus `shellui.company_id` and `shellui.user_id` when those ids exist. Recipient addresses, subjects, and message bodies are not span attributes. Database and Redis calls made outside a span, such as an idle lane worker poll, a gauge callback, or a heartbeat, are not exported. A poll that finds a message still records it under `email.send`.

Log lines include `trace_id` and `span_id` (both `0` when there is no span) and still include `req=` for `X-Request-ID`.

`GET /api/v1/metrics` and `GET /api/v1/metrics/all` are removed. The `pat_agm` claim is not read.

### Instruments

Gauges are computed from the database and Redis on the export interval (`OTEL_METRIC_EXPORT_INTERVAL`, default 60000 ms). One process publishes them, so a restart moves the series instead of multiplying it by the number of workers. Names are the old Prometheus names with `_total` removed and `_` turned into `.`.

| Instrument | Type | Attributes | Meaning |
| --- | --- | --- | --- |
| `shellui.email.queue.depth` | gauge | `lane` | `queued` and `retrying` messages. Empty lanes are 0 |
| `shellui.email.queue.oldest.age.seconds` | gauge | `lane` | Age of the oldest queued message, in seconds. Empty lanes are 0 |
| `shellui.email.send.latency.seconds` | gauge | `lane`, `quantile` (`0.5`, `0.95`) | Accept-to-handoff over the last 24 hours. Absent when that lane sent nothing |
| `shellui.email.provider.errors` | gauge | `provider`, `error_code` | Errors recorded in the last 24 hours |
| `shellui.email.auth.ttl.expiries` | gauge | none | Auth messages that expired before handoff |
| `shellui.email.fallback.sent.today` | gauge | none | Mail accepted today (UTC) on the Shellui fallback provider (the global counter) |
| `shellui.email.fallback.refused.today` | gauge | none | Mail refused today (UTC) by the fallback caps or kill switch (the global counter) |

Scheduled job and lane worker instruments are in [Scheduled jobs](scheduled-jobs.md#opentelemetry-metrics). There is no `company_id` attribute on these instruments. Per-company counts stay on `GET /api/v1/stats`.
