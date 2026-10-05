"""Newsletters: lists people join from a public form with double opt-in. Subscribers need not be users."""

from __future__ import annotations

import csv
import hashlib
import io
import re
import secrets
from datetime import timedelta

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.actions.emit import emit_email_event
from apps.email.catalog import LANE_BULK, LANE_TRANSACTIONAL, LANGUAGES, newsletter_confirmation_definition
from apps.email.companies import apply_company_name
from apps.email.crypto import decrypt_json, decrypt_text, email_hmac, encrypt_json, encrypt_text, mask_email
from apps.email.models import CompanyProvider, EmailTemplate, LibraryTemplate, NewsletterList, NewsletterSubscriber
from apps.email.service import (
    EMAIL_RE,
    SendError,
    _check_rates,
    _notify,
    _platform_credentials,
    _queue_message,
    _rate_allow,
    _require_sender,
    deliver_due,
    suppressed,
)

PAGE_SIZE = 50
MAX_ORIGINS = 20
MAX_IMPORT_BYTES = 2 * 1024 * 1024
ORIGIN_RE = re.compile(r'^https?://[a-z0-9.-]+(:\d{1,5})?$')
SUBSCRIBE_PATH_RE = re.compile(r'^/api/v1/public/newsletters/(?P<key>[^/]+)/subscribe$')
UNSUBSCRIBE_PREFIX = 'list-'
ORIGINS_CACHE_SECONDS = 60
CSV_FIELDS = ('email', 'first_name', 'language', 'status', 'source', 'created_at', 'confirmed_at', 'unsubscribed_at')
MODE_CONFIRM = 'confirm'
MODE_CONSENTED = 'consented'

OUTCOME_SENT = 'sent'
OUTCOME_EXISTING = 'existing'
OUTCOME_THROTTLED = 'throttled'
OUTCOME_SUPPRESSED = 'suppressed'
OUTCOME_ADDED = 'added'
OUTCOME_UNSUBSCRIBED = 'skipped_unsubscribed'

# The default design's placeholder copy, rewritten for a newsletter and translated.
# ``(text the block starts with, textId, English, French)``.
CONFIRMATION_COPY = (
    ("We're almost there", 'nlhead01', 'Confirm your subscription', 'Confirmez votre inscription'),
    (
        'Thank you for signing up',
        'nlintro1',
        "Thanks for subscribing to {{ list_name }}. Confirm your email address and we'll start sending it to you.",
        'Merci de vous être inscrit à {{ list_name }}. Confirmez votre adresse e-mail pour commencer à la recevoir.',
    ),
    ('Confirm email', 'nlbutton', 'Confirm subscription', "Confirmer l'inscription"),
    (
        "If you didn't request this",
        'nlignore',
        "If you didn't sign up, ignore this email and you won't hear from us.",
        "Si vous ne vous êtes pas inscrit, ignorez cet e-mail et vous ne recevrez rien d'autre.",
    ),
)


def _iso(value) -> str | None:
    return value.isoformat().replace('+00:00', 'Z') if value else None


def _new_key() -> str:
    return 'nl_' + secrets.token_urlsafe(18)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _opaque(value: str) -> str:
    pepper = settings.EMAIL_HASH_PEPPER or ''
    return hashlib.sha256(f'{pepper}|{value}'.encode('utf-8')).hexdigest()[:32]


def unsubscribe_category(list_id: int) -> str:
    return f'{UNSUBSCRIBE_PREFIX}{list_id}'


def list_id_from_category(category: str) -> int | None:
    if not category.startswith(UNSUBSCRIBE_PREFIX):
        return None
    try:
        return int(category[len(UNSUBSCRIBE_PREFIX) :])
    except ValueError:
        return None


def confirm_url(token: str) -> str:
    return f'{settings.PUBLIC_BASE_URL}/n/confirm/{token}'


def subscribe_url(newsletter: NewsletterList) -> str:
    return f'{settings.PUBLIC_BASE_URL}/api/v1/public/newsletters/{newsletter.public_key}/subscribe'


