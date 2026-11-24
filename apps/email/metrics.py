"""OpenTelemetry metrics for lane health. Values are read from the database at export time."""

from __future__ import annotations

import time
from datetime import timedelta

from django.db.models import Count, Min
from django.utils import timezone
from shellui_django.telemetry import canonical_metric_name

from apps.email.models import FallbackUsage, Message

QUEUE_DEPTH = canonical_metric_name('shellui_email_queue_depth')
QUEUE_OLDEST_AGE = canonical_metric_name('shellui_email_queue_oldest_age_seconds')
SEND_LATENCY = canonical_metric_name('shellui_email_send_latency_seconds')
PROVIDER_ERRORS = canonical_metric_name('shellui_email_provider_errors')
AUTH_TTL_EXPIRIES = canonical_metric_name('shellui_email_auth_ttl_expiries')
FALLBACK_SENT = canonical_metric_name('shellui_email_fallback_sent_today')
FALLBACK_REFUSED = canonical_metric_name('shellui_email_fallback_refused_today')

LANES = ('auth', 'transactional', 'bulk')
_CACHE_SECONDS = 1.0
_registered_generation = -1
_cached_at = 0.0
_cached_samples: list[Sample] | None = None


class Sample:
    """One observable point. Attribute values are strings, matching the old labels."""

    def __init__(self, name: str, value: float, attributes: dict[str, str] | None = None):
        self.name = name
        self.value = value
        self.attributes = attributes or {}


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * quantile))))
    return ordered[index]


def queue_samples() -> list[Sample]:
    """Queue, latency, provider error, auth expiry, and fallback gauges.

    These match the old Prometheus scrape of ``GET /api/v1/metrics`` with no
    company filter: deployment-wide, with the same labels. Per-company counts
    stay on ``GET /api/v1/stats``.
    """
    now = timezone.now()
    base = Message.objects.all()
    depth = {lane: 0 for lane in LANES}
    age = {lane: 0.0 for lane in LANES}
    queued = base.filter(status__in=[Message.STATUS_QUEUED, Message.STATUS_RETRYING])
    for row in queued.values('lane').annotate(n=Count('id'), oldest=Min('accepted_at')):
        lane = row['lane']
        if lane not in depth:
            continue
        depth[lane] = row['n']
        if row['oldest']:
            age[lane] = max(0.0, (now - row['oldest']).total_seconds())
    samples = [Sample(QUEUE_DEPTH, depth[lane], {'lane': lane}) for lane in LANES]
    samples.extend(Sample(QUEUE_OLDEST_AGE, age[lane], {'lane': lane}) for lane in LANES)
    day_ago = now - timedelta(hours=24)
    sent = base.filter(sent_at__isnull=False, sent_at__gte=day_ago)
    latencies: dict[str, list[float]] = {}
    for message in sent.only('lane', 'accepted_at', 'sent_at'):
        if message.sent_at and message.accepted_at:
            latencies.setdefault(message.lane, []).append(
                (message.sent_at - message.accepted_at).total_seconds()
            )
    for lane, values in latencies.items():
        samples.append(Sample(SEND_LATENCY, _percentile(values, 0.5), {'lane': lane, 'quantile': '0.5'}))
        samples.append(Sample(SEND_LATENCY, _percentile(values, 0.95), {'lane': lane, 'quantile': '0.95'}))
    errors = (
        base.filter(accepted_at__gte=day_ago)
        .exclude(last_error_code='')
        .values('provider', 'last_error_code')
        .annotate(n=Count('id'))
    )
    for row in errors:
        samples.append(
            Sample(
                PROVIDER_ERRORS,
                row['n'],
                {'provider': row['provider'] or 'unknown', 'error_code': row['last_error_code']},
            )
        )
    samples.append(
        Sample(AUTH_TTL_EXPIRIES, base.filter(lane='auth', status=Message.STATUS_EXPIRED).count())
    )
    usage = FallbackUsage.objects.filter(company_id=0, day=now.date()).first()
    samples.append(Sample(FALLBACK_SENT, usage.sent if usage else 0))
    samples.append(Sample(FALLBACK_REFUSED, usage.refused if usage else 0))
    return samples


def _samples() -> list[Sample]:
    global _cached_at, _cached_samples
    now = time.monotonic()
    if _cached_samples is not None and now - _cached_at < _CACHE_SECONDS:
        return _cached_samples
    _cached_samples = queue_samples()
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


def register_observable_metrics() -> None:
    """Create the observable instruments once per meter generation."""
    global _registered_generation
    from shellui_django.telemetry import get_meter
    from shellui_django.telemetry._state import state

    if not state.active or _registered_generation == state.generation:
        return
    meter = get_meter('shellui.email')
    if not hasattr(meter, 'create_observable_gauge'):
        return
    meter.create_observable_gauge(
        QUEUE_DEPTH,
        callbacks=[_callback(QUEUE_DEPTH)],
        description='Queued and retrying messages per lane.',
    )
    meter.create_observable_gauge(
        QUEUE_OLDEST_AGE,
        callbacks=[_callback(QUEUE_OLDEST_AGE)],
        unit='s',
        description='Age in seconds of the oldest queued message per lane.',
    )
    meter.create_observable_gauge(
        SEND_LATENCY,
        callbacks=[_callback(SEND_LATENCY)],
        unit='s',
        description='Accept-to-handoff latency over messages sent in the last 24 hours.',
    )
    meter.create_observable_gauge(
        PROVIDER_ERRORS,
        callbacks=[_callback(PROVIDER_ERRORS)],
        description='Provider errors recorded in the last 24 hours.',
    )
    meter.create_observable_gauge(
        AUTH_TTL_EXPIRIES,
        callbacks=[_callback(AUTH_TTL_EXPIRIES)],
        description='Auth-lane messages expired before provider handoff.',
    )
    meter.create_observable_gauge(
        FALLBACK_SENT,
        callbacks=[_callback(FALLBACK_SENT)],
        description='Mail accepted today (UTC) on the Shellui fallback provider.',
    )
    meter.create_observable_gauge(
        FALLBACK_REFUSED,
        callbacks=[_callback(FALLBACK_REFUSED)],
        description='Mail refused today (UTC) by the Shellui fallback provider caps or kill switch.',
    )
    from apps.actions.scheduled_job_metrics import register_scheduled_job_metrics

    register_scheduled_job_metrics(meter)
    _registered_generation = state.generation
