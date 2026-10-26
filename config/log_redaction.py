"""Remove tokens from URL paths before they reach logs or Sentry.

Confirmation (``/n/confirm/<token>``) and unsubscribe (``/u/<token>``) links carry a
token in the path. Logs keep the route and drop the token.
"""

from __future__ import annotations

import logging
import re

_TOKEN_PATH_RE = re.compile(r'(/(?:n/confirm|u)/)[^/?#\s]+')


def redact_path(value: str | None) -> str:
    return _TOKEN_PATH_RE.sub(r'\1[redacted]', value or '')


class RedactTokenPathsFilter(logging.Filter):
    """``django.request`` logs ``Not Found: <path>`` and similar lines."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_path(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact_path(arg) if isinstance(arg, str) else arg for arg in record.args)
        return True