# Confirmation template


def _block_text(node: dict) -> str:
    parts = []
    stack = [node]
    while stack:
        current = stack.pop()
        if not isinstance(current, dict):
            continue
        if current.get('type') == 'text':
            parts.append(str(current.get('text') or ''))
        stack.extend(reversed(current.get('content') or []))
    return ''.join(parts)


def _first_marks(node: dict):
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if current.get('type') == 'text':
                return current.get('marks')
            stack.extend(reversed(current.get('content') or []))
    return None


def _text_node(text: str, marks) -> dict:
    node = {'type': 'text', 'text': text}
    if marks:
        node['marks'] = marks
    return node


def _is_unsubscribe_block(node: dict) -> bool:
    from apps.email.document import links

    return node.get('type') == 'paragraph' and any('system.unsubscribe_url' in href for href in links(node))


def confirmation_document(source_document: dict) -> tuple[dict, dict]:
    """The default design adapted to the confirmation, and its French translation."""
    from apps.email.document import adapt_to_event

    document = adapt_to_event(source_document, newsletter_confirmation_definition())
    blocks: dict[str, dict] = {}

    def visit(node: dict) -> None:
        content = node.get('content')
        if isinstance(content, list):
            node['content'] = [child for child in content if not (isinstance(child, dict) and _is_unsubscribe_block(child))]
            for child in node['content']:
                if isinstance(child, dict):
                    visit(child)
        if node.get('type') not in {'paragraph', 'heading'}:
            return
        text = _block_text(node)
        for prefix, block_id, english, french in CONFIRMATION_COPY:
            if text.startswith(prefix):
                marks = _first_marks(node)
                node['content'] = [_text_node(english, marks)]
                node['attrs'] = {**(node.get('attrs') or {}), 'textId': block_id}
                blocks[block_id] = {'content': [_text_node(french, marks)], 'source': ''}
                return

    visit(document)
    return document, ({'fr': {'subject': '', 'preheader': '', 'blocks': blocks}} if blocks else {})


def _create_confirmation_template(company_id: int, name: str, theme, user_id: int | None) -> EmailTemplate:
    from apps.email.rules import create_version, publish_version

    definition = newsletter_confirmation_definition()
    source = LibraryTemplate.objects.filter(key=definition['default_template'], built_in=True).first()
    if source is None:
        raise SendError(503, 'library_not_synced')
    template = EmailTemplate.objects.create(
        template_key='company.' + secrets.token_hex(6),
        company_id=company_id,
        language='en',
        name=name[:120],
        event_type='',
        kind=EmailTemplate.KIND_NEWSLETTER_CONFIRMATION,
        source_key=source.key,
        set=source.set,
    )
    document, translations = confirmation_document(source.document)
    pack = definition['languages']['en']
    version = create_version(
        template,
        document=document,
        subject=pack['subject'],
        preheader=pack['preheader'],
        translations=translations,
        theme=source.theme or theme,
        user_id=user_id,
    )
    publish_version(template, version)
    return template


# Lists


def _invalid(field: str, code: str = 'invalid') -> SendError:
    return SendError(400, 'validation_failed', {field: [code]})


def _clean_origins(raw) -> list[str]:
    if raw in (None, ''):
        return []
    if not isinstance(raw, list):
        raise _invalid('allowed_origins')
    origins: list[str] = []
    for item in raw:
        origin = str(item or '').strip().rstrip('/').lower()
        if not ORIGIN_RE.match(origin):
            raise _invalid('allowed_origins')
        if origin not in origins:
            origins.append(origin)
    if len(origins) > MAX_ORIGINS:
        raise _invalid('allowed_origins', 'too_many')
    return origins


def _clean_redirect(raw) -> str:
    value = str(raw or '').strip()
    if not value:
        return ''
    lowered = value.lower()
    local = settings.DEBUG and lowered.startswith(('http://localhost', 'http://127.0.0.1'))
    if len(value) > 500 or not (lowered.startswith('https://') or local) or any(char.isspace() for char in value):
        raise _invalid('confirmed_redirect_url')
    return value


