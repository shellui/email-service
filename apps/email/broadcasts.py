"""Broadcasts: one saved email sent to an audience of the company's users, each in their language."""

from __future__ import annotations

import copy
import logging
import secrets
import time
from datetime import date, timedelta
from html import escape, unescape

import requests
from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from apps.email.catalog import LANE_BULK
from apps.email.companies import apply_company_name
from apps.email.crypto import decrypt_json, email_hmac, encrypt_json, mask_email
from apps.email.models import (
    Broadcast,
    BroadcastRecipient,
    CompanyProvider,
    EmailTemplate,
    LibraryTemplate,
    Message,
    Suppression,
    Unsubscribe,
)
from apps.email.service import (
    EMAIL_RE,
    SendError,
    _lane_paused,
    _notify,
    _platform_credentials,
    _queue_message,
    assets_url,
    deliver_due,
    models_company,
)
from apps.email.substitution import HTML_TOKEN_RE, TOKEN_RE
from apps.providers.credentials import strip_internal_credentials
from apps.providers.resend import ResendMarketing, ResendMarketingError, format_from

logger = logging.getLogger(__name__)

ROLES = ('owner', 'staff', 'member')
ACCESS = ('enabled', 'disabled', 'any')
DATE_FIELDS = ('joined_after', 'joined_before', 'seen_after', 'seen_before')
MAX_PICKED = 1000
IDENTITY_PAGE_SIZE = 1000
SAMPLE_SIZE = 5
QUEUE_CHUNK = 500

# Resend fills these per contact; the rest is the same for every recipient.
RESEND_TOKENS = {
    'first_name': 'contact.first_name',
    'last_name': 'contact.last_name',
    'recipient_email': 'contact.email',
}
RESEND_UNSUBSCRIBE_TOKENS = {'system.unsubscribe_url', 'system.preferences_url'}

MESSAGE_BUCKETS = {
    Message.STATUS_QUEUED: BroadcastRecipient.STATUS_QUEUED,
    Message.STATUS_SENDING: BroadcastRecipient.STATUS_QUEUED,
    Message.STATUS_RETRYING: BroadcastRecipient.STATUS_QUEUED,
    Message.STATUS_SENT: BroadcastRecipient.STATUS_SENT,
    'delivery_delayed': BroadcastRecipient.STATUS_SENT,
    Message.STATUS_DELIVERED: BroadcastRecipient.STATUS_DELIVERED,
    Message.STATUS_BOUNCED: BroadcastRecipient.STATUS_BOUNCED,
    Message.STATUS_COMPLAINED: BroadcastRecipient.STATUS_COMPLAINED,
}
COUNT_KEYS = (
    BroadcastRecipient.STATUS_PENDING,
    BroadcastRecipient.STATUS_UNSUBSCRIBED,
    BroadcastRecipient.STATUS_SUPPRESSED,
    BroadcastRecipient.STATUS_QUEUED,
    BroadcastRecipient.STATUS_SENT,
    BroadcastRecipient.STATUS_DELIVERED,
    BroadcastRecipient.STATUS_BOUNCED,
    BroadcastRecipient.STATUS_COMPLAINED,
    BroadcastRecipient.STATUS_FAILED,
)


def _invalid(field: str, code: str = 'invalid') -> SendError:
    return SendError(400, 'validation_failed', {f'audience.{field}': [code]})


def _int_list(raw, field: str, *, limit: int | None = None) -> list[int]:
    if raw in (None, ''):
        return []
    if not isinstance(raw, list):
        raise _invalid(field)
    values = []
    for item in raw:
        try:
            value = int(item)
        except (TypeError, ValueError):
            raise _invalid(field)
        if value <= 0:
            raise _invalid(field)
        if value not in values:
            values.append(value)
    if limit is not None and len(values) > limit:
        raise _invalid(field, 'too_many')
    return values


def _empty_audience(mode: str) -> dict:
    return {
        'mode': mode,
        'group_ids': [],
        'roles': [],
        'access': 'enabled',
        **{field: '' for field in DATE_FIELDS},
        'user_ids': [],
        'emails': [],
    }


