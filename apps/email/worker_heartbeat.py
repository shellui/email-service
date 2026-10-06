"""
Lane worker heartbeat in Redis (the scheduler broker).

Each ``run_email_worker`` stores the time of its last poll, at most every
``HEARTBEAT_INTERVAL_SECONDS``, so the web process can tell a running worker from a
stopped one. One key per lane: with several workers on a lane, the latest poll wins.
Without a broker nothing is stored. See docs/scheduled-jobs.md#monitoring.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from django.conf import settings

logger = logging.getLogger(__name__)

LANES = ('auth', 'transactional', 'bulk')
HEARTBEAT_INTERVAL_SECONDS = 15
# Kept for a day so a stopped worker still shows its last poll.
HEARTBEAT_TTL = 86400
WORKER_STALE_AFTER = timedelta(seconds=60)


def worker_heartbeat_key(lane: str) -> str:
    return f'{settings.SCHEDULER_LOCK_PREFIX}:worker:{lane}:heartbeat'


def record_worker_heartbeat(lane: str, now: float | None = None, client=None) -> bool:
    """Never raises: a Redis error must not stop the worker from sending mail."""
    if client is None:
        if not getattr(settings, 'CELERY_BROKER_URL', ''):
            return False
        from config.task_lock import get_lock_client

        client = get_lock_client()
    try:
        client.set(
            worker_heartbeat_key(lane),
            f'{now if now is not None else time.time():.3f}',
            ex=HEARTBEAT_TTL,
        )
        return True
    except Exception:  # noqa: BLE001
        logger.warning('Could not store the %s lane worker heartbeat', lane)
        return False


def read_worker_heartbeats(client=None) -> dict[str, float]:
    """Unix time of the last poll per lane (lanes without a heartbeat are left out)."""
    if client is None:
        from config.task_lock import get_lock_client

        client = get_lock_client()
    try:
        raw = client.mget([worker_heartbeat_key(lane) for lane in LANES])
    except Exception:  # noqa: BLE001
        return {}
    seen = {}
    for lane, value in zip(LANES, raw):
        if value is None:
            continue
        try:
            seen[lane] = float(value)
        except (TypeError, ValueError):
            continue
    return seen


class HeartbeatThrottle:
    """Calls ``record_worker_heartbeat`` at most once per interval."""

    def __init__(self, lane: str, interval: float = HEARTBEAT_INTERVAL_SECONDS):
        self.lane = lane
        self.interval = interval
        self._last = None

    def beat(self, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        if self._last is not None and moment - self._last < self.interval:
            return False
        self._last = moment
        record_worker_heartbeat(self.lane)
        return True