def _clean_language(raw) -> str:
    value = str(raw or 'en')
    if value not in LANGUAGES:
        raise _invalid('default_language')
    return value


def _forget_origins(public_key: str) -> None:
    cache.delete(f'email:nl:origins:{public_key}')


def allowed_origins_for(public_key: str) -> list[str] | None:
    """The list's allowed origins (empty means any), or None when no list has that key."""
    key = f'email:nl:origins:{public_key}'
    cached = cache.get(key)
    if cached is None:
        row = NewsletterList.objects.filter(public_key=public_key).values('allowed_origins').first()
        cached = {'missing': True} if row is None else {'origins': list(row['allowed_origins'] or [])}
        cache.set(key, cached, ORIGINS_CACHE_SECONDS)
    if cached.get('missing'):
        return None
    return cached['origins']


def origin_allowed(public_key: str, origin: str) -> bool:
    origins = allowed_origins_for(public_key)
    if origins is None:
        return False
    return not origins or origin.rstrip('/').lower() in origins


def cors_for_subscribe(sender, request, **kwargs) -> bool:
    """django-cors-headers asks this per request. Only a list's sign-up endpoint answers its websites."""
    match = SUBSCRIBE_PATH_RE.match(request.path_info)
    if match is None:
        return False
    origin = request.headers.get('Origin', '')
    return bool(origin) and origin_allowed(match['key'], origin)


def create_list(company_id: int, data: dict, user_id: int | None) -> NewsletterList:
    if not isinstance(data, dict):
        raise SendError(400, 'validation_failed')
    name = str(data.get('name') or '').strip()
    if not name:
        raise _invalid('name', 'required')
    with transaction.atomic():
        template = _create_confirmation_template(company_id, name, data.get('theme'), user_id)
        newsletter = NewsletterList.objects.create(
            company_id=company_id,
            name=name[:120],
            public_key=_new_key(),
            default_language=_clean_language(data.get('default_language')),
            confirmation_template=template,
            created_by_user_id=user_id,
        )
        extra = {key: data[key] for key in ('description', 'allowed_origins', 'confirmed_redirect_url') if key in data}
        if extra:
            update_list(newsletter, extra)
    return newsletter


def update_list(newsletter: NewsletterList, data: dict) -> NewsletterList:
    if not isinstance(data, dict):
        raise SendError(400, 'validation_failed')
    fields = ['updated_at']
    if 'name' in data:
        name = str(data.get('name') or '').strip()
        if not name:
            raise _invalid('name', 'required')
        newsletter.name = name[:120]
        newsletter.confirmation_template.name = newsletter.name
        newsletter.confirmation_template.save(update_fields=['name'])
        fields.append('name')
    if 'description' in data:
        newsletter.description = str(data.get('description') or '').strip()[:500]
        fields.append('description')
    if 'default_language' in data:
        newsletter.default_language = _clean_language(data.get('default_language'))
        fields.append('default_language')
    if 'allowed_origins' in data:
        newsletter.allowed_origins = _clean_origins(data.get('allowed_origins'))
        fields.append('allowed_origins')
    if 'confirmed_redirect_url' in data:
        newsletter.confirmed_redirect_url = _clean_redirect(data.get('confirmed_redirect_url'))
        fields.append('confirmed_redirect_url')
    if 'turnstile_site_key' in data:
        newsletter.turnstile_site_key = str(data.get('turnstile_site_key') or '').strip()[:100]
        fields.append('turnstile_site_key')
    if 'turnstile_secret' in data:
        secret = str(data.get('turnstile_secret') or '').strip()
        newsletter.turnstile_secret_ciphertext = encrypt_text(secret) if secret else ''
        fields.append('turnstile_secret_ciphertext')
    newsletter.save(update_fields=fields)
    _forget_origins(newsletter.public_key)
    return newsletter


def rotate_key(newsletter: NewsletterList) -> NewsletterList:
    _forget_origins(newsletter.public_key)
    newsletter.public_key = _new_key()
    newsletter.save(update_fields=['public_key', 'updated_at'])
    return newsletter