def clean_audience(raw) -> dict:
    """``mode`` is ``filter`` (every member matching the filters), ``pick`` (chosen users and addresses)
    or ``newsletter`` (confirmed subscribers of ``list_id``)."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise SendError(400, 'validation_failed', {'audience': ['invalid']})
    mode = raw.get('mode') or 'filter'
    if mode not in {'filter', 'pick', 'newsletter'}:
        raise _invalid('mode')
    if mode == 'newsletter':
        try:
            list_id = int(raw.get('list_id'))
        except (TypeError, ValueError):
            raise _invalid('list_id', 'required')
        if list_id <= 0:
            raise _invalid('list_id')
        return {**_empty_audience(mode), 'list_id': list_id}
    roles = raw.get('roles') or []
    if not isinstance(roles, list) or any(role not in ROLES for role in roles):
        raise _invalid('roles')
    access = raw.get('access') or 'enabled'
    if access not in ACCESS:
        raise _invalid('access')
    dates = {}
    for field in DATE_FIELDS:
        value = raw.get(field) or ''
        if value:
            try:
                date.fromisoformat(str(value))
            except ValueError:
                raise _invalid(field)
        dates[field] = str(value)
    emails_raw = raw.get('emails') or []
    if not isinstance(emails_raw, list):
        raise _invalid('emails')
    emails: list[str] = []
    for item in emails_raw:
        email = str(item or '').strip()
        if not EMAIL_RE.match(email) or len(email) > 254:
            raise _invalid('emails')
        if email.lower() not in {existing.lower() for existing in emails}:
            emails.append(email)
    if len(emails) > MAX_PICKED:
        raise _invalid('emails', 'too_many')
    return {
        'mode': mode,
        'group_ids': _int_list(raw.get('group_ids'), 'group_ids', limit=100),
        'roles': [role for role in ROLES if role in roles],
        'access': access,
        **dates,
        'user_ids': _int_list(raw.get('user_ids'), 'user_ids', limit=MAX_PICKED),
        'emails': emails,
    }


def _identity_params(audience: dict) -> dict | None:
    """Query for identity's audience endpoint, or None when identity has nothing to add."""
    if audience['mode'] == 'pick':
        if not audience['user_ids']:
            return None
        return {'user_ids': ','.join(str(pk) for pk in audience['user_ids'])}
    params = {'access': audience['access']}
    if audience['group_ids']:
        params['group_ids'] = ','.join(str(pk) for pk in audience['group_ids'])
    if audience['roles']:
        params['roles'] = ','.join(audience['roles'])
    for field in DATE_FIELDS:
        if audience[field]:
            params[field] = audience[field]
    return params


def _identity_page(authorization: str, params: dict) -> dict:
    try:
        response = requests.get(
            f'{settings.IDENTITY_SERVICE_URL}/api/v1/users/audience',
            params=params,
            headers={'Authorization': authorization, 'Accept': 'application/json'},
            timeout=15,
        )
    except requests.RequestException:
        raise SendError(502, 'identity_unavailable')
    if response.status_code in {401, 403}:
        raise SendError(403, 'forbidden')
    if response.status_code == 400:
        try:
            field = str(response.json().get('field') or 'audience')
        except ValueError:
            field = 'audience'
        raise _invalid(field)
    if response.status_code != 200:
        raise SendError(502, 'identity_unavailable')
    try:
        body = response.json()
    except ValueError:
        raise SendError(502, 'identity_unavailable')
    if not isinstance(body, dict):
        raise SendError(502, 'identity_unavailable')
    return body


def audience_newsletter(company_id: int, audience: dict):
    """The company's list a newsletter audience points at, or None for other modes."""
    if audience.get('mode') != 'newsletter':
        return None
    from apps.email.models import NewsletterList

    newsletter = NewsletterList.objects.filter(pk=audience.get('list_id'), company_id=company_id).first()
    if newsletter is None:
        raise _invalid('list_id')
    return newsletter


