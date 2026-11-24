"""
OpenTelemetry metrics for the scheduled jobs and lane workers.

Runs happen in the Celery worker or a cron process, so values are read from
``ScheduledJobCounter``, ``ScheduledJobState``, and Redis at export time.
Labels: ``job``, ``status``, ``kind``, and ``lane`` only (low cardinality).
"""

from __future__ import annotations

import time

from django.conf import settings
from django.utils import timezone
from shellui_django.telemetry import canonical_metric_name

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
from apps.email.metrics import Sample

RUNS = canonical_metric_name('shellui_email_scheduled_job_runs_total')
ITEMS = canonical_metric_name('shellui_email_scheduled_job_items_total')
LAST_SUCCESS = canonical_metric_name('shellui_email_scheduled_job_last_success_timestamp_seconds')
LAST_RUN = canonical_metric_name('shellui_email_scheduled_job_last_run_timestamp_seconds')
DURATION = canonical_metric_name('shellui_email_scheduled_job_last_run_duration_seconds')
OVERDUE = canonical_metric_name('shellui_email_scheduled_job_overdue')
SCHEDULER_ENABLED = canonical_metric_name('shellui_email_scheduler_enabled')
REDIS_UP = canonical_metric_name('shellui_email_scheduler_redis_up')
BEAT_LAST_SEEN = canonical_metric_name('shellui_email_scheduler_beat_last_seen_timestamp_seconds')
WORKER_UP = canonical_metric_name('shellui_email_lane_worker_up')
WORKER_LAST_SEEN = canonical_metric_name('shellui_email_lane_worker_last_seen_timestamp_seconds')

_CACHE_SECONDS = 1.0
_cached_at = 0.0
_cached_samples: list[Sample] | None = None


def scheduled_job_samples() -> list[Sample]:
    now = timezone.now()
    states = ensure_states()
    counters = {(row.job, row.name): row.value for row in ScheduledJobCounter.objects.filter(job__in=list(JOBS))}
    samples: list[Sample] = []
    for name, spec in JOBS.items():
        state = states[name]
        for status in RUN_STATUSES:
            samples.append(
                Sample(RUNS, counters.get((name, f'runs.{status}'), 0), {'job': name, 'status': status})
            )
        for kind in spec.item_kinds:
            samples.append(
                Sample(ITEMS, counters.get((name, f'items.{kind}'), 0), {'job': name, 'kind': kind})
            )
        samples.append(
            Sample(
                LAST_SUCCESS,
                state.last_success_at.timestamp() if state.last_success_at else 0,
                {'job': name},
            )
        )
        samples.append(
            Sample(
                LAST_RUN,
                state.last_started_at.timestamp() if state.last_started_at else 0,
                {'job': name},
            )
        )
        if state.last_duration_ms is not None:
            samples.append(Sample(DURATION, state.last_duration_ms / 1000.0, {'job': name}))
        samples.append(Sample(OVERDUE, 1 if is_overdue(spec, state, now) else 0, {'job': name}))
    samples.append(Sample(SCHEDULER_ENABLED, 1 if scheduler_enabled() else 0))
    reachable = redis_reachable()
    if reachable is not None:
        samples.append(Sample(REDIS_UP, 1 if reachable else 0))
    beat = beat_last_seen() if reachable else None
    if beat is not None:
        samples.append(Sample(BEAT_LAST_SEEN, beat.timestamp()))
    if reachable and getattr(settings, 'CELERY_BROKER_URL', ''):
        samples.extend(_lane_worker_samples(now))
    return samples


def _lane_worker_samples(now):
    from apps.email.worker_heartbeat import (
        LANES,
        WORKER_STALE_AFTER,
        read_worker_heartbeats,
    )

    seen = read_worker_heartbeats()
    stale_after = WORKER_STALE_AFTER.total_seconds()
    samples = []
    for lane in LANES:
        ts = seen.get(lane)
        up = 1 if ts is not None and now.timestamp() - ts <= stale_after else 0
        samples.append(Sample(WORKER_UP, up, {'lane': lane}))
        if ts is not None:
            samples.append(Sample(WORKER_LAST_SEEN, ts, {'lane': lane}))
    return samples


def _samples() -> list[Sample]:
    global _cached_at, _cached_samples
    now = time.monotonic()
    if _cached_samples is not None and now - _cached_at < _CACHE_SECONDS:
        return _cached_samples
    _cached_samples = scheduled_job_samples()
    _cached_at = now
    return _cached_samples


def _callback(name: str):
    def observe(_options):
        from opentelemetry.metrics import Observation

        from config.telemetry import gauge_publisher

        if not gauge_publisher():
            return []
        return [Observation(sample.value, sample.attributes) for sample in _samples() if sample.name == name]

    return observe


def register_scheduled_job_metrics(meter) -> None:
    meter.create_observable_counter(
        RUNS,
        callbacks=[_callback(RUNS)],
        description='Finished scheduled job runs by status (succeeded, failed, skipped_locked).',
    )
    meter.create_observable_counter(
        ITEMS,
        callbacks=[_callback(ITEMS)],
        description='Items processed by scheduled job runs, by kind.',
    )
    meter.create_observable_gauge(
        LAST_SUCCESS,
        callbacks=[_callback(LAST_SUCCESS)],
        unit='s',
        description='Unix time when the last successful run finished (0 before the first one).',
    )
    meter.create_observable_gauge(
        LAST_RUN,
        callbacks=[_callback(LAST_RUN)],
        unit='s',
        description='Unix time when the last run started (0 before the first one).',
    )
    meter.create_observable_gauge(
        DURATION,
        callbacks=[_callback(DURATION)],
        unit='s',
        description='Duration of the last finished run.',
    )
    meter.create_observable_gauge(
        OVERDUE,
        callbacks=[_callback(OVERDUE)],
        description='1 when the last successful run is older than 3 times the job interval.',
    )
    meter.create_observable_gauge(
        SCHEDULER_ENABLED,
        callbacks=[_callback(SCHEDULER_ENABLED)],
        description='1 when the in-container Celery worker and beat run (SCHEDULER_ENABLED).',
    )
    meter.create_observable_gauge(
        REDIS_UP,
        callbacks=[_callback(REDIS_UP)],
        description='1 when the scheduler broker (Redis) answers PING.',
    )
    meter.create_observable_gauge(
        BEAT_LAST_SEEN,
        callbacks=[_callback(BEAT_LAST_SEEN)],
        unit='s',
        description='Unix time when Celery beat last published a scheduled job.',
    )
    meter.create_observable_gauge(
        WORKER_UP,
        callbacks=[_callback(WORKER_UP)],
        description='1 when a worker of the lane polled within the last 60 seconds.',
    )
    meter.create_observable_gauge(
        WORKER_LAST_SEEN,
        callbacks=[_callback(WORKER_LAST_SEEN)],
        unit='s',
        description='Unix time when a worker of the lane last polled. Absent before the first poll.',
    )