def delete_list(newsletter: NewsletterList) -> None:
    template = newsletter.confirmation_template
    _forget_origins(newsletter.public_key)
    with transaction.atomic():
        newsletter.subscribers.all().delete()
        newsletter.delete()
        template.delete()


def confirmation_sender(company_id: int) -> dict:
    try:
        _require_sender(company_id, LANE_TRANSACTIONAL)
    except SendError as exc:
        return {'from_email': '', 'error_code': exc.code}
    company = CompanyProvider.objects.filter(company_id=company_id, configured=True).first()
    if company is not None:
        return {'from_email': company.from_email, 'error_code': ''}
    if _platform_credentials(settings.EMAIL_FALLBACK_PROVIDER or 'none') is None:
        return {'from_email': '', 'error_code': 'provider_not_configured'}
    return {'from_email': settings.DEFAULT_FROM_EMAIL, 'error_code': ''}


def list_counts(newsletter: NewsletterList) -> dict:
    counts = {status: 0 for status in NewsletterSubscriber.STATUSES}
    for row in newsletter.subscribers.values('status').annotate(total=Count('id')):
        counts[row['status']] = row['total']
    return counts


def list_payload(newsletter: NewsletterList, *, detail: bool = False) -> dict:
    payload = {
        'id': newsletter.pk,
        'name': newsletter.name,
        'description': newsletter.description,
        'public_key': newsletter.public_key,
        'default_language': newsletter.default_language,
        'allowed_origins': list(newsletter.allowed_origins or []),
        'confirmed_redirect_url': newsletter.confirmed_redirect_url,
        'turnstile_site_key': newsletter.turnstile_site_key,
        'turnstile_configured': bool(newsletter.turnstile_secret_ciphertext),
        'confirmation_template_id': newsletter.confirmation_template_id,
        'subscribe_url': subscribe_url(newsletter),
        'counts': list_counts(newsletter),
        'created_at': _iso(newsletter.created_at),
        'updated_at': _iso(newsletter.updated_at),
    }
    if detail:
        payload['sender'] = confirmation_sender(newsletter.company_id)
    return payload


# Sign-up and confirmation


def client_ip(request) -> str:
    header = settings.EMAIL_CLIENT_IP_HEADER
    if header:
        value = str(request.headers.get(header) or '').split(',')[0].strip()
        if value:
            return value
    return str(request.META.get('REMOTE_ADDR') or '')


def pick_language(newsletter: NewsletterList, raw, accept_language: str) -> str:
    if raw in LANGUAGES:
        return raw
    for part in (accept_language or '').split(','):
        code = part.split(';')[0].strip().lower()[:2]
        if code in LANGUAGES:
            return code
    return newsletter.default_language if newsletter.default_language in LANGUAGES else 'en'


def _verify_turnstile(newsletter: NewsletterList, token, ip: str) -> None:
    secret = decrypt_text(newsletter.turnstile_secret_ciphertext)
    if not secret:
        return
    if not token:
        raise SendError(400, 'turnstile_failed')
    try:
        response = requests.post(
            settings.EMAIL_TURNSTILE_VERIFY_URL,
            data={'secret': secret, 'response': str(token)[:2048], 'remoteip': ip},
            timeout=10,
        )
        ok = response.status_code == 200 and response.json().get('success') is True
    except (requests.RequestException, ValueError):
        raise SendError(503, 'turnstile_unavailable')
    if not ok:
        raise SendError(400, 'turnstile_failed')


def _clean_email(raw) -> str:
    email = str(raw or '').strip()
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise SendError(400, 'validation_failed', {'email': ['invalid_format']})
    return email