def fetch_audience(authorization: str, audience: dict, *, main_language: str, company_id: int) -> list[dict]:
    """Recipients as ``{email, user_id, first_name, last_name, language, hmac}``, one per address."""
    limit = settings.EMAIL_BROADCAST_MAX_RECIPIENTS
    recipients: list[dict] = []
    seen: set[str] = set()

    def add(row: dict) -> None:
        hmac_value = email_hmac(row['email'])
        if hmac_value in seen:
            return
        seen.add(hmac_value)
        recipients.append({**row, 'hmac': hmac_value})

    newsletter = audience_newsletter(company_id, audience)
    if newsletter is not None:
        from apps.email.newsletters import confirmed_recipients

        for row in confirmed_recipients(newsletter):
            add({**row, 'language': row['language'] or main_language})
        if len(recipients) > limit:
            raise SendError(400, 'audience_too_large', extra={'limit': limit})
        return recipients

    params = _identity_params(audience)
    if params is not None:
        if not settings.IDENTITY_SERVICE_URL:
            raise SendError(503, 'identity_not_configured')
        page = 1
        while True:
            body = _identity_page(authorization, {**params, 'page': page, 'page_size': IDENTITY_PAGE_SIZE})
            if int(body.get('count') or 0) + len(audience['emails']) > limit:
                raise SendError(400, 'audience_too_large', extra={'limit': limit})
            rows = body.get('results') or []
            for row in rows:
                email = str(row.get('email') or '').strip()
                if not EMAIL_RE.match(email):
                    continue
                add(
                    {
                        'email': email,
                        'user_id': row.get('id'),
                        'first_name': str(row.get('first_name') or '')[:150],
                        'last_name': str(row.get('last_name') or '')[:150],
                        'language': str(row.get('language') or main_language)[:8],
                    }
                )
            if len(rows) < IDENTITY_PAGE_SIZE:
                break
            page += 1
    for email in audience['emails']:
        add({'email': email, 'user_id': None, 'first_name': '', 'last_name': '', 'language': main_language})
    if len(recipients) > limit:
        raise SendError(400, 'audience_too_large', extra={'limit': limit})
    return recipients


def _hmacs_in_chunks(hmacs: list[str]):
    for start in range(0, len(hmacs), 500):
        yield hmacs[start : start + 500]


def unsubscribed_hmacs(company_id: int, hmacs: list[str], audience: dict | None = None) -> set[str]:
    """Company-wide bulk unsubscribes. A newsletter audience is already only confirmed subscribers: a
    company-wide unsubscribe marks them unsubscribed when it happens, and confirming again later wins."""
    if audience and audience.get('mode') == 'newsletter':
        return set()
    found: set[str] = set()
    for chunk in _hmacs_in_chunks(hmacs):
        found |= set(
            Unsubscribe.objects.filter(company_id=company_id, category=LANE_BULK, email_hmac__in=chunk).values_list(
                'email_hmac', flat=True
            )
        )
    return found


def suppressed_hmacs(company_id: int, hmacs: list[str]) -> set[str]:
    """Addresses the bulk lane must skip. Same rule as ``service.suppressed``."""
    now = timezone.now()
    found: set[str] = set()
    for chunk in _hmacs_in_chunks(hmacs):
        for row in Suppression.objects.filter(email_hmac__in=chunk).filter(models_company(company_id)):
            if row.expires_at and row.expires_at <= now:
                continue
            if row.lanes and LANE_BULK not in row.lanes:
                continue
            found.add(row.email_hmac)
    return found


def template_languages(template: EmailTemplate) -> set[str]:
    from apps.email.rules import representative_version

    version = representative_version(template)
    translations = set((version.translations or {}) if version else {})
    return {template.language or 'en'} | translations


def _send_language(language: str, available: set[str], main: str) -> str:
    return language if language in available else main


def sender_for(company_id: int) -> dict:
    """Provider, credentials, and From for bulk mail, or the reason there is none."""
    company = CompanyProvider.objects.filter(company_id=company_id, configured=True).first()
    if company is not None:
        if company.provider == 'smtp' and not settings.EMAIL_ALLOW_COMPANY_SMTP:
            raise SendError(409, 'company_smtp_disabled')
        from_email = company.bulk_from_email
        if not from_email and int(company_id) in settings.EMAIL_PLATFORM_COMPANY_IDS:
            from_email = settings.BULK_FROM_EMAIL
        if not from_email:
            raise SendError(409, 'bulk_sender_required')
        credentials = strip_internal_credentials(
            decrypt_json(company.credentials_ciphertext, setting='EMAIL_CREDENTIALS_KEY')
        )
        provider = company.provider
        from_name = company.from_name or settings.DEFAULT_FROM_NAME
    else:
        if int(company_id) not in settings.EMAIL_PLATFORM_COMPANY_IDS:
            raise SendError(409, 'platform_sender_not_allowed')
        provider = settings.EMAIL_FALLBACK_PROVIDER or 'none'
        credentials = _platform_credentials(provider)
        if credentials is None:
            raise SendError(503, 'provider_not_configured')
        from_email = settings.BULK_FROM_EMAIL
        from_name = settings.BULK_FROM_NAME
    delivery = Broadcast.DELIVERY_RESEND if provider == 'resend' else Broadcast.DELIVERY_BULK_LANE
    return {
        'provider': provider,
        'credentials': credentials,
        'from_email': from_email,
        'from_name': from_name,
        'delivery': delivery,
    }


