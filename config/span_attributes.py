"""Attach opaque Shellui ids to the current span. Emails and tokens are not recorded."""

from __future__ import annotations


class ShelluiSpanMiddleware:
    """Set company, user, and agent ids after the view, while the server span is open."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, 'user', None)
        if getattr(user, 'is_authenticated', False) and not getattr(user, 'is_service', False):
            from apps.email.tracing import tag_span

            claims = getattr(user, 'claims', None)
            agent_id = claims.get('agent_id') if isinstance(claims, dict) else None
            user_id = getattr(user, 'user_id', None)
            if agent_id and user_id == 0:
                user_id = None
            tag_span(
                company_id=getattr(user, 'company_id', None),
                user_id=user_id,
                agent_id=agent_id,
            )
        return response
