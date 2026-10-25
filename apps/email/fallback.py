"""Shellui fallback provider for companies without a provider of their own.

New projects can sign in from day one: the platform provider sends their auth
mail with Shellui's wording, from ``DEFAULT_FROM_EMAIL``, to the one address the
auth event names. Per-recipient, per-company and global caps keep it from
becoming a spam relay. Companies in ``EMAIL_PLATFORM_COMPANY_IDS`` are Shellui
itself and are not capped here.
"""

from __future__ import annotations

import logging
from html import escape

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.email.models import CompanyProfile, CompanyProvider, FallbackUsage

logger = logging.getLogger(__name__)

GLOBAL = 0

FOOTER = {
    'en': 'Sent by Shellui on behalf of {name}.',
    'fr': 'Envoyé par Shellui pour le compte de {name}.',
}


class FallbackRefused(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def applies(company_id: int) -> bool:
    """True when this company's mail goes through the Shellui fallback with its caps."""
    if int(company_id) in settings.EMAIL_PLATFORM_COMPANY_IDS:
        return False
    return not CompanyProvider.objects.filter(company_id=company_id, configured=True).exists()


def disabled(company_id: int) -> bool:
    return CompanyProfile.objects.filter(company_id=company_id, fallback_disabled=True).exists()


def _today():
    return timezone.now().date()


def _refuse(company_id: int, code: str) -> None:
    day = _today()
    for key in (company_id, GLOBAL):
        FallbackUsage.objects.get_or_create(company_id=key, day=day)
        FallbackUsage.objects.filter(company_id=key, day=day).update(refused=F('refused') + 1)
    logger.warning('fallback send refused company_id=%s reason=%s', company_id, code)
    raise FallbackRefused(code)


def consume(company_id: int, hmac_value: str) -> None:
    """Count one auth send on the fallback, or raise ``FallbackRefused``. Call only when ``applies``."""
    from apps.email.service import _rate_allow

    if disabled(company_id):
        _refuse(company_id, 'fallback_disabled')
    hour = timezone.now().strftime('%Y%m%d%H')
    if not _rate_allow(
        f'email:fallback:{company_id}:{hmac_value}:{hour}', settings.EMAIL_FALLBACK_RECIPIENT_HOURLY_LIMIT, 3700
    ):
        _refuse(company_id, 'fallback_recipient_limit')
    day = _today()
    limits = {GLOBAL: settings.EMAIL_FALLBACK_GLOBAL_DAILY_LIMIT, company_id: settings.EMAIL_FALLBACK_COMPANY_DAILY_LIMIT}
    for key in limits:
        FallbackUsage.objects.get_or_create(company_id=key, day=day)
    code = ''
    with transaction.atomic():
        # Lock the global row first so concurrent sends always lock in the same order.
        rows = {
            row.company_id: row
            for row in FallbackUsage.objects.select_for_update()
            .filter(company_id__in=list(limits), day=day)
            .order_by('company_id')
        }
        for key, limit in limits.items():
            if limit > 0 and rows[key].sent >= limit:
                code = 'fallback_global_limit' if key == GLOBAL else 'fallback_company_limit'
                break
        if not code:
            FallbackUsage.objects.filter(company_id__in=list(limits), day=day).update(sent=F('sent') + 1)
    if code:
        _refuse(company_id, code)


def usage(company_id: int) -> dict:
    """Today's fallback use for the company API, so admin can show it."""
    day = _today()
    rows = dict(
        FallbackUsage.objects.filter(company_id__in=[company_id, GLOBAL], day=day).values_list('company_id', 'sent')
    )
    return {
        'applies': applies(company_id),
        'disabled': disabled(company_id),
        'day': day.isoformat(),
        'used': rows.get(company_id, 0),
        'limit': settings.EMAIL_FALLBACK_COMPANY_DAILY_LIMIT,
        'recipient_hourly_limit': settings.EMAIL_FALLBACK_RECIPIENT_HOURLY_LIMIT,
    }


def add_footer(html: str, text: str, company_name: str, language: str) -> tuple[str, str]:
    """Append one line naming the project the mail is for."""
    line = (FOOTER.get(language) or FOOTER['en']).format(name=company_name or 'a Shellui project')
    block = (
        '<p style="font-size:12px;line-height:1.5;color:rgb(120,122,128);margin:24px 0 0;'
        f'font-family:Inter,system-ui,Arial,sans-serif">{escape(line)}</p>'
    )
    lower = html.lower()
    at = lower.rfind('</body>')
    html = html[:at] + block + html[at:] if at >= 0 else html + block
    return html, f'{text.rstrip()}\n\n{line}\n'