def sender_summary(company_id: int) -> dict:
    try:
        sender = sender_for(company_id)
    except SendError as exc:
        return {'from_email': '', 'from_name': '', 'delivery': '', 'error_code': exc.code}
    return {
        'from_email': sender['from_email'],
        'from_name': sender['from_name'],
        'delivery': sender['delivery'],
        'error_code': '',
    }


def preview_audience(company_id: int, authorization: str, audience: dict, template: EmailTemplate) -> dict:
    main = template.language or 'en'
    available = template_languages(template)
    recipients = fetch_audience(authorization, audience, main_language=main, company_id=company_id)
    hmacs = [row['hmac'] for row in recipients]
    unsubscribed = unsubscribed_hmacs(company_id, hmacs, audience)
    suppressed = suppressed_hmacs(company_id, hmacs) - unsubscribed
    languages: dict[str, int] = {}
    sendable = []
    for row in recipients:
        if row['hmac'] in unsubscribed or row['hmac'] in suppressed:
            continue
        sendable.append(row)
        language = _send_language(row['language'], available, main)
        languages[language] = languages.get(language, 0) + 1
    return {
        'total': len(recipients),
        'sendable': len(sendable),
        'unsubscribed': len(unsubscribed),
        'suppressed': len(suppressed),
        'languages': languages,
        'samples': [mask_email(row['email']) for row in sendable[:SAMPLE_SIZE]],
    }


def _with_unsubscribe_link(document: dict) -> dict:
    from apps.email.rendering import document_tokens

    if 'system.unsubscribe_url' in document_tokens(document, '', ''):
        return document
    adapted = copy.deepcopy(document)
    adapted.setdefault('content', []).append(
        {
            'type': 'paragraph',
            'attrs': {'alignment': 'center'},
            'content': [
                {
                    'type': 'text',
                    'marks': [{'type': 'link', 'attrs': {'href': '{{ system.unsubscribe_url }}'}}],
                    'text': 'Unsubscribe',
                }
            ],
        }
    )
    return adapted


def create_broadcast(company_id: int, data: dict, user_id: int | None) -> Broadcast:
    from apps.email.catalog import broadcast_definition
    from apps.email.document import adapt_to_event
    from apps.email.rules import create_version, publish_version

    if not isinstance(data, dict):
        raise SendError(400, 'validation_failed')
    name = str(data.get('name') or '').strip()
    if not name:
        raise SendError(400, 'validation_failed', {'name': ['required']})
    source = LibraryTemplate.objects.filter(key=str(data.get('source_key') or '')).first()
    if source is None or (source.company_id is not None and source.company_id != company_id):
        raise SendError(400, 'validation_failed', {'source_key': ['invalid']})
    language = str(data.get('language') or 'en')[:8]
    with transaction.atomic():
        template = EmailTemplate.objects.create(
            template_key='company.' + secrets.token_hex(6),
            company_id=company_id,
            language=language,
            name=name[:120],
            event_type='',
            kind=EmailTemplate.KIND_BROADCAST,
            source_key=source.key,
            set=source.set,
        )
        document = _with_unsubscribe_link(adapt_to_event(source.document, broadcast_definition()))
        version = create_version(
            template,
            document=document,
            subject=source.subject or name,
            preheader=source.preheader or '',
            theme=source.theme or data.get('theme'),
            user_id=user_id,
        )
        publish_version(template, version)
        audience = clean_audience(data.get('audience'))
        audience_newsletter(company_id, audience)
        return Broadcast.objects.create(
            company_id=company_id,
            name=name[:120],
            template=template,
            audience=audience,
            created_by_user_id=user_id,
        )


def update_broadcast(broadcast: Broadcast, data: dict) -> Broadcast:
    if broadcast.state != Broadcast.STATE_DRAFT:
        raise SendError(409, 'broadcast_not_draft')
    if not isinstance(data, dict):
        raise SendError(400, 'validation_failed')
    fields = ['updated_at']
    if 'name' in data:
        name = str(data.get('name') or '').strip()
        if not name:
            raise SendError(400, 'validation_failed', {'name': ['required']})
        broadcast.name = name[:120]
        broadcast.template.name = broadcast.name
        broadcast.template.save(update_fields=['name'])
        fields.append('name')
    if 'audience' in data:
        audience = clean_audience(data.get('audience'))
        audience_newsletter(broadcast.company_id, audience)
        broadcast.audience = audience
        fields.append('audience')
    broadcast.save(update_fields=fields)
    return broadcast


