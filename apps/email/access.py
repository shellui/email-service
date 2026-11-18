"""Admin scope: staff, or the company owner whose token company_id matches."""

from __future__ import annotations

from apps.authapi.mcp_agent import resolve_company_id_for_request
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
    return resolve_company_id_for_request(request, allow_null=allow_null)
