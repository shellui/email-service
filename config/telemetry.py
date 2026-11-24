"""OpenTelemetry setup for every process that serves or sends mail.

Importing this module does not import the OpenTelemetry SDK. :func:`configure_telemetry`
loads it only when an OTLP endpoint is set, or when a test passes an exporter.
"""

from __future__ import annotations

import atexit
import fcntl
import logging
import os
import tempfile
import tomllib
from pathlib import Path

logger = logging.getLogger(__name__)

SERVICE_NAME = 'email-service'
_ROOT = Path(__file__).resolve().parent.parent
_lock_fh = None
_lock_pid: int | None = None
_shutdown_registered = False


def service_version() -> str:
    with (_ROOT / 'pyproject.toml').open('rb') as handle:
        data = tomllib.load(handle)
    version = str(data.get('project', {}).get('version', '')).strip()
    return version or '0'


def _resource_value(resource: str, key: str) -> str | None:
    for item in resource.split(','):
        name, separator, value = item.strip().partition('=')
        if separator and name.strip() == key and value.strip():
            return value.strip()
    return None


def gauge_publisher() -> bool:
    """True for the one process that should export database gauges.

    Gunicorn, Celery, and the lane workers all read the same rows. Publishing
    from each of them would copy every gauge once per process. The process that
    takes this lock publishes. When it exits, the next export takes the lock.
    """
    global _lock_fh, _lock_pid
    pid = os.getpid()
    if _lock_fh is not None and _lock_pid == pid:
        return True
    if _lock_pid is not None and _lock_pid != pid:
        # Inherited across fork. Closing the fd would release the parent's lock.
        _lock_fh = None
        _lock_pid = None
    path = os.path.join(tempfile.gettempdir(), 'email-service-otel-gauges.lock')
    try:
        handle = open(path, 'a+')  # noqa: SIM115 - the fd is held so the lock stays taken
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return False
    except OSError:
        logger.warning('OpenTelemetry gauge lock is unavailable; this process will publish gauges')
        return True
    _lock_fh = handle
    _lock_pid = pid
    return True


def configure_telemetry() -> bool:
    """Enable traces, metrics, and log correlation. Inert without an OTLP endpoint."""
    endpoint = os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT', '').strip()
    headers = os.getenv('OTEL_EXPORTER_OTLP_HEADERS', '').strip()
    protocol = os.getenv('OTEL_EXPORTER_OTLP_PROTOCOL', '').strip()
    service_name = os.getenv('OTEL_SERVICE_NAME', '').strip() or SERVICE_NAME
    resource = os.getenv('OTEL_RESOURCE_ATTRIBUTES', '')
    # Headers can carry collector credentials. Never log them.
    logger.debug(
        'telemetry endpoint=%s protocol=%s headers_set=%s',
        'set' if endpoint else 'unset',
        protocol or 'default',
        bool(headers),
    )
    from shellui_django.telemetry import setup_telemetry

    started = setup_telemetry(
        service_name,
        service_version=service_version(),
        environment=(
            _resource_value(resource, 'deployment.environment')
            or _resource_value(resource, 'deployment.environment.name')
        ),
        region=_resource_value(resource, 'cloud.region'),
    )
    if not started:
        return False
    _register_metrics_if_ready()
    _register_shutdown()
    return True


def _register_metrics_if_ready() -> None:
    from django.apps import apps

    if not apps.ready:
        return
    from apps.email.metrics import register_observable_metrics

    register_observable_metrics()


def _register_shutdown() -> None:
    global _shutdown_registered
    if _shutdown_registered:
        return
    from shellui_django.telemetry import shutdown_telemetry

    atexit.register(shutdown_telemetry)
    _shutdown_registered = True
