"""Liveness probe for load balancers and the Docker HEALTHCHECK (tools/healthcheck.py)."""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse

_LIVENESS_PATH = '/health/live'


class LivenessMiddleware:
    """Answer GET /health/live first: no host check, https redirect, session, DB or Redis."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.method == 'GET' and request.path.rstrip('/') == _LIVENESS_PATH:
            return HttpResponse('ok', content_type='text/plain')
        return self.get_response(request)