def subscribe(newsletter: NewsletterList, data, *, ip: str, accept_language: str = '') -> None:
    """Public sign-up. The answer never tells whether the address was already on the list."""
    if not isinstance(data, dict):
        raise SendError(400, 'validation_failed')
    if str(data.get('website') or '').strip():
        return
    email = _clean_email(data.get('email'))
    hour = timezone.now().strftime('%Y%m%d%H')
    if not _rate_allow(f'email:nl:ip:{_opaque(ip or "unknown")}:{hour}', settings.EMAIL_NEWSLETTER_IP_PER_HOUR, 3700):
        raise SendError(429, 'rate_limited')
    if not _rate_allow(f'email:nl:list:{newsletter.pk}:{hour}', settings.EMAIL_NEWSLETTER_LIST_PER_HOUR, 3700):
        raise SendError(429, 'rate_limited')
    _verify_turnstile(newsletter, data.get('turnstile_token'), ip)
    if confirmation_sender(newsletter.company_id)['error_code']:
        raise SendError(503, 'newsletter_unavailable')
    request_confirmation(
        newsletter,
        email,
        language=pick_language(newsletter, data.get('language'), accept_language),
        first_name=str(data.get('first_name') or '').strip()[:150],
        source=NewsletterSubscriber.SOURCE_FORM,
    )


def _store_address(row: NewsletterSubscriber, email: str) -> None:
    row.email_ciphertext = encrypt_json({'email': email})
    row.email_masked = mask_email(email)


def request_confirmation(
    newsletter: NewsletterList,
    email: str,
    *,
    language: str,
    first_name: str,
    source: str,
    check_rates: bool = True,
) -> str:
    """Create or refresh a pending subscriber and send the confirmation. Returns what happened."""
    hmac_value = email_hmac(email)
    now = timezone.now()
    with transaction.atomic():
        row, created = NewsletterSubscriber.objects.select_for_update().get_or_create(
            list=newsletter,
            email_hmac=hmac_value,
            defaults={'email_masked': mask_email(email), 'language': language, 'source': source},
        )
        if not created:
            if row.status == NewsletterSubscriber.STATUS_CONFIRMED:
                return OUTCOME_EXISTING
            recent = now - timedelta(minutes=settings.EMAIL_NEWSLETTER_RESEND_MINUTES)
            if row.status == NewsletterSubscriber.STATUS_PENDING and row.confirm_sent_at and row.confirm_sent_at > recent:
                return OUTCOME_THROTTLED
            if row.status == NewsletterSubscriber.STATUS_UNSUBSCRIBED:
                row.status = NewsletterSubscriber.STATUS_PENDING
                row.source = source
                row.unsubscribed_at = None
            row.language = language
        _store_address(row, email)
        if first_name:
            row.first_name = first_name
        day = now.strftime('%Y%m%d')
        if check_rates and not _rate_allow(
            f'email:nl:addr:{newsletter.pk}:{hmac_value}:{day}', settings.EMAIL_NEWSLETTER_ADDRESS_PER_DAY, 90000
        ):
            row.save()
            return OUTCOME_THROTTLED
        if suppressed(newsletter.company_id, hmac_value, LANE_TRANSACTIONAL):
            row.save()
            return OUTCOME_SUPPRESSED
        if check_rates:
            _check_rates(company_id=newsletter.company_id, lane=LANE_TRANSACTIONAL, hmac_value=hmac_value)
        token = secrets.token_urlsafe(32)
        row.confirm_token_hash = _token_hash(token)
        row.confirm_sent_at = now
        row.save()
        template = newsletter.confirmation_template
        _queue_message(
            company_id=newsletter.company_id,
            service='newsletter',
            send_request=None,
            template_key=template.template_key,
            version=template.active_version,
            language=row.language,
            lane=LANE_TRANSACTIONAL,
            recipient={'email': email, 'user_id': None},
            variables={
                **apply_company_name(newsletter.company_id, {}),
                'list_name': newsletter.name,
                'confirm_url': confirm_url(token),
                'first_name': row.first_name,
                'recipient_email': email,
            },
            expires_at=None,
            event_type='newsletter_confirmation',
        )
    _notify(LANE_TRANSACTIONAL)
    if settings.EMAIL_DELIVER_SYNC:
        deliver_due(LANE_TRANSACTIONAL)
    return OUTCOME_SENT