def send_broadcast(broadcast: Broadcast, authorization: str) -> Broadcast:
    """Snapshot the audience, then hand the broadcast to the bulk worker."""
    if broadcast.state != Broadcast.STATE_DRAFT:
        raise SendError(409, 'broadcast_not_draft')
    template = broadcast.template
    if not template.active_version:
        raise SendError(409, 'template_not_published')
    sender = sender_for(broadcast.company_id)
    if _lane_paused(LANE_BULK, broadcast.company_id):
        raise SendError(409, 'lane_paused')
    audience = clean_audience(broadcast.audience)
    main = template.language or 'en'
    available = template_languages(template)
    recipients = fetch_audience(authorization, audience, main_language=main, company_id=broadcast.company_id)
    if not recipients:
        raise SendError(400, 'audience_empty')
    hmacs = [row['hmac'] for row in recipients]
    unsubscribed = unsubscribed_hmacs(broadcast.company_id, hmacs, audience)
    suppressed = suppressed_hmacs(broadcast.company_id, hmacs)
    rows = []
    for row in recipients:
        if row['hmac'] in unsubscribed:
            status = BroadcastRecipient.STATUS_UNSUBSCRIBED
        elif row['hmac'] in suppressed:
            status = BroadcastRecipient.STATUS_SUPPRESSED
        else:
            status = BroadcastRecipient.STATUS_PENDING
        contact = ''
        if status == BroadcastRecipient.STATUS_PENDING:
            contact = encrypt_json(
                {'email': row['email'], 'first_name': row['first_name'], 'last_name': row['last_name']}
            )
        user_id = row.get('user_id')
        rows.append(
            BroadcastRecipient(
                broadcast=broadcast,
                user_id=int(user_id) if isinstance(user_id, int) else None,
                contact_ciphertext=contact,
                email_hmac=row['hmac'],
                language=_send_language(row['language'], available, main),
                status=status,
            )
        )
    with transaction.atomic():
        locked = Broadcast.objects.select_for_update().get(pk=broadcast.pk)
        if locked.state != Broadcast.STATE_DRAFT:
            raise SendError(409, 'broadcast_not_draft')
        BroadcastRecipient.objects.bulk_create(rows, batch_size=500)
        locked.audience = audience
        locked.state = Broadcast.STATE_QUEUED
        locked.delivery = sender['delivery']
        locked.from_email = sender['from_email']
        locked.template_version = template.active_version
        locked.last_error_code = ''
        locked.save()
    _notify(LANE_BULK)
    if settings.EMAIL_DELIVER_SYNC:
        for _ in range(100):
            if not process_broadcasts():
                break
            deliver_due(LANE_BULK, limit=QUEUE_CHUNK)
        deliver_due(LANE_BULK, limit=QUEUE_CHUNK)
    locked.refresh_from_db()
    return locked


def broadcast_counts(broadcast: Broadcast) -> dict:
    counts = {key: 0 for key in COUNT_KEYS}
    rows = BroadcastRecipient.objects.filter(broadcast=broadcast)
    if broadcast.delivery == Broadcast.DELIVERY_BULK_LANE:
        for row in rows.filter(message__isnull=True).values('status').annotate(total=Count('id')):
            counts[row['status']] = counts.get(row['status'], 0) + row['total']
        for row in rows.filter(message__isnull=False).values('message__status').annotate(total=Count('id')):
            bucket = MESSAGE_BUCKETS.get(row['message__status'], BroadcastRecipient.STATUS_FAILED)
            counts[bucket] += row['total']
    else:
        for row in rows.values('status').annotate(total=Count('id')):
            counts[row['status']] = counts.get(row['status'], 0) + row['total']
    counts['total'] = sum(counts[key] for key in COUNT_KEYS)
    return counts


def _iso(value) -> str | None:
    return value.isoformat().replace('+00:00', 'Z') if value else None


def broadcast_payload(broadcast: Broadcast, *, detail: bool = False) -> dict:
    payload = {
        'id': broadcast.pk,
        'name': broadcast.name,
        'state': broadcast.state,
        'delivery': broadcast.delivery,
        'from_email': broadcast.from_email,
        'template_id': broadcast.template_id,
        'template_version': broadcast.template_version,
        'language': broadcast.template.language,
        'audience': clean_audience(broadcast.audience),
        'counts': broadcast_counts(broadcast) if broadcast.state != Broadcast.STATE_DRAFT else None,
        'last_error_code': broadcast.last_error_code,
        'created_at': _iso(broadcast.created_at),
        'updated_at': _iso(broadcast.updated_at),
        'sent_at': _iso(broadcast.sent_at),
    }
    if detail:
        payload['sender'] = sender_summary(broadcast.company_id)
    return payload


