"""Accept sends, apply rules, and hand messages to a provider adapter."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.actions.emit import emit_email_event
from apps.email.catalog import LANE_AUTH, LANE_BULK, get_definition
from apps.email.companies import apply_company_name, company_name_may_be_stored
from apps.email.unsubscribe import unsubscribe_url
from apps.providers.credentials import strip_internal_credentials
from apps.email.crypto import decrypt_json, email_hmac, encrypt_json, mask_email
from apps.email.models import (
    CompanyProvider,
    EmailRule,
    EmailTemplate,
    EventSkip,
    LaneState,
    Message,
    MessageEvent,
    SendRequest,
    Suppression,
)
from apps.email.substitution import SubstitutionError, substitute
from apps.providers.registry import get_provider

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
AUTH_RETRY_DELAYS = (2, 6)


class SendError(Exception):
    def __init__(self, status: int, code: str, field_errors: dict | None = None, extra: dict | None = None):
        self.status = status
        self.code = code
        self.field_errors = field_errors or {}
        self.extra = extra or {}
        super().__init__(code)


def payload_hash(body: dict) -> str:
    raw = json.dumps(body, separators=(',', ':'), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _json_size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def _coerce(value):
    if isinstance(value, list):
        return ', '.join(str(item) for item in value)
    if isinstance(value, (dict, bool)):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return ''
    return value


def _lane_paused(lane: str, company_id: int | None = None) -> bool:
    if LaneState.objects.filter(lane=lane, company_id__isnull=True, paused=True).exists():
        return True
    if company_id is None:
        return False
    return LaneState.objects.filter(lane=lane, company_id=company_id, paused=True).exists()


def pause_lane(lane: str, reason: str, company_id: int | None = None) -> None:
    """Pause one company, or every company when ``company_id`` is omitted (staff)."""
    defaults = {'paused': True, 'paused_at': timezone.now(), 'reason': reason}
    if company_id is None:
        state = LaneState.objects.filter(lane=lane, company_id__isnull=True).first()
        if state is None:
            LaneState.objects.create(lane=lane, company_id=None, **defaults)
        else:
            for key, value in defaults.items():
                setattr(state, key, value)
            state.save()
    else:
        LaneState.objects.update_or_create(lane=lane, company_id=company_id, defaults=defaults)
    logger.error('lane paused lane=%s company_id=%s reason=%s', lane, company_id, reason)


def _rate_allow(key: str, limit: int, ttl: int) -> bool:
    if limit <= 0:
        return True
    if cache.add(key, 0, ttl):
        pass
    try:
        current = cache.incr(key)
    except ValueError:
        cache.set(key, 1, ttl)
        current = 1
    return current <= limit


def _check_rates(*, company_id: int, lane: str, hmac_value: str) -> None:
    if lane == LANE_AUTH:
        company_key = f'email:auth-company:{company_id}'
        if not _rate_allow(
            company_key,
            settings.EMAIL_COMPANY_AUTH_LIMIT,
            settings.EMAIL_COMPANY_AUTH_WINDOW_SECONDS,
        ):
            raise SendError(429, 'company_rate_limited')
        key = f'email:auth:{company_id}:{hmac_value}'
        if not _rate_allow(key, settings.EMAIL_RECIPIENT_AUTH_LIMIT, settings.EMAIL_RECIPIENT_AUTH_WINDOW_SECONDS):
            raise SendError(429, 'recipient_rate_limited')
        return
    hour = timezone.now().strftime('%Y%m%d%H')
    key = f'email:company:{company_id}:{lane}:{hour}'
    if not _rate_allow(key, settings.EMAIL_COMPANY_TRANSACTIONAL_PER_HOUR, 3700):
        raise SendError(429, 'company_rate_limited')


def suppressed(company_id: int, hmac_value: str, lane: str) -> bool:
    now = timezone.now()
    rows = Suppression.objects.filter(email_hmac=hmac_value).filter(
        models_company(company_id)
    )
    for row in rows:
        if row.expires_at and row.expires_at <= now:
            continue
        if lane == LANE_AUTH:
            if row.reason == Suppression.REASON_HARD_BOUNCE:
                return True
            continue
        if row.lanes and lane not in row.lanes:
            continue
        return True
    return False


def models_company(company_id: int):
    from django.db.models import Q

    return Q(company_id=company_id) | Q(company_id__isnull=True)


def _url_tokens(definition: dict) -> dict[str, set[str] | None]:
    tokens: dict[str, set[str] | None] = {}
    for variable in definition.get('variables') or []:
        if variable.get('type') != 'url' and not variable.get('is_url'):
            continue
        hosts = None
        if variable.get('allowed_hosts_setting'):
            hosts = {host.lower() for host in settings.EMAIL_AUTH_LINK_HOSTS}
        tokens[variable['token']] = hosts
    return tokens


def assets_url() -> str:
    return f'{settings.EMAIL_PUBLIC_URL}/static/library'


def _system_variables(message: Message, variables: dict | None = None) -> dict:
    values = {'system.message_id': message.public_id, 'system.assets_url': assets_url()}
    if message.lane == LANE_AUTH:
        return values
    category = str((variables or {}).get('system.unsubscribe_category') or '') or message.lane
    url = unsubscribe_url(message.company_id, message.to_email, category)
    values['system.unsubscribe_url'] = url
    values['system.preferences_url'] = url
    return values


def _list_unsubscribe_headers(lane: str, variables: dict) -> dict[str, str]:
    """One-click headers for bulk mail. Transactional templates do not add them."""
    if lane != LANE_BULK:
        return {}
    url = variables.get('system.unsubscribe_url') or ''
    if not url:
        return {}
    return {
        'List-Unsubscribe': f'<{url}>',
        'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click',
    }


def resolve_content(
    company_id: int, template_key: str, language: str, variables: dict | None = None
) -> tuple[dict, dict]:
    from apps.email.rules import content_for_event

    definition = get_definition(template_key)
    if definition is None:
        raise SendError(404, 'template_not_found')
    if language not in definition['languages'] and language != 'en':
        raise SendError(400, 'language_not_available')
    return definition, content_for_event(company_id, definition, language, variables)


def _validate_variables(definition: dict, variables: dict, *, field_prefix: str) -> dict:
    field_errors = {}
    cleaned = {}
    declared = {item['token']: item for item in definition.get('variables') or []}
    for token, spec in declared.items():
        raw = variables.get(token, '')
        value = _coerce(raw)
        if spec.get('required') and not str(value).strip():
            field_errors[f'{field_prefix}{token}'] = ['required']
            continue
        cleaned[token] = value
    for token, raw in variables.items():
        if token not in cleaned and token in declared:
            continue
        if token not in declared and not token.startswith('system.'):
            continue
        cleaned[token] = _coerce(raw)
    if _json_size(cleaned) > settings.EMAIL_MAX_VARIABLES_BYTES:
        field_errors[field_prefix.rstrip('.')] = ['too_large']
    if field_errors:
        raise SendError(400, 'validation_failed', field_errors)
    return cleaned


def _check_lane(principal, definition: dict, requested_lane: str | None, company_id: int) -> str:
    lane = requested_lane or definition['default_lane']
    if lane == LANE_BULK or definition['lane_class'] == LANE_BULK:
        raise SendError(400, 'lane_requires_campaign')
    if lane != definition['lane_class']:
        raise SendError(400, 'template_lane_mismatch')
    if not principal.allows_lane(lane):
        raise SendError(403, 'lane_not_allowed')
    if not principal.allows_template(definition['key']):
        raise SendError(403, 'forbidden')
    if _lane_paused(lane, company_id):
        raise SendError(409, 'lane_paused')
    return lane


def company_may_use_platform_sender(company_id: int, lane: str) -> bool:
    """Auth mail may use the platform fallback. Other lanes may only for Shellui companies."""
    if int(company_id) in settings.EMAIL_PLATFORM_COMPANY_IDS:
        return True
    return lane == LANE_AUTH


def _require_sender(company_id: int, lane: str) -> None:
    company = CompanyProvider.objects.filter(company_id=company_id, configured=True).first()
    if company:
        if company.provider == 'smtp' and not settings.EMAIL_ALLOW_COMPANY_SMTP:
            raise SendError(409, 'company_smtp_disabled')
        return
    if company_may_use_platform_sender(company_id, lane):
        return
    raise SendError(409, 'platform_sender_not_allowed')


def _expires_at(definition: dict, lane: str, ttl_seconds):
    if lane != LANE_AUTH and not ttl_seconds:
        return None
    default_ttl = definition.get('default_ttl_seconds') or settings.EMAIL_AUTH_DEFAULT_TTL_SECONDS
    requested = int(ttl_seconds) if ttl_seconds else default_ttl
    if requested <= 0:
        raise SendError(400, 'validation_failed', {'ttl_seconds': ['invalid']})
    seconds = min(requested, settings.EMAIL_AUTH_MAX_TTL_SECONDS if lane == LANE_AUTH else requested)
    if lane == LANE_AUTH:
        seconds = min(seconds, settings.EMAIL_AUTH_MAX_TTL_SECONDS)
    return timezone.now() + timedelta(seconds=seconds)


def _normalize_recipient(raw, index: int) -> dict:
    if not isinstance(raw, dict):
        raise SendError(400, 'validation_failed', {f'to.{index}.email': ['invalid_format']})
    email = str(raw.get('email') or '').strip()
    if not EMAIL_RE.match(email):
        raise SendError(400, 'recipient_invalid', {f'to.{index}.email': ['invalid_format']})
    user_id = raw.get('user_id')
    if user_id not in (None, ''):
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            raise SendError(400, 'validation_failed', {f'to.{index}.user_id': ['invalid']})
    else:
        user_id = None
    return {'email': email, 'user_id': user_id}


def _idempotent(service: str, company_id: int, key: str, body: dict):
    if not key:
        return None
    existing = SendRequest.objects.filter(
        service=service, company_id=company_id, idempotency_key=key
    ).first()
    if existing is None:
        return None
    if existing.payload_hash != payload_hash(body):
        raise SendError(409, 'idempotency_conflict')
    replay = dict(existing.response_body)
    replay['idempotent_replay'] = True
    return existing.response_status, replay


def _store_request(service, company_id, key, body, status, response):
    if not key:
        return None
    try:
        return SendRequest.objects.create(
            service=service,
            company_id=company_id,
            idempotency_key=key,
            payload_hash=payload_hash(body),
            response_status=status,
            response_body=response,
        )
    except IntegrityError:
        replay = _idempotent(service, company_id, key, body)
        if replay:
            return replay
        raise


def _message_payload(message: Message) -> dict:
    return {
        'id': message.public_id,
        'to': message.to_email,
        'status': message.status,
        'lane': message.lane,
        'template_version': message.template_version,
        'language': message.language,
        'expires_at': message.expires_at.isoformat().replace('+00:00', 'Z') if message.expires_at else None,
    }


def _notify(lane: str) -> None:
    if connection.vendor != 'postgresql':
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_notify(%s, %s)', [f'email_lane_{lane}', lane])
    except Exception:
        logger.warning('pg_notify failed for lane=%s', lane)


def _queue_message(
    *,
    company_id: int,
    service: str,
    send_request,
    template_key: str,
    version,
    language: str,
    lane: str,
    recipient: dict,
    variables: dict,
    expires_at,
    event_type: str = '',
) -> Message:
    hmac_value = email_hmac(recipient['email'])
    message = Message.objects.create(
        company_id=company_id,
        service=service,
        send_request=send_request if isinstance(send_request, SendRequest) else None,
        template_key=template_key,
        template_version=version,
        language=language,
        lane=lane,
        status=Message.STATUS_QUEUED,
        to_email=recipient['email'],
        to_user_id=recipient.get('user_id'),
        email_hmac=hmac_value,
        variables_ciphertext=encrypt_json(variables),
        accepted_at=timezone.now(),
        expires_at=expires_at,
        next_attempt_at=timezone.now(),
        event_type=event_type or template_key,
    )
    MessageEvent.objects.create(
        message=message,
        type='queued',
        occurred_at=message.accepted_at,
        detail={},
    )
    return message


def accept_send(principal, body: dict) -> tuple[int, dict]:
    if not isinstance(body, dict):
        raise SendError(400, 'validation_failed')
    company_id = _require_company(principal, body.get('company_id'))
    template_key = str(body.get('template_key') or '')
    definition = get_definition(template_key)
    if definition is None:
        raise SendError(404, 'template_not_found')
    language = str(body.get('language') or 'en')
    lane = _check_lane(principal, definition, body.get('lane') or None, company_id)
    _require_sender(company_id, lane)
    recipients_raw = body.get('to') or []
    if not isinstance(recipients_raw, list) or not recipients_raw:
        raise SendError(400, 'validation_failed', {'to': ['required']})
    if len(recipients_raw) > settings.EMAIL_MAX_RECIPIENTS:
        raise SendError(400, 'validation_failed', {'to': ['too_many']})
    if lane == LANE_AUTH and len(recipients_raw) != 1:
        # One sign-in link, one address: the one that asked for it.
        raise SendError(400, 'auth_single_recipient', {'to': ['single_recipient']})
    idem = str(body.get('idempotency_key') or '')
    replay = _idempotent(principal.service, company_id, idem, body)
    if replay:
        return replay
    variables_in = body.get('variables') or {}
    if not isinstance(variables_in, dict):
        raise SendError(400, 'validation_failed', {'variables': ['invalid']})
    if _json_size(variables_in) > settings.EMAIL_MAX_VARIABLES_BYTES:
        raise SendError(400, 'validation_failed', {'variables': ['too_large']})
    definition, content = resolve_content(company_id, template_key, language)
    recipients = [_normalize_recipient(item, index) for index, item in enumerate(recipients_raw)]
    expires = _expires_at(definition, lane, body.get('ttl_seconds'))
    prepared = []
    for index, recipient in enumerate(recipients):
        hmac_value = email_hmac(recipient['email'])
        if suppressed(company_id, hmac_value, lane):
            if lane == LANE_AUTH:
                raise SendError(422, 'recipient_suppressed', {f'to.{index}.email': ['recipient_suppressed']})
            prepared.append((recipient, None, hmac_value))
            continue
        try:
            _check_rates(company_id=company_id, lane=lane, hmac_value=hmac_value)
        except SendError:
            if lane == LANE_AUTH:
                raise
            raise
        merged = dict(variables_in)
        merged.setdefault('recipient_email', recipient['email'])
        apply_company_name(company_id, merged, store=company_name_may_be_stored(principal, company_id))
        cleaned = _validate_variables(definition, merged, field_prefix='variables.')
        prepared.append((recipient, cleaned, hmac_value))
    messages = []
    with transaction.atomic():
        request_row = None
        created = []
        for recipient, cleaned, hmac_value in prepared:
            if cleaned is None:
                message = Message.objects.create(
                    company_id=company_id,
                    service=principal.service,
                    template_key=template_key,
                    language=content['language'],
                    lane=lane,
                    status=Message.STATUS_SUPPRESSED,
                    to_email=recipient['email'],
                    to_user_id=recipient.get('user_id'),
                    email_hmac=hmac_value,
                    accepted_at=timezone.now(),
                    event_type=template_key,
                )
                _record_event(message, 'suppressed', {})
                created.append(message)
                continue
            message = _queue_message(
                company_id=company_id,
                service=principal.service,
                send_request=None,
                template_key=template_key,
                version=content.get('version'),
                language=content['language'],
                lane=lane,
                recipient=recipient,
                variables=cleaned,
                expires_at=expires,
                event_type=template_key,
            )
            created.append(message)
        response = {
            'idempotent_replay': False,
            'messages': [_message_payload(item) for item in created],
        }
        if idem:
            request_row = SendRequest.objects.create(
                service=principal.service,
                company_id=company_id,
                idempotency_key=idem,
                payload_hash=payload_hash(body),
                response_status=202,
                response_body=response,
            )
            Message.objects.filter(pk__in=[item.pk for item in created]).update(send_request=request_row)
        messages = created
    for message in messages:
        if message.status == Message.STATUS_SUPPRESSED:
            _emit_status(message, 'email.message.suppressed', {})
        elif settings.EMAIL_DELIVER_SYNC:
            deliver_message(message.pk)
    if any(item.status == Message.STATUS_QUEUED for item in messages):
        transaction.on_commit(lambda: _notify(lane))
    fresh = [_message_payload(Message.objects.get(pk=item.pk)) for item in messages]
    response = {'idempotent_replay': False, 'messages': fresh}
    if idem and request_row:
        SendRequest.objects.filter(pk=request_row.pk).update(response_body=response)
    return 202, response


def accept_batch(principal, body: dict) -> tuple[int, dict]:
    if not isinstance(body, dict):
        raise SendError(400, 'validation_failed')
    company_id = _require_company(principal, body.get('company_id'))
    template_key = str(body.get('template_key') or '')
    definition = get_definition(template_key)
    if definition is None:
        raise SendError(404, 'template_not_found')
    language = str(body.get('language') or 'en')
    lane = _check_lane(principal, definition, body.get('lane') or None, company_id)
    _require_sender(company_id, lane)
    items = body.get('items') or []
    if not isinstance(items, list) or not items:
        raise SendError(400, 'validation_failed', {'items': ['required']})
    if len(items) > settings.EMAIL_MAX_BATCH_ITEMS:
        raise SendError(400, 'validation_failed', {'items': ['too_many']})
    if lane == LANE_AUTH:
        # Same rule as /send: one auth message, one address. A batch of auth items
        # would send the same sign-in or invitation mail to several addresses.
        if len(items) != 1:
            raise SendError(400, 'auth_single_recipient', {'items': ['single_recipient']})
        only = items[0]
        to = only.get('to') if isinstance(only, dict) else None
        if isinstance(to, list) and len(to) != 1:
            raise SendError(400, 'auth_single_recipient', {'items.0.to': ['single_recipient']})
    idem = str(body.get('idempotency_key') or '')
    replay = _idempotent(principal.service, company_id, idem, body)
    if replay:
        return replay
    definition, content = resolve_content(company_id, template_key, language)
    accepted = []
    rejected = []
    queued = []
    with transaction.atomic():
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                rejected.append({'index': index, 'error_code': 'validation_failed'})
                continue
            try:
                recipient = _normalize_recipient(item.get('to') or {}, index)
            except SendError as exc:
                rejected.append({'index': index, 'error_code': exc.code})
                continue
            variables_in = item.get('variables') or {}
            if not isinstance(variables_in, dict):
                rejected.append({'index': index, 'error_code': 'validation_failed'})
                continue
            hmac_value = email_hmac(recipient['email'])
            if suppressed(company_id, hmac_value, lane):
                rejected.append({'index': index, 'error_code': 'recipient_suppressed'})
                continue
            try:
                _check_rates(company_id=company_id, lane=lane, hmac_value=hmac_value)
                merged = dict(variables_in)
                merged.setdefault('recipient_email', recipient['email'])
                apply_company_name(
                    company_id, merged, store=company_name_may_be_stored(principal, company_id)
                )
                cleaned = _validate_variables(definition, merged, field_prefix=f'items.{index}.variables.')
            except SendError as exc:
                rejected.append({'index': index, 'error_code': exc.code})
                continue
            message = _queue_message(
                company_id=company_id,
                service=principal.service,
                send_request=None,
                template_key=template_key,
                version=content.get('version'),
                language=content['language'],
                lane=lane,
                recipient=recipient,
                variables=cleaned,
                expires_at=_expires_at(definition, lane, body.get('ttl_seconds')),
                event_type=template_key,
            )
            queued.append(message)
            accepted.append({'index': index, 'id': message.public_id, 'status': message.status})
        response = {'idempotent_replay': False, 'accepted': accepted, 'rejected': rejected}
        if idem:
            SendRequest.objects.create(
                service=principal.service,
                company_id=company_id,
                idempotency_key=idem,
                payload_hash=payload_hash(body),
                response_status=202,
                response_body=response,
            )
    for message in queued:
        if settings.EMAIL_DELIVER_SYNC:
            deliver_message(message.pk)
    if queued:
        transaction.on_commit(lambda: _notify(lane))
    if settings.EMAIL_DELIVER_SYNC:
        accepted = []
        for index, item in enumerate(items):
            match = next((row for row in response['accepted'] if row['index'] == index), None)
            if match:
                from apps.email.models import parse_message_id

                message = Message.objects.get(pk=parse_message_id(match['id']))
                accepted.append({'index': index, 'id': message.public_id, 'status': message.status})
        response['accepted'] = accepted
    return 202, response


def _rule_recipients(rule: EmailRule, body: dict, *, auth: bool = False) -> list:
    if rule.recipient_mode == EmailRule.MODE_STATIC and not auth:
        return [{'email': item} if isinstance(item, str) else item for item in (rule.static_recipients or [])]
    raw = body.get('recipients') if body.get('recipients') is not None else []
    if not isinstance(raw, list):
        raise SendError(400, 'validation_failed', {'recipients': ['invalid']})
    return raw


def accept_event(principal, body: dict) -> tuple[int, dict]:
    if not isinstance(body, dict):
        raise SendError(400, 'validation_failed')
    company_id = _require_company(principal, body.get('company_id'))
    service = str(body.get('service') or principal.service)
    if service != principal.service:
        raise SendError(403, 'forbidden')
    event_type = str(body.get('event_type') or '')
    definition = get_definition(event_type)
    if definition is None:
        raise SendError(400, 'unknown_event')
    if not principal.allows_template(event_type):
        raise SendError(403, 'forbidden')
    from apps.email.rules import content_for_rule, ensure_builtin_rules

    ensure_builtin_rules(company_id)
    idem = str(body.get('idempotency_key') or '')
    replay = _idempotent(principal.service, company_id, idem, body)
    if replay:
        return replay
    auth = definition['lane_class'] == LANE_AUTH
    rules_qs = EmailRule.objects.filter(
        company_id=company_id,
        service=service,
        event_type=event_type,
        enabled=True,
    )
    if auth:
        # Auth-lane events carry a sign-in or invitation link. Only the built-in rule
        # sends them, to the event's own recipient. A company rule never does.
        rules_qs = rules_qs.filter(built_in=True)
    rules = list(rules_qs.select_related('template').order_by('created_at', 'id'))
    if definition.get('company_editable', True) is False:
        # Built-in copy only (``apps.email.builtin_auth``): direct send, never a rule.
        rules = []
    if not rules:
        return _skip_event(
            principal,
            company_id,
            event_type,
            idem,
            body,
            reason=EventSkip.REASON_NO_RULE,
            rule_enabled=False,
        )
    lane = _check_lane(principal, definition, None, company_id)
    payload = body.get('payload') or {}
    if not isinstance(payload, dict):
        raise SendError(400, 'validation_failed', {'payload': ['invalid']})
    planned = []
    for rule in rules:
        recipients_raw = _rule_recipients(rule, body, auth=auth)
        if len(recipients_raw) > settings.EMAIL_MAX_RECIPIENTS:
            raise SendError(400, 'validation_failed', {'recipients': ['too_many']})
        for index, raw in enumerate(recipients_raw):
            planned.append((rule, index, raw))
    if auth and len(planned) > 1:
        raise SendError(400, 'auth_single_recipient', {'recipients': ['single_recipient']})
    if not planned:
        return _skip_event(
            principal,
            company_id,
            event_type,
            idem,
            body,
            reason=EventSkip.REASON_NO_RECIPIENTS,
            rule_enabled=True,
        )
    _require_sender(company_id, lane)
    messages = []
    with transaction.atomic():
        for rule, index, raw in planned:
            recipient = _normalize_recipient(raw, index)
            hmac_value = email_hmac(recipient['email'])
            if suppressed(company_id, hmac_value, lane):
                if lane == LANE_AUTH:
                    raise SendError(422, 'recipient_suppressed')
                continue
            _check_rates(company_id=company_id, lane=lane, hmac_value=hmac_value)
            language = str(rule.language or recipient.get('language') or body.get('language') or 'en')
            content = content_for_rule(rule, language)
            merged = {key: _coerce(value) for key, value in payload.items()}
            merged.setdefault('recipient_email', recipient['email'])
            apply_company_name(company_id, merged, store=company_name_may_be_stored(principal, company_id))
            cleaned = _validate_variables(definition, merged, field_prefix='payload.')
            message = _queue_message(
                company_id=company_id,
                service=principal.service,
                send_request=None,
                template_key=rule.template.template_key,
                version=content.get('version'),
                language=content['language'],
                lane=lane,
                recipient=recipient,
                variables=cleaned,
                expires_at=_expires_at(definition, lane, body.get('ttl_seconds')),
                event_type=event_type,
            )
            messages.append(message)
        response = {
            'idempotent_replay': False,
            'rule_enabled': True,
            'messages': [_message_payload(item) for item in messages],
        }
        if idem:
            request_row = SendRequest.objects.create(
                service=principal.service,
                company_id=company_id,
                idempotency_key=idem,
                payload_hash=payload_hash(body),
                response_status=202,
                response_body=response,
            )
            Message.objects.filter(pk__in=[item.pk for item in messages]).update(send_request=request_row)
    for message in messages:
        if settings.EMAIL_DELIVER_SYNC:
            deliver_message(message.pk)
    if messages:
        transaction.on_commit(lambda: _notify(lane))
    fresh = [_message_payload(Message.objects.get(pk=item.pk)) for item in messages]
    response = {'idempotent_replay': False, 'rule_enabled': True, 'messages': fresh}
    if idem:
        SendRequest.objects.filter(
            service=principal.service,
            company_id=company_id,
            idempotency_key=idem,
        ).update(response_body=response)
    return 202, response


def _skip_event(principal, company_id: int, event_type: str, idem: str, body: dict, *, reason: str, rule_enabled: bool):
    EventSkip.objects.create(
        company_id=company_id,
        service=principal.service,
        event_type=event_type,
        reason=reason,
    )
    response = {
        'idempotent_replay': False,
        'rule_enabled': rule_enabled,
        'skipped_reason': reason,
        'messages': [],
    }
    if idem:
        SendRequest.objects.create(
            service=principal.service,
            company_id=company_id,
            idempotency_key=idem,
            payload_hash=payload_hash(body),
            response_status=202,
            response_body=response,
        )
    return 202, response


def _require_company(principal, raw) -> int:
    try:
        company_id = int(raw)
    except (TypeError, ValueError):
        raise SendError(400, 'validation_failed', {'company_id': ['required']})
    if not principal.allows_company(company_id):
        raise SendError(403, 'company_mismatch')
    return company_id


def _record_event(message: Message, event_type: str, detail: dict, provider_event_id: str | None = None) -> None:
    if provider_event_id and MessageEvent.objects.filter(provider_event_id=provider_event_id).exists():
        return
    MessageEvent.objects.create(
        message=message,
        type=event_type,
        occurred_at=timezone.now(),
        provider_event_id=provider_event_id,
        detail=detail,
    )


def _emit_status(message: Message, event_type: str, extra: dict) -> None:
    data = {
        'message_id': message.public_id,
        'template_key': message.template_key,
        'lane': message.lane,
        'service': message.service,
        'to_email': message.to_email,
        'to_user_id': message.to_user_id,
    }
    data.update(extra)
    emit_email_event(
        company_id=message.company_id,
        event_type=event_type,
        data=data,
        service=message.service,
    )


def _credentials_for(message: Message) -> tuple[str, dict, str, str]:
    company = CompanyProvider.objects.filter(company_id=message.company_id, configured=True).first()
    if message.smtp_failover_used:
        creds = _platform_credentials('smtp')
        if not creds:
            return 'smtp', {}, settings.DEFAULT_FROM_EMAIL, settings.DEFAULT_FROM_NAME
        return 'smtp', creds, settings.DEFAULT_FROM_EMAIL, settings.DEFAULT_FROM_NAME
    if company:
        creds = strip_internal_credentials(
            decrypt_json(company.credentials_ciphertext, setting='EMAIL_CREDENTIALS_KEY')
        )
        from_email = company.from_email or settings.DEFAULT_FROM_EMAIL
        if message.lane == LANE_BULK and (company.bulk_from_email or settings.BULK_FROM_EMAIL):
            from_email = company.bulk_from_email or settings.BULK_FROM_EMAIL
        return company.provider, creds, from_email, company.from_name or settings.DEFAULT_FROM_NAME
    provider_name = settings.EMAIL_FALLBACK_PROVIDER or 'none'
    creds = _platform_credentials(provider_name)
    from_email = settings.BULK_FROM_EMAIL if message.lane == LANE_BULK else settings.DEFAULT_FROM_EMAIL
    if not creds:
        return provider_name, {}, from_email, settings.DEFAULT_FROM_NAME
    return provider_name, creds, from_email, settings.DEFAULT_FROM_NAME


def _platform_credentials(provider_name: str) -> dict | None:
    if provider_name == 'resend' and settings.RESEND_API_KEY:
        return {'api_key': settings.RESEND_API_KEY}
    if provider_name == 'smtp' and settings.EMAIL_HOST:
        return {
            'host': settings.EMAIL_HOST,
            'port': settings.EMAIL_PORT,
            'username': settings.EMAIL_HOST_USER,
            'password': settings.EMAIL_HOST_PASSWORD,
            'use_tls': settings.EMAIL_USE_TLS,
            'use_ssl': settings.EMAIL_USE_SSL,
        }
    if provider_name == 'fake' and settings.EMAIL_ALLOW_FAKE_PROVIDER:
        return {}
    return None


def platform_fallback_configured() -> bool:
    return _platform_credentials(settings.EMAIL_FALLBACK_PROVIDER) is not None


def deliver_message(message_id) -> None:
    with transaction.atomic():
        message = Message.objects.select_for_update().get(pk=message_id)
        if message.status not in {Message.STATUS_QUEUED, Message.STATUS_RETRYING}:
            return
        now = timezone.now()
        if message.expires_at and message.expires_at <= now:
            _expire(message)
            return
        if _lane_paused(message.lane, message.company_id):
            return
        message.status = Message.STATUS_SENDING
        message.locked_until = now + timedelta(seconds=settings.EMAIL_SEND_LEASE_SECONDS)
        message.attempt_count += 1
        message.save(update_fields=['status', 'locked_until', 'attempt_count'])
        variables = decrypt_json(message.variables_ciphertext)
        variables.update(_system_variables(message, variables))
        snapshot = {
            'attempt': message.attempt_count,
            'smtp_failover_used': message.smtp_failover_used,
            'variables': variables,
            'template_key': message.template_key,
            'language': message.language,
            'company_id': message.company_id,
            'lane': message.lane,
            'to_email': message.to_email,
            'public_id': message.public_id,
            'expires_at': message.expires_at,
        }
    definition = get_definition(snapshot['template_key'])
    template = None
    if definition is None:
        template = EmailTemplate.objects.filter(
            company_id=snapshot['company_id'],
            template_key=snapshot['template_key'],
        ).first()
        if template is not None:
            from apps.email.rules import definition_for

            definition = definition_for(template) or get_definition(template.template_key)
    if definition is None and template is None:
        _finish_failed(message_id, 'template_not_found')
        return
    if definition is None:
        definition = {'variables': [], 'lane_class': snapshot['lane'], 'key': snapshot['template_key']}
    try:
        if template is not None:
            from apps.email.rules import content_for_template

            content = content_for_template(template, snapshot['language'])
        else:
            _definition, content = resolve_content(
                snapshot['company_id'],
                snapshot['template_key'],
                snapshot['language'],
                snapshot['variables'],
            )
    except SendError as exc:
        _finish_failed(message_id, exc.code)
        return
    url_tokens = _url_tokens(definition)
    try:
        subject, _missing_subject = substitute(
            content['subject'],
            snapshot['variables'],
            html=False,
            subject=True,
            url_tokens=url_tokens,
            allow_http_localhost=settings.DEBUG,
        )
        html, _missing_html = substitute(
            content['html'],
            snapshot['variables'],
            html=True,
            url_tokens=url_tokens,
            allow_http_localhost=settings.DEBUG,
        )
        text, _missing_text = substitute(
            content['text'],
            snapshot['variables'],
            html=False,
            url_tokens=url_tokens,
            allow_http_localhost=settings.DEBUG,
        )
    except SubstitutionError as exc:
        _finish_failed(message_id, exc.code if exc.code != 'invalid_url_scheme' else 'variable_url_not_allowed')
        return
    provider_name, credentials, from_email, from_name = _credentials_for(Message.objects.get(pk=message_id))
    platform_from = {settings.DEFAULT_FROM_EMAIL.lower(), settings.BULK_FROM_EMAIL.lower()}
    uses_platform_from = (from_email or '').lower() in platform_from
    shellui_company = snapshot['company_id'] in settings.EMAIL_PLATFORM_COMPANY_IDS
    has_company_provider = CompanyProvider.objects.filter(
        company_id=snapshot['company_id'], configured=True
    ).exists()
    if uses_platform_from and not shellui_company and (has_company_provider or snapshot['lane'] != LANE_AUTH):
        _finish_failed(message_id, 'platform_sender_not_allowed')
        return
    provider = get_provider(provider_name)
    if provider is None or (provider_name != 'fake' and not credentials):
        _finish_failed(message_id, 'provider_not_configured')
        return
    from apps.providers.base import ProviderMessage

    provider_message = ProviderMessage(
        message_id=snapshot['public_id'],
        to_email=snapshot['to_email'],
        from_email=from_email,
        from_name=from_name,
        subject=subject,
        html=html,
        text=text,
        idempotency_key=snapshot['public_id'],
        tags={
            'message_id': snapshot['public_id'],
            'lane': snapshot['lane'],
            'company_id': str(snapshot['company_id']),
        },
        headers=_list_unsubscribe_headers(snapshot['lane'], snapshot['variables']),
        stream='bulk' if snapshot['lane'] == LANE_BULK else 'transactional',
    )
    if provider is None:
        _finish_failed(message_id, 'provider_not_configured')
        return
    try:
        result = provider.send(provider_message, credentials)
    except Exception:
        logger.exception('provider send crashed message=%s', message_id)
        _finish_failed(message_id, 'provider_error')
        return
    _apply_result(message_id, provider_name, result)


def _apply_result(message_id, provider_name: str, result) -> None:
    with transaction.atomic():
        message = Message.objects.select_for_update().get(pk=message_id)
        message.provider = provider_name
        message.locked_until = None
        if result.ok:
            message.status = Message.STATUS_SENT
            message.sent_at = timezone.now()
            message.provider_message_id = result.provider_message_id or ''
            message.variables_ciphertext = ''
            message.last_error_code = ''
            message.save()
            _record_event(message, 'sent', {})
            _emit_status(message, 'email.message.sent', {})
            return
        message.last_error_code = result.error_code or 'provider_rejected'
        if result.error_code == 'provider_unauthorized':
            message.status = Message.STATUS_FAILED
            message.variables_ciphertext = ''
            message.save()
            _record_event(message, 'failed', {'error_code': result.error_code})
            _emit_status(message, 'email.message.failed', {'error_code': result.error_code})
            pause_lane(message.lane, 'provider_unauthorized', company_id=message.company_id)
            return
        if _schedule_retry(message, result):
            return
        message.status = Message.STATUS_FAILED
        message.variables_ciphertext = ''
        message.save()
        _record_event(message, 'failed', {'error_code': message.last_error_code})
        _emit_status(message, 'email.message.failed', {'error_code': message.last_error_code})


def _schedule_retry(message: Message, result) -> bool:
    now = timezone.now()
    if message.lane == LANE_AUTH:
        if message.expires_at and message.expires_at <= now:
            _expire(message)
            return True
        if result.retryable and message.attempt_count <= len(AUTH_RETRY_DELAYS):
            delay = AUTH_RETRY_DELAYS[message.attempt_count - 1]
            message.status = Message.STATUS_RETRYING
            message.next_attempt_at = now + timedelta(seconds=delay)
            message.save()
            _record_event(message, 'retrying', {'error_code': result.error_code})
            return True
        if result.retryable and not message.smtp_failover_used and _platform_credentials('smtp'):
            message.smtp_failover_used = True
            message.status = Message.STATUS_RETRYING
            message.next_attempt_at = now
            message.save()
            _record_event(message, 'retrying', {'error_code': 'smtp_failover'})
            return True
        return False
    if result.retryable and message.attempt_count < 8:
        delay = min(3600, 30 * (2 ** (message.attempt_count - 1)))
        if result.retry_after_seconds:
            delay = min(3600, int(result.retry_after_seconds))
        message.status = Message.STATUS_RETRYING
        message.next_attempt_at = now + timedelta(seconds=delay)
        message.save()
        _record_event(message, 'retrying', {'error_code': result.error_code})
        return True
    return False


def _expire(message: Message) -> None:
    message.status = Message.STATUS_EXPIRED
    message.variables_ciphertext = ''
    message.locked_until = None
    message.last_error_code = 'expired'
    message.save()
    _record_event(message, 'expired', {})
    _emit_status(message, 'email.message.expired', {})


def _finish_failed(message_id, code: str) -> None:
    with transaction.atomic():
        message = Message.objects.select_for_update().get(pk=message_id)
        message.status = Message.STATUS_FAILED
        message.last_error_code = code
        message.variables_ciphertext = ''
        message.locked_until = None
        message.save()
        _record_event(message, 'failed', {'error_code': code})
        _emit_status(message, 'email.message.failed', {'error_code': code})


def deliver_due(lane: str, *, limit: int = 20) -> int:
    now = timezone.now()
    from django.db.models import Q

    ids = list(
        Message.objects.filter(lane=lane, status__in=[Message.STATUS_QUEUED, Message.STATUS_RETRYING])
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        .filter(Q(locked_until__isnull=True) | Q(locked_until__lte=now))
        .order_by('accepted_at')
        .values_list('id', flat=True)[:limit]
    )
    for message_id in ids:
        try:
            deliver_message(message_id)
        except Exception:
            logger.exception('deliver_message crashed id=%s', message_id)
            try:
                _finish_failed(message_id, 'provider_error')
            except Exception:
                logger.exception('could not record provider failure id=%s', message_id)
    return len(ids)


def sweep() -> dict:
    now = timezone.now()
    expired = 0
    released = 0
    from django.db.models import Q

    overdue = Message.objects.filter(
        status__in=[Message.STATUS_QUEUED, Message.STATUS_RETRYING],
        expires_at__isnull=False,
        expires_at__lte=now,
    )
    for message in overdue:
        with transaction.atomic():
            locked = Message.objects.select_for_update().get(pk=message.pk)
            if locked.status in {Message.STATUS_QUEUED, Message.STATUS_RETRYING} and locked.expires_at and locked.expires_at <= now:
                _expire(locked)
                expired += 1
    stale = Message.objects.filter(status=Message.STATUS_SENDING, locked_until__lte=now)
    for message in list(stale):
        with transaction.atomic():
            locked = Message.objects.select_for_update().get(pk=message.pk)
            if locked.status != Message.STATUS_SENDING:
                continue
            if locked.provider_message_id:
                locked.status = Message.STATUS_SENT
                if locked.sent_at is None:
                    locked.sent_at = now
                locked.locked_until = None
                locked.save()
                released += 1
                continue
            if locked.expires_at and locked.expires_at <= now:
                _expire(locked)
                expired += 1
            else:
                locked.status = Message.STATUS_RETRYING
                locked.locked_until = None
                locked.next_attempt_at = now
                locked.save()
                released += 1
    return {'expired': expired, 'released': released}


def apply_provider_event(*, provider_message_id: str, event_name: str, provider_event_id: str, detail: dict) -> bool:
    from apps.providers.webhooks import RESEND_STATUS

    status = RESEND_STATUS.get(event_name)
    if status is None or event_name in {'email.opened', 'email.clicked'}:
        return False
    message = Message.objects.filter(provider_message_id=provider_message_id).first()
    if message is None:
        return False
    if provider_event_id and MessageEvent.objects.filter(provider_event_id=provider_event_id).exists():
        return True
    message.status = status
    if status in Message.PROVIDER_ACCEPTED or status in {
        Message.STATUS_FAILED,
        Message.STATUS_SUPPRESSED,
        Message.STATUS_BOUNCED,
        Message.STATUS_COMPLAINED,
    }:
        message.variables_ciphertext = ''
    message.save()
    _record_event(message, status, detail, provider_event_id=provider_event_id or None)
    if status == Message.STATUS_BOUNCED and detail.get('bounce_type', 'permanent') == 'permanent':
        Suppression.objects.create(
            company_id=message.company_id,
            email_hmac=message.email_hmac,
            email_masked=mask_email(message.to_email),
            reason=Suppression.REASON_HARD_BOUNCE,
            lanes=[],
            expires_at=timezone.now() + timedelta(days=30),
        )
    if status == Message.STATUS_COMPLAINED and message.lane != LANE_AUTH:
        Suppression.objects.create(
            company_id=message.company_id,
            email_hmac=message.email_hmac,
            email_masked=mask_email(message.to_email),
            reason=Suppression.REASON_COMPLAINT,
            lanes=['transactional', 'bulk'],
        )
    if status == Message.STATUS_SUPPRESSED:
        Suppression.objects.get_or_create(
            company_id=message.company_id,
            email_hmac=message.email_hmac,
            reason=Suppression.REASON_PROVIDER,
            defaults={'email_masked': mask_email(message.to_email), 'lanes': []},
        )
    event_type = f'email.message.{status}' if status != 'delivery_delayed' else 'email.message.delivery_delayed'
    if status == 'sent':
        event_type = 'email.message.sent'
    _emit_status(message, event_type, detail)
    return True


def send_test_message(
    *,
    company_id: int,
    to_email: str,
    provider_name: str | None = None,
    subject: str | None = None,
    html: str | None = None,
    text: str | None = None,
) -> dict:
    """Send one test message with the company provider, or the platform fallback."""
    from apps.providers.base import ProviderMessage

    company = CompanyProvider.objects.filter(company_id=company_id, configured=True).first()
    if company:
        name = provider_name or company.provider
        credentials = decrypt_json(company.credentials_ciphertext, setting='EMAIL_CREDENTIALS_KEY')
        from_email = company.from_email
        from_name = company.from_name or settings.DEFAULT_FROM_NAME
    else:
        name = provider_name or settings.EMAIL_FALLBACK_PROVIDER
        credentials = _platform_credentials(name) or {}
        from_email = settings.DEFAULT_FROM_EMAIL
        from_name = settings.DEFAULT_FROM_NAME
    provider = get_provider(name)
    if provider is None or (name != 'fake' and not credentials):
        raise SendError(409, 'provider_not_configured')
    probe = ProviderMessage(
        message_id=f'test-{company_id}',
        to_email=to_email,
        from_email=from_email,
        from_name=from_name,
        subject=subject or '[Shellui] Test message',
        html=html or '<p>This is a test message from Shellui.</p>',
        text=text or 'This is a test message from Shellui.\n',
        idempotency_key=f'test-{company_id}-{to_email}',
        tags={'lane': 'transactional', 'company_id': str(company_id)},
    )
    result = provider.send(probe, credentials)
    if not result.ok:
        raise SendError(502, 'provider_test_failed')
    return {'status': 'sent', 'provider': name, 'provider_message_id': result.provider_message_id}