def pending_for_token(token: str) -> NewsletterSubscriber | None:
    """The subscriber a confirmation link belongs to, while it can still be used."""
    if not token or len(token) > 200:
        return None
    row = (
        NewsletterSubscriber.objects.select_related('list')
        .filter(confirm_token_hash=_token_hash(token))
        .first()
    )
    if row is None:
        return None
    if row.status == NewsletterSubscriber.STATUS_CONFIRMED:
        return row
    if row.status != NewsletterSubscriber.STATUS_PENDING or not row.confirm_sent_at:
        return None
    if row.confirm_sent_at + timedelta(hours=settings.EMAIL_NEWSLETTER_CONFIRM_HOURS) <= timezone.now():
        return None
    return row


def _address(row: NewsletterSubscriber) -> str:
    return decrypt_json(row.email_ciphertext).get('email') or ''


def _emit(row: NewsletterSubscriber, event_type: str, extra: dict) -> None:
    emit_email_event(
        company_id=row.list.company_id,
        event_type=event_type,
        data={
            'list_id': row.list_id,
            'list_name': row.list.name,
            'email': _address(row) or row.email_masked,
            'language': row.language,
            **extra,
        },
    )


def confirm(token: str) -> NewsletterSubscriber | None:
    row = pending_for_token(token)
    if row is None or row.status == NewsletterSubscriber.STATUS_CONFIRMED:
        return row
    with transaction.atomic():
        locked = NewsletterSubscriber.objects.select_for_update().select_related('list').get(pk=row.pk)
        if locked.status != NewsletterSubscriber.STATUS_PENDING:
            return locked if locked.status == NewsletterSubscriber.STATUS_CONFIRMED else None
        locked.status = NewsletterSubscriber.STATUS_CONFIRMED
        locked.confirmed_at = timezone.now()
        locked.save(update_fields=['status', 'confirmed_at'])
    _emit(locked, 'email.newsletter.confirmed', {'source': locked.source})
    return locked


def unsubscribe_subscriber(row: NewsletterSubscriber, *, source: str) -> None:
    if row.status == NewsletterSubscriber.STATUS_UNSUBSCRIBED:
        return
    row.status = NewsletterSubscriber.STATUS_UNSUBSCRIBED
    row.unsubscribed_at = timezone.now()
    row.confirm_token_hash = ''
    row.save(update_fields=['status', 'unsubscribed_at', 'confirm_token_hash'])
    _emit(row, 'email.newsletter.unsubscribed', {'source': source})


def unsubscribe_from_list(company_id: int, list_id: int, hmac_value: str, *, source: str) -> bool:
    row = (
        NewsletterSubscriber.objects.select_related('list')
        .filter(list_id=list_id, list__company_id=company_id, email_hmac=hmac_value)
        .first()
    )
    if row is None:
        return False
    unsubscribe_subscriber(row, source=source)
    return True


def unsubscribe_everywhere(company_ids: list[int], hmac_value: str, *, source: str) -> int:
    rows = NewsletterSubscriber.objects.select_related('list').filter(
        list__company_id__in=company_ids,
        email_hmac=hmac_value,
        status__in=[NewsletterSubscriber.STATUS_PENDING, NewsletterSubscriber.STATUS_CONFIRMED],
    )
    count = 0
    for row in rows:
        unsubscribe_subscriber(row, source=source)
        count += 1
    return count


# Admin: subscribers


def subscriber_payload(row: NewsletterSubscriber) -> dict:
    return {
        'id': row.pk,
        'email': _address(row) or row.email_masked,
        'first_name': row.first_name,
        'language': row.language,
        'status': row.status,
        'source': row.source,
        'created_at': _iso(row.created_at),
        'confirmed_at': _iso(row.confirmed_at),
        'unsubscribed_at': _iso(row.unsubscribed_at),
    }


