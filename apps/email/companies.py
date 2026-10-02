"""Company display names used when a caller omits ``company_name``."""

from __future__ import annotations

from django.conf import settings

from apps.email.models import CompanyProfile, CompanyProvider


def remember_company_name(company_id: int, name: str) -> None:
    cleaned = (name or '').strip()
    if not cleaned:
        return
    CompanyProfile.objects.update_or_create(
        company_id=company_id,
        defaults={'name': cleaned[:255]},
    )


def company_name_may_be_stored(principal, company_id: int) -> bool:
    """Identity is the source of truth. A key for every company must not write the name.

    A service key may store the name when it is the identity key, or when
    ``allowed_company_ids`` is exactly this company. Hosting and storage keys
    that can address every company use the name for that message only.
    """
    if not getattr(principal, 'is_service', False):
        return False
    if getattr(principal, 'service', '') == 'identity':
        return True
    allowed = getattr(principal, 'allowed_company_ids', None)
    if not allowed or len(allowed) != 1:
        return False
    return int(allowed[0]) == int(company_id)


def resolve_company_name(company_id: int) -> str:
    """Name from a previous caller, then the company provider From name."""
    profile = CompanyProfile.objects.filter(company_id=company_id).first()
    if profile and profile.name.strip():
        return profile.name.strip()
    provider = CompanyProvider.objects.filter(company_id=company_id, configured=True).first()
    if provider is None:
        return ''
    name = (provider.from_name or '').strip()
    default_name = (settings.DEFAULT_FROM_NAME or '').strip()
    if not name or name.lower() == default_name.lower():
        return ''
    return name


def apply_company_name(company_id: int, variables: dict, *, store: bool = False) -> dict:
    """Fill ``company_name`` when the caller left it out.

    A name on this request is used for the message. It is written to
    ``CompanyProfile`` only when ``store`` is true.
    """
    current = str(variables.get('company_name') or '').strip()
    if current:
        if store:
            remember_company_name(company_id, current)
        variables['company_name'] = current
        return variables
    resolved = resolve_company_name(company_id)
    if resolved:
        variables['company_name'] = resolved
    else:
        variables.pop('company_name', None)
    return variables
