"""Per-company delivery counts for the admin dashboard."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.email.models import Message

COUNT_KEYS = (
    'sent',
    'delivered',
    'bounced',
    'complained',
    'expired',
    'failed',
    'queued',
    'suppressed',
    'cancelled',
)


def _window(raw_from: str | None, raw_to: str | None) -> tuple[datetime, datetime]:
    end = parse_datetime(raw_to) if raw_to else timezone.now()
    start = parse_datetime(raw_from) if raw_from else end - timedelta(days=30)
    if start is None or end is None:
        end = timezone.now()
        start = end - timedelta(days=30)
    if timezone.is_naive(start):
        start = timezone.make_aware(start, timezone.utc)
    if timezone.is_naive(end):
        end = timezone.make_aware(end, timezone.utc)
    return start, end


def _bucket(status: str) -> str | None:
    if status in Message.PROVIDER_ACCEPTED:
        if status == Message.STATUS_SENT:
            return 'sent'
        return status
    if status in COUNT_KEYS:
        return status
    return None


def _empty() -> dict[str, int]:
    return {key: 0 for key in COUNT_KEYS}


def company_stats(
    company_id: int,
    *,
    raw_from: str | None = None,
    raw_to: str | None = None,
    lane: str | None = None,
    event_type: str | None = None,
) -> dict:
    start, end = _window(raw_from, raw_to)
    qs = Message.objects.filter(company_id=company_id, accepted_at__gte=start, accepted_at__lte=end)
    if lane:
        qs = qs.filter(lane=lane)
    if event_type:
        qs = qs.filter(event_type=event_type)
    totals = _empty()
    by_lane: dict[str, dict[str, int]] = defaultdict(_empty)
    by_event: dict[str, dict[str, int]] = defaultdict(_empty)
    by_day: dict[str, dict[str, int]] = defaultdict(_empty)
    rows = qs.values('status', 'lane', 'event_type').annotate(n=Count('id'))
    for row in rows:
        bucket = _bucket(row['status'])
        if bucket is None:
            continue
        count = row['n']
        totals[bucket] += count
        if row['status'] in Message.PROVIDER_ACCEPTED and bucket != 'sent':
            totals['sent'] += count
            by_lane[row['lane']]['sent'] += count
            by_event[row['event_type'] or row['lane']]['sent'] += count
        by_lane[row['lane']][bucket] += count
        by_event[row['event_type'] or ''][bucket] += count
    day_rows = (
        qs.annotate(day=TruncDate('accepted_at'))
        .values('day', 'status')
        .annotate(n=Count('id'))
    )
    for row in day_rows:
        bucket = _bucket(row['status'])
        if bucket is None or row['day'] is None:
            continue
        key = row['day'].isoformat()
        by_day[key][bucket] += row['n']
        if row['status'] in Message.PROVIDER_ACCEPTED and bucket != 'sent':
            by_day[key]['sent'] += row['n']
    return {
        'company_id': company_id,
        'from': start.isoformat().replace('+00:00', 'Z'),
        'to': end.isoformat().replace('+00:00', 'Z'),
        'totals': totals,
        'by_lane': dict(by_lane),
        'by_event': dict(by_event),
        'by_day': [{'day': day, **counts} for day, counts in sorted(by_day.items())],
    }