def list_subscribers(newsletter: NewsletterList, *, status: str = '', email: str = '', page: int = 1) -> dict:
    rows = newsletter.subscribers.all()
    if status:
        if status not in NewsletterSubscriber.STATUSES:
            raise _invalid('status')
        rows = rows.filter(status=status)
    if email:
        rows = rows.filter(email_hmac=email_hmac(email))
    count = rows.count()
    page = max(1, page)
    start = (page - 1) * PAGE_SIZE
    return {
        'count': count,
        'page': page,
        'page_size': PAGE_SIZE,
        'results': [subscriber_payload(row) for row in rows[start : start + PAGE_SIZE]],
    }


def _consent(newsletter: NewsletterList, email: str, *, language: str, first_name: str, source: str) -> str:
    """The admin attests this person agreed. A previous unsubscribe always wins."""
    hmac_value = email_hmac(email)
    if suppressed(newsletter.company_id, hmac_value, LANE_BULK):
        return OUTCOME_SUPPRESSED
    with transaction.atomic():
        row, created = NewsletterSubscriber.objects.select_for_update().get_or_create(
            list=newsletter,
            email_hmac=hmac_value,
            defaults={'email_masked': mask_email(email), 'language': language, 'source': source},
        )
        if not created and row.status == NewsletterSubscriber.STATUS_UNSUBSCRIBED:
            return OUTCOME_UNSUBSCRIBED
        if not created and row.status == NewsletterSubscriber.STATUS_CONFIRMED:
            return OUTCOME_EXISTING
        _store_address(row, email)
        if first_name:
            row.first_name = first_name
        row.status = NewsletterSubscriber.STATUS_CONFIRMED
        row.confirmed_at = timezone.now()
        row.confirm_token_hash = ''
        row.save()
    return OUTCOME_ADDED


def _mode(raw) -> str:
    mode = str(raw or MODE_CONFIRM)
    if mode not in {MODE_CONFIRM, MODE_CONSENTED}:
        raise _invalid('mode')
    return mode


def _row_language(newsletter: NewsletterList, raw) -> str:
    return raw if raw in LANGUAGES else newsletter.default_language


def add_subscriber(newsletter: NewsletterList, data) -> tuple[str, NewsletterSubscriber]:
    if not isinstance(data, dict):
        raise SendError(400, 'validation_failed')
    email = _clean_email(data.get('email'))
    mode = _mode(data.get('mode'))
    language = _row_language(newsletter, data.get('language'))
    first_name = str(data.get('first_name') or '').strip()[:150]
    if mode == MODE_CONSENTED:
        outcome = _consent(
            newsletter, email, language=language, first_name=first_name, source=NewsletterSubscriber.SOURCE_ADMIN
        )
        if outcome == OUTCOME_UNSUBSCRIBED:
            raise SendError(409, 'subscriber_unsubscribed')
        if outcome == OUTCOME_SUPPRESSED:
            raise SendError(409, 'subscriber_suppressed')
    else:
        error = confirmation_sender(newsletter.company_id)['error_code']
        if error:
            raise SendError(409, error)
        outcome = request_confirmation(
            newsletter,
            email,
            language=language,
            first_name=first_name,
            source=NewsletterSubscriber.SOURCE_ADMIN,
            check_rates=False,
        )
    return outcome, newsletter.subscribers.get(email_hmac=email_hmac(email))


def parse_csv(text: str) -> list[dict]:
    """Rows as ``{email, first_name, language}``. A header row is optional; without one the columns are in that order."""
    if len(text.encode('utf-8')) > MAX_IMPORT_BYTES:
        raise SendError(400, 'import_too_large', extra={'limit': settings.EMAIL_BROADCAST_MAX_RECIPIENTS})
    rows = [row for row in csv.reader(io.StringIO(text.lstrip('\ufeff'))) if any(cell.strip() for cell in row)]
    if not rows:
        return []
    columns = {'email': 0, 'first_name': 1, 'language': 2}
    if not any('@' in cell for cell in rows[0]):
        header = [cell.strip().lower().replace(' ', '_') for cell in rows[0]]
        if 'email' not in header:
            raise _invalid('csv', 'email_column_missing')
        columns = {name: header.index(name) if name in header else None for name in columns}
        rows = rows[1:]

    def cell(row: list[str], name: str) -> str:
        index = columns[name]
        return row[index].strip() if index is not None and index < len(row) else ''

    return [{'email': cell(row, 'email'), 'first_name': cell(row, 'first_name'), 'language': cell(row, 'language')} for row in rows]