def resend_content(text: str, values: dict, *, html: bool) -> str:
    """Our placeholders as Resend writes them: contact fields stay, everything else is filled in."""

    def replace(match) -> str:
        token = match.group(1)
        default = match.group(2)
        if html and default is not None:
            default = unescape(default)
        if token in RESEND_UNSUBSCRIBE_TOKENS:
            return '{{{RESEND_UNSUBSCRIBE_URL}}}'
        if token in RESEND_TOKENS:
            fallback = (default or '').replace('}', '').replace('|', '')
            return '{{{' + RESEND_TOKENS[token] + ('|' + fallback if fallback else '') + '}}}'
        value = values.get(token)
        if value in (None, ''):
            value = default or ''
        value = str(value)
        return escape(value, quote=True) if html else value.replace('\r', '').replace('\n', ' ')

    return (HTML_TOKEN_RE if html else TOKEN_RE).sub(replace, text or '')


_last_resend_call = [0.0]


def _throttle() -> None:
    rps = settings.EMAIL_RESEND_BROADCAST_RPS
    if rps <= 0:
        return
    wait = _last_resend_call[0] + 1 / rps - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_resend_call[0] = time.monotonic()


def _fail(broadcast: Broadcast, code: str) -> None:
    broadcast.state = Broadcast.STATE_FAILED
    broadcast.last_error_code = code
    broadcast.save(update_fields=['state', 'last_error_code', 'updated_at'])
    broadcast.recipients.filter(status=BroadcastRecipient.STATUS_PENDING).update(
        status=BroadcastRecipient.STATUS_FAILED, contact_ciphertext=''
    )


def _step_bulk_lane(broadcast: Broadcast, deadline: float) -> None:
    template = broadcast.template
    variables_base = apply_company_name(broadcast.company_id, {})
    audience = broadcast.audience or {}
    if audience.get('mode') == 'newsletter':
        from apps.email.newsletters import unsubscribe_category

        variables_base['system.unsubscribe_category'] = unsubscribe_category(audience['list_id'])
    queued_any = False
    while time.monotonic() < deadline:
        batch = list(
            broadcast.recipients.filter(status=BroadcastRecipient.STATUS_PENDING, message__isnull=True)[:QUEUE_CHUNK]
        )
        if not batch:
            break
        with transaction.atomic():
            for recipient in batch:
                contact = decrypt_json(recipient.contact_ciphertext)
                message = _queue_message(
                    company_id=broadcast.company_id,
                    service='broadcast',
                    send_request=None,
                    template_key=template.template_key,
                    version=broadcast.template_version,
                    language=recipient.language,
                    lane=LANE_BULK,
                    recipient={'email': contact['email'], 'user_id': recipient.user_id},
                    variables={
                        **variables_base,
                        'first_name': contact.get('first_name') or '',
                        'last_name': contact.get('last_name') or '',
                        'recipient_email': contact['email'],
                    },
                    expires_at=None,
                    event_type='broadcast',
                )
                recipient.message = message
                recipient.status = BroadcastRecipient.STATUS_QUEUED
                recipient.contact_ciphertext = ''
            BroadcastRecipient.objects.bulk_update(batch, ['message', 'status', 'contact_ciphertext'])
        queued_any = True
    if queued_any:
        _notify(LANE_BULK)
    if broadcast.recipients.filter(status=BroadcastRecipient.STATUS_PENDING).exists():
        broadcast.state = Broadcast.STATE_PREPARING
        broadcast.save(update_fields=['state', 'updated_at'])
        return
    in_flight = broadcast.recipients.filter(
        message__status__in=[Message.STATUS_QUEUED, Message.STATUS_SENDING, Message.STATUS_RETRYING]
    ).exists()
    if in_flight:
        if broadcast.state != Broadcast.STATE_SENDING:
            broadcast.state = Broadcast.STATE_SENDING
            broadcast.save(update_fields=['state', 'updated_at'])
        return
    broadcast.state = Broadcast.STATE_SENT
    broadcast.sent_at = broadcast.sent_at or timezone.now()
    broadcast.save(update_fields=['state', 'sent_at', 'updated_at'])


def _resend_message(broadcast: Broadcast, language: str) -> dict:
    from apps.email.rules import content_for_template

    content = content_for_template(broadcast.template, language)
    values = apply_company_name(broadcast.company_id, {})
    values['system.assets_url'] = assets_url()
    return {
        'subject': resend_content(content['subject'], values, html=False),
        'html': resend_content(content['html'], values, html=True),
        'text': resend_content(content['text'], values, html=False),
    }


