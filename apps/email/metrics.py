"""Prometheus metrics for lane health. Computed from the database at scrape time."""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, Min
from django.utils import timezone
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest

from apps.email.models import Message

METRICS_CONTENT_TYPE = CONTENT_TYPE_LATEST


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * quantile))))
    return ordered[index]


def metrics_http_body(*, company_id: int | None = None) -> bytes:
    registry = CollectorRegistry()
    depth = Gauge(
        'shellui_email_queue_depth',
        'Queued and retrying messages per lane.',
        ['lane'],
        registry=registry,
    )
    age = Gauge(
        'shellui_email_queue_oldest_age_seconds',
        'Age in seconds of the oldest queued message per lane.',
        ['lane'],
        registry=registry,
    )
    latency = Gauge(
        'shellui_email_send_latency_seconds',
        'Accept-to-handoff latency over messages sent in the last 24 hours.',
        ['lane', 'quantile'],
        registry=registry,
    )
    provider_errors = Gauge(
        'shellui_email_provider_errors',
        'Provider errors recorded in the last 24 hours.',
        ['provider', 'error_code'],
        registry=registry,
    )
    expiries = Gauge(
        'shellui_email_auth_ttl_expiries',
        'Auth-lane messages expired before provider handoff.',
        registry=registry,
    )
    now = timezone.now()
    base = Message.objects.all()
    if company_id is not None:
        base = base.filter(company_id=company_id)
    for lane_name in ('auth', 'transactional', 'bulk'):
        depth.labels(lane=lane_name).set(0)
        age.labels(lane=lane_name).set(0)
    queued = base.filter(status__in=[Message.STATUS_QUEUED, Message.STATUS_RETRYING])
    for row in queued.values('lane').annotate(n=Count('id'), oldest=Min('accepted_at')):
        depth.labels(lane=row['lane']).set(row['n'])
        if row['oldest']:
            age.labels(lane=row['lane']).set(max(0, (now - row['oldest']).total_seconds()))
    day_ago = now - timedelta(hours=24)
    sent = base.filter(sent_at__isnull=False, sent_at__gte=day_ago)
    samples: dict[str, list[float]] = {}
    for message in sent.only('lane', 'accepted_at', 'sent_at'):
        if message.sent_at and message.accepted_at:
            samples.setdefault(message.lane, []).append((message.sent_at - message.accepted_at).total_seconds())
    for lane, values in samples.items():
        latency.labels(lane=lane, quantile='0.5').set(_percentile(values, 0.5))
        latency.labels(lane=lane, quantile='0.95').set(_percentile(values, 0.95))
    errors = (
        base.filter(accepted_at__gte=day_ago)
        .exclude(last_error_code='')
        .values('provider', 'last_error_code')
        .annotate(n=Count('id'))
    )
    for row in errors:
        provider_errors.labels(
            provider=row['provider'] or 'unknown',
            error_code=row['last_error_code'],
        ).set(row['n'])
    expiries.set(base.filter(lane='auth', status=Message.STATUS_EXPIRED).count())
    return generate_latest(registry)