def import_csv(newsletter: NewsletterList, text, mode_raw) -> dict:
    if not isinstance(text, str):
        raise _invalid('csv')
    mode = _mode(mode_raw)
    rows = parse_csv(text)
    limit = settings.EMAIL_BROADCAST_MAX_RECIPIENTS
    if len(rows) > limit:
        raise SendError(400, 'import_too_large', extra={'limit': limit})
    if mode == MODE_CONFIRM:
        error = confirmation_sender(newsletter.company_id)['error_code']
        if error:
            raise SendError(409, error)
    result = {'added': 0, 'existing': 0, 'invalid': 0, 'skipped_unsubscribed': 0, 'skipped_suppressed': 0}
    seen: set[str] = set()
    for row in rows:
        email = row['email']
        if not EMAIL_RE.match(email) or len(email) > 254:
            result['invalid'] += 1
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        language = _row_language(newsletter, row['language'])
        first_name = row['first_name'][:150]
        if mode == MODE_CONSENTED:
            outcome = _consent(
                newsletter, email, language=language, first_name=first_name, source=NewsletterSubscriber.SOURCE_IMPORT
            )
        else:
            outcome = request_confirmation(
                newsletter,
                email,
                language=language,
                first_name=first_name,
                source=NewsletterSubscriber.SOURCE_IMPORT,
                check_rates=False,
            )
        if outcome in {OUTCOME_ADDED, OUTCOME_SENT}:
            result['added'] += 1
        elif outcome == OUTCOME_UNSUBSCRIBED:
            result['skipped_unsubscribed'] += 1
        elif outcome == OUTCOME_SUPPRESSED:
            result['skipped_suppressed'] += 1
        else:
            result['existing'] += 1
    return result


def _cell(value) -> str:
    text = '' if value is None else str(value)
    return "'" + text if text[:1] in {'=', '+', '-', '@', '\t', '\r'} else text


def export_csv(newsletter: NewsletterList, *, status: str = '') -> str:
    rows = newsletter.subscribers.order_by('created_at', 'id')
    if status:
        if status not in NewsletterSubscriber.STATUSES:
            raise _invalid('status')
        rows = rows.filter(status=status)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_FIELDS)
    for row in rows.iterator():
        payload = subscriber_payload(row)
        writer.writerow([_cell(payload[field]) for field in CSV_FIELDS])
    return buffer.getvalue()


# Broadcasts and housekeeping


def confirmed_recipients(newsletter: NewsletterList) -> list[dict]:
    recipients = []
    rows = newsletter.subscribers.filter(status=NewsletterSubscriber.STATUS_CONFIRMED).exclude(email_ciphertext='')
    for row in rows.iterator():
        email = _address(row)
        if not EMAIL_RE.match(email):
            continue
        recipients.append(
            {
                'email': email,
                'user_id': None,
                'first_name': row.first_name,
                'last_name': '',
                'language': row.language,
            }
        )
    return recipients


def purge(now=None) -> tuple[int, int]:
    """Drop confirmations nobody clicked and forget the address of people who left. Returns both counts."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=settings.EMAIL_NEWSLETTER_PENDING_DAYS)
    stale = NewsletterSubscriber.objects.filter(status=NewsletterSubscriber.STATUS_PENDING).filter(
        Q(confirm_sent_at__lt=cutoff) | Q(confirm_sent_at__isnull=True, created_at__lt=cutoff)
    )
    deleted, _rest = stale.delete()
    cleared = (
        NewsletterSubscriber.objects.filter(status=NewsletterSubscriber.STATUS_UNSUBSCRIBED, unsubscribed_at__lt=cutoff)
        .exclude(email_ciphertext='')
        .update(email_ciphertext='', first_name='')
    )
    return deleted, cleared