def _step_resend(broadcast: Broadcast, deadline: float) -> None:
    sender = sender_for(broadcast.company_id)
    if sender['delivery'] != Broadcast.DELIVERY_RESEND:
        raise SendError(409, 'provider_changed')
    client = ResendMarketing(sender['credentials'].get('api_key') or '')
    from_header = format_from(sender['from_name'], broadcast.from_email or sender['from_email'])
    languages = list(
        broadcast.recipients.filter(status__in=[BroadcastRecipient.STATUS_PENDING, BroadcastRecipient.STATUS_QUEUED])
        .order_by()
        .values_list('language', flat=True)
        .distinct()
    )
    ids = dict(broadcast.resend_ids or {})
    remaining = [language for language in sorted(languages) if not (ids.get(language) or {}).get('broadcast_id')]
    if broadcast.state == Broadcast.STATE_QUEUED:
        broadcast.state = Broadcast.STATE_PREPARING
        broadcast.save(update_fields=['state', 'updated_at'])
    for language in remaining:
        entry = dict(ids.get(language) or {})
        if not entry.get('segment_id'):
            _throttle()
            entry['segment_id'] = client.create_segment(f'{broadcast.name} ({language}) #{broadcast.pk}')
            ids[language] = entry
            broadcast.resend_ids = ids
            broadcast.save(update_fields=['resend_ids', 'updated_at'])
        pending = broadcast.recipients.filter(language=language, status=BroadcastRecipient.STATUS_PENDING)
        for recipient in pending.iterator():
            if time.monotonic() >= deadline:
                return
            contact = decrypt_json(recipient.contact_ciphertext)
            _throttle()
            try:
                client.add_contact(
                    entry['segment_id'],
                    email=contact['email'],
                    first_name=contact.get('first_name') or '',
                    last_name=contact.get('last_name') or '',
                )
            except ResendMarketingError as exc:
                if exc.retryable or exc.code == 'provider_unauthorized':
                    raise
                recipient.status = BroadcastRecipient.STATUS_FAILED
                recipient.contact_ciphertext = ''
                recipient.save(update_fields=['status', 'contact_ciphertext'])
                continue
            recipient.status = BroadcastRecipient.STATUS_QUEUED
            recipient.save(update_fields=['status'])
        if not broadcast.recipients.filter(language=language, status=BroadcastRecipient.STATUS_QUEUED).exists():
            entry['broadcast_id'] = ''
            entry['skipped'] = True
            ids[language] = entry
            broadcast.resend_ids = ids
            broadcast.save(update_fields=['resend_ids', 'updated_at'])
            continue
        message = _resend_message(broadcast, language)
        _throttle()
        entry['broadcast_id'] = client.send_broadcast(
            segment_id=entry['segment_id'],
            from_header=from_header,
            subject=message['subject'],
            html=message['html'],
            text=message['text'],
            name=f'{broadcast.name} ({language})',
        )
        ids[language] = entry
        broadcast.resend_ids = ids
        broadcast.resend_lookup = ' '.join(
            value['broadcast_id'] for value in ids.values() if value.get('broadcast_id')
        )[:512]
        broadcast.state = Broadcast.STATE_SENDING
        broadcast.save(update_fields=['resend_ids', 'resend_lookup', 'state', 'updated_at'])
        broadcast.recipients.filter(language=language, status=BroadcastRecipient.STATUS_QUEUED).update(
            contact_ciphertext=''
        )
    broadcast.state = Broadcast.STATE_SENT
    broadcast.sent_at = broadcast.sent_at or timezone.now()
    broadcast.save(update_fields=['state', 'sent_at', 'updated_at'])


def _step(broadcast: Broadcast, deadline: float) -> None:
    try:
        if broadcast.delivery == Broadcast.DELIVERY_RESEND:
            _step_resend(broadcast, deadline)
        else:
            _step_bulk_lane(broadcast, deadline)
    except ResendMarketingError as exc:
        if exc.retryable:
            broadcast.last_error_code = exc.code
            broadcast.save(update_fields=['last_error_code', 'updated_at'])
            if exc.retry_after:
                time.sleep(min(exc.retry_after, 5))
            return
        _fail(broadcast, exc.code)
    except SendError as exc:
        _fail(broadcast, exc.code)


