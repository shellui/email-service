"""Gunicorn access logs without secrets in URLs.

Confirmation (``/n/confirm/<token>``) and unsubscribe (``/u/<token>``) links carry a
token in the path. The access log keeps the route and drops the token. The Referer is
never logged, and neither are credential headers.
"""

from __future__ import annotations

from config.log_redaction import redact_path
from config.safe_access_log import SafeAccessLogger

_DROPPED = ('referer', 'authorization', 'cookie')


class RedactingLogger(SafeAccessLogger):
    def atoms(self, resp, req, environ, request_time):
        atoms = super().atoms(resp, req, environ, request_time)
        atoms['r'] = redact_path(atoms.get('r'))
        atoms['U'] = redact_path(atoms.get('U'))
        atoms['f'] = '-'
        # Header ({...}i, {...}o) and environ ({...}e) atoms.
        for key in [key for key in atoms if key.startswith('{')]:
            if any(word in key for word in _DROPPED):
                del atoms[key]
            elif isinstance(atoms[key], str):
                atoms[key] = redact_path(atoms[key])
        return atoms
