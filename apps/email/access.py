"""Admin scope: staff, or the company owner whose token company_id matches."""

from __future__ import annotations

from apps.authapi.service_auth import ServicePrincipal
from apps.email.service import SendError


def require_admin(request, company_id, *, allow_platform: bool = False):
    user = request.user
    if not user or not getattr(user, 'is_authenticated', False) or isinstance(user, ServicePrincipal):
        raise SendError(401, 'unauthorized')
    if allow_platform and company_id is None:
        if not getattr(user, 'is_staff', False):
            raise SendError(403, 'forbidden')
        return user
    if company_id is None:
        raise SendError(400, 'validation_failed', {'company_id': ['required']})
    token_company = getattr(user, 'company_id', None)
    if getattr(user, 'is_staff', False):
        return user
    if token_company is None:
        raise SendError(403, 'forbidden')
    if int(token_company) != int(company_id):
        raise SendError(403, 'company_mismatch')
    if getattr(user, 'is_company_owner', False):
        return user
    raise SendError(403, 'forbidden')


def require_staff(request):
    user = request.user
    if not user or not getattr(user, 'is_authenticated', False) or isinstance(user, ServicePrincipal):
        raise SendError(401, 'unauthorized')
    if not getattr(user, 'is_staff', False):
        raise SendError(403, 'forbidden')
    return user


def company_from_request(request, *, allow_null: bool = False) -> int | None:
    raw = None
    if hasattr(request, 'query_params'):
        raw = request.query_params.get('company_id')
    if raw in (None, '') and isinstance(getattr(request, 'data', None), dict):
        if 'company_id' in request.data:
            raw = request.data.get('company_id')
    if raw in (None, ''):
        user = request.user
        if getattr(user, 'company_id', None) is not None and not getattr(user, 'is_service', False):
            return int(user.company_id)
        if allow_null:
            return None
        raise SendError(400, 'validation_failed', {'company_id': ['required']})
    if raw is None and allow_null:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise SendError(400, 'validation_failed', {'company_id': ['invalid']})