def process_broadcasts(*, budget_seconds: float | None = None) -> int:
    """Move every active broadcast forward. Returns how many still need work."""
    budget = settings.EMAIL_BROADCAST_STEP_SECONDS if budget_seconds is None else budget_seconds
    deadline = time.monotonic() + budget
    active = [Broadcast.STATE_QUEUED, Broadcast.STATE_PREPARING, Broadcast.STATE_SENDING]
    ids = list(Broadcast.objects.filter(state__in=active).order_by('created_at').values_list('pk', flat=True))
    for pk in ids:
        if time.monotonic() >= deadline:
            break
        with transaction.atomic():
            broadcast = (
                Broadcast.objects.select_for_update(skip_locked=True)
                .select_related('template')
                .filter(pk=pk, state__in=active)
                .first()
            )
            if broadcast is None:
                continue
            if _lane_paused(LANE_BULK, broadcast.company_id):
                continue
            _step(broadcast, deadline)
    return Broadcast.objects.filter(state__in=active).count()


RECIPIENT_RANK = {
    BroadcastRecipient.STATUS_QUEUED: 0,
    BroadcastRecipient.STATUS_SENT: 1,
    BroadcastRecipient.STATUS_DELIVERED: 2,
    BroadcastRecipient.STATUS_FAILED: 3,
    BroadcastRecipient.STATUS_BOUNCED: 3,
    BroadcastRecipient.STATUS_COMPLAINED: 4,
}
RESEND_EVENT_STATUS = {
    'email.sent': BroadcastRecipient.STATUS_SENT,
    'email.delivery_delayed': BroadcastRecipient.STATUS_SENT,
    'email.delivered': BroadcastRecipient.STATUS_DELIVERED,
    'email.bounced': BroadcastRecipient.STATUS_BOUNCED,
    'email.complained': BroadcastRecipient.STATUS_COMPLAINED,
    'email.failed': BroadcastRecipient.STATUS_FAILED,
    'email.suppressed': BroadcastRecipient.STATUS_FAILED,
}


def _recipient_address(data: dict) -> str:
    to = data.get('to')
    if isinstance(to, list):
        to = to[0] if to else ''
    return str(to or '').strip()


def apply_broadcast_event(event_name: str, data: dict) -> bool:
    """A Resend delivery event for one recipient of a broadcast."""
    status = RESEND_EVENT_STATUS.get(event_name)
    broadcast_id = str(data.get('broadcast_id') or '')
    address = _recipient_address(data)
    if status is None or not broadcast_id or not address:
        return False
    broadcast = Broadcast.objects.filter(resend_lookup__contains=broadcast_id).first()
    if broadcast is None:
        return False
    hmac_value = email_hmac(address)
    recipient = broadcast.recipients.filter(email_hmac=hmac_value).first()
    if recipient is None:
        return False
    if RECIPIENT_RANK.get(status, 0) > RECIPIENT_RANK.get(recipient.status, -1):
        recipient.status = status
        recipient.save(update_fields=['status'])
    bounce = data.get('bounce') if isinstance(data.get('bounce'), dict) else {}
    if status == BroadcastRecipient.STATUS_BOUNCED and str(bounce.get('type') or 'Permanent').lower() == 'permanent':
        Suppression.objects.get_or_create(
            company_id=broadcast.company_id,
            email_hmac=hmac_value,
            reason=Suppression.REASON_HARD_BOUNCE,
            defaults={
                'email_masked': mask_email(address),
                'lanes': [],
                'expires_at': timezone.now() + timedelta(days=30),
            },
        )
    if status == BroadcastRecipient.STATUS_COMPLAINED:
        Suppression.objects.get_or_create(
            company_id=broadcast.company_id,
            email_hmac=hmac_value,
            reason=Suppression.REASON_COMPLAINT,
            defaults={'email_masked': mask_email(address), 'lanes': ['transactional', 'bulk']},
        )
    return True


def apply_contact_unsubscribe(company_ids: list[int], data: dict) -> bool:
    """Someone used Resend's unsubscribe link. Contacts are per Resend account, so every company on it counts."""
    if data.get('unsubscribed') is not True:
        return False
    address = str(data.get('email') or '').strip()
    if not EMAIL_RE.match(address) or not company_ids:
        return False
    from apps.email.newsletters import unsubscribe_everywhere

    hmac_value = email_hmac(address)
    for company_id in company_ids:
        Unsubscribe.objects.get_or_create(
            company_id=company_id,
            email_hmac=hmac_value,
            category=LANE_BULK,
            defaults={'source': 'resend'},
        )
    unsubscribe_everywhere(company_ids, hmac_value, source='resend')
    return True
