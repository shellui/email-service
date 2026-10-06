"""
Prometheus metrics for the scheduled jobs and lane workers, in the global ``GET /api/v1/metrics`` only.

Runs happen in the Celery worker or a cron process, not in gunicorn, so values are read
from the database at scrape time (``ScheduledJobCounter``, ``ScheduledJobState``) instead
of in-process counters. Heartbeats come from Redis. A dedicated registry keeps them out of
company-scoped scrapes. Labels: ``job``, ``status``, ``kind`` and ``lane`` only.
"""

from __future__ import annotations

from django.conf import settings
from django.utils import timezone
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from apps.actions.models import ScheduledJobCounter
from apps.actions.scheduled_jobs import (
    JOBS,
    RUN_STATUSES,
    beat_last_seen,
    ensure_states,
    is_overdue,
    redis_reachable,
    scheduler_enabled,
)

PREFIX = 'shellui_email_scheduled_job'

METRIC_NAMES = (
    f'{PREFIX}_runs_total',
    f'{PREFIX}_items_total',
    f'{PREFIX}_last_success_timestamp_seconds',
    f'{PREFIX}_last_run_timestamp_seconds',
    f'{PREFIX}_last_run_duration_seconds',
    f'{PREFIX}_overdue',
    'shellui_email_scheduler_enabled',
    'shellui_email_scheduler_redis_up',
    'shellui_email_scheduler_beat_last_seen_timestamp_seconds',
    'shellui_email_lane_worker_up',
    'shellui_email_lane_worker_last_seen_timestamp_seconds',
)


class ScheduledJobsCollector:
    def collect(self):
        now = timezone.now()
        states = ensure_states()
        counters = {(c.job, c.name): c.value for c in ScheduledJobCounter.objects.filter(job__in=list(JOBS))}

        runs = CounterMetricFamily(
            f'{PREFIX}_runs',
            'Finished scheduled job runs by status (succeeded, failed, skipped_locked).',
            labels=('job', 'status'),
        )
        items = CounterMetricFamily(
            f'{PREFIX}_items',
            'Items processed by scheduled job runs, by kind.',
            labels=('job', 'kind'),
        )
        last_success = GaugeMetricFamily(
            f'{PREFIX}_last_success_timestamp_seconds',
            'Unix time when the last successful run finished (0 before the first one).',
            labels=('job',),
        )
        last_run = GaugeMetricFamily(
            f'{PREFIX}_last_run_timestamp_seconds',
            'Unix time when the last run started (0 before the first one).',
            labels=('job',),
        )
        duration = GaugeMetricFamily(
            f'{PREFIX}_last_run_duration_seconds',
            'Duration of the last finished run.',
            labels=('job',),
        )
        overdue = GaugeMetricFamily(
            f'{PREFIX}_overdue',
            '1 when the last successful run is older than 3 times the job interval.',
            labels=('job',),
        )
        for name, spec in JOBS.items():
            state = states[name]
            for st in RUN_STATUSES:
                runs.add_metric([name, st], counters.get((name, f'runs.{st}'), 0))
            for kind in spec.item_kinds:
                items.add_metric([name, kind], counters.get((name, f'items.{kind}'), 0))
            last_success.add_metric([name], state.last_success_at.timestamp() if state.last_success_at else 0)
            last_run.add_metric([name], state.last_started_at.timestamp() if state.last_started_at else 0)
            if state.last_duration_ms is not None:
                duration.add_metric([name], state.last_duration_ms / 1000.0)
            overdue.add_metric([name], 1 if is_overdue(spec, state, now) else 0)
        yield from (runs, items, last_success, last_run, duration, overdue)

        yield GaugeMetricFamily(
            'shellui_email_scheduler_enabled',
            '1 when the in-container Celery worker and beat run (SCHEDULER_ENABLED).',
            value=1 if scheduler_enabled() else 0,
        )
        reachable = redis_reachable()
        if reachable is not None:
            yield GaugeMetricFamily(
                'shellui_email_scheduler_redis_up',
                '1 when the scheduler broker (Redis) answers PING.',
                value=1 if reachable else 0,
            )
        beat = beat_last_seen() if reachable else None
        if beat is not None:
            yield GaugeMetricFamily(
                'shellui_email_scheduler_beat_last_seen_timestamp_seconds',
                'Unix time when Celery beat last published a scheduled job.',
                value=beat.timestamp(),
            )
        if reachable and getattr(settings, 'CELERY_BROKER_URL', ''):
            yield from _lane_worker_metrics(now)


def _lane_worker_metrics(now):
    from apps.email.worker_heartbeat import LANES, WORKER_STALE_AFTER, read_worker_heartbeats

    seen = read_worker_heartbeats()
    up = GaugeMetricFamily(
        'shellui_email_lane_worker_up',
        '1 when a worker of the lane polled within the last 60 seconds.',
        labels=('lane',),
    )
    last = GaugeMetricFamily(
        'shellui_email_lane_worker_last_seen_timestamp_seconds',
        'Unix time when a worker of the lane last polled. Absent before the first poll.',
        labels=('lane',),
    )
    stale_after = WORKER_STALE_AFTER.total_seconds()
    for lane in LANES:
        ts = seen.get(lane)
        up.add_metric([lane], 1 if ts is not None and now.timestamp() - ts <= stale_after else 0)
        if ts is not None:
            last.add_metric([lane], ts)
    yield up
    yield last


_registry = CollectorRegistry(auto_describe=False)
_registry.register(ScheduledJobsCollector())


def scheduled_jobs_metrics_body() -> bytes:
    return generate_latest(_registry)
