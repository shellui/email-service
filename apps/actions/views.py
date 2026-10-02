"""Shellui Actions admin API. Paths match storage-service and hosting-service."""

from __future__ import annotations

from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.actions.emit import store_rule_secret
from apps.actions.models import ActionOutbox, ActionRule, DeliveryAttempt, EventLog
from apps.actions.registry import all_event_types
from apps.email.access import company_from_request, require_admin
from apps.email.crypto import decrypt_text
from apps.email.service import SendError
from apps.email.views import error_response


def _mask_config(config: dict) -> dict:
    raw = dict(config or {})
    secret = raw.pop('secret', '') or ''
    if secret.startswith('enc:'):
        secret = decrypt_text(secret[4:])
    raw.pop('authorization_header', None)
    hint = secret[-4:] if len(secret) >= 4 else ''
    raw['has_secret'] = bool(secret)
    raw['secret_hint'] = hint
    return raw


def _rule_payload(rule: ActionRule, *, reveal_secret: bool = False) -> dict:
    data = {
        'id': rule.pk,
        'company_id': rule.company_id,
        'name': rule.name,
        'description': rule.description,
        'event_type': rule.event_type,
        'enabled': rule.enabled,
        'action_kind': rule.action_kind,
        'config': _mask_config(rule.config),
        'created_at': rule.created_at.isoformat(),
        'updated_at': rule.updated_at.isoformat(),
    }
    if reveal_secret:
        secret = (rule.config or {}).get('secret') or ''
        if secret.startswith('enc:'):
            secret = decrypt_text(secret[4:])
        if secret:
            data['secret'] = secret
    return data


class ActionEventsView(APIView):
    def get(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
        except SendError as exc:
            return error_response(exc, request)
        return Response(
            {
                'events': [
                    {'id': event.id, 'label': event.label, 'description': event.description}
                    for event in all_event_types()
                ]
            }
        )


class ActionRuleListCreateView(APIView):
    def get(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
        except SendError as exc:
            return error_response(exc, request)
        rules = ActionRule.objects.filter(company_id=company_id)
        return Response({'rules': [_rule_payload(rule) for rule in rules]})

    def post(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
            name = str(request.data.get('name') or '')
            event_type = str(request.data.get('event_type') or '')
            url = str(request.data.get('url') or '')
            if not name or not event_type or not url:
                raise SendError(400, 'validation_failed', {
                    key: ['required']
                    for key, value in (('name', name), ('event_type', event_type), ('url', url))
                    if not value
                })
            from apps.actions.registry import is_registered_event

            if not is_registered_event(event_type):
                raise SendError(400, 'validation_failed', {'event_type': ['unknown']})
            secret = store_rule_secret(str(request.data.get('secret') or ''))
            rule = ActionRule.objects.create(
                company_id=company_id,
                name=name,
                description=str(request.data.get('description') or ''),
                event_type=event_type,
                enabled=bool(request.data.get('enabled', True)),
                config={'url': url, 'secret': secret},
            )
        except SendError as exc:
            return error_response(exc, request)
        return Response(_rule_payload(rule, reveal_secret=True), status=201)


class ActionRuleDetailView(APIView):
    def get(self, request, pk):
        rule = ActionRule.objects.filter(pk=pk).first()
        if rule is None:
            return error_response(SendError(404, 'not_found'), request)
        try:
            require_admin(request, rule.company_id)
        except SendError as exc:
            return error_response(exc, request)
        return Response(_rule_payload(rule))

    def patch(self, request, pk):
        rule = ActionRule.objects.filter(pk=pk).first()
        if rule is None:
            return error_response(SendError(404, 'not_found'), request)
        try:
            require_admin(request, rule.company_id)
        except SendError as exc:
            return error_response(exc, request)
        if 'name' in request.data:
            rule.name = str(request.data.get('name') or rule.name)
        if 'description' in request.data:
            rule.description = str(request.data.get('description') or '')
        if 'enabled' in request.data:
            rule.enabled = bool(request.data.get('enabled'))
        if 'url' in request.data:
            config = dict(rule.config or {})
            config['url'] = str(request.data.get('url') or '')
            rule.config = config
        rule.save()
        return Response(_rule_payload(rule))

    def delete(self, request, pk):
        rule = ActionRule.objects.filter(pk=pk).first()
        if rule is None:
            return error_response(SendError(404, 'not_found'), request)
        try:
            require_admin(request, rule.company_id)
        except SendError as exc:
            return error_response(exc, request)
        rule.delete()
        return Response(status=204)


class ActionRuleRotateSecretView(APIView):
    def post(self, request, pk):
        rule = ActionRule.objects.filter(pk=pk).first()
        if rule is None:
            return error_response(SendError(404, 'not_found'), request)
        try:
            require_admin(request, rule.company_id)
        except SendError as exc:
            return error_response(exc, request)
        config = dict(rule.config or {})
        config['secret'] = store_rule_secret('')
        rule.config = config
        rule.save(update_fields=['config', 'updated_at'])
        return Response(_rule_payload(rule, reveal_secret=True))


class ActionRuleSendTestView(APIView):
    def post(self, request, pk):
        rule = ActionRule.objects.filter(pk=pk).first()
        if rule is None:
            return error_response(SendError(404, 'not_found'), request)
        try:
            require_admin(request, rule.company_id)
        except SendError as exc:
            return error_response(exc, request)
        from apps.actions.emit import emit_email_event

        emit_email_event(
            company_id=rule.company_id,
            event_type=rule.event_type,
            data={'message_id': 'msg_test', 'template_key': 'email.test', 'lane': 'transactional'},
        )
        return Response({'status': 'queued'})


class ActionDeliveryListView(APIView):
    def get(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
        except SendError as exc:
            return error_response(exc, request)
        rows = ActionOutbox.objects.filter(company_id=company_id).order_by('-created_at')[:100]
        return Response({'deliveries': [_delivery_payload(row) for row in rows]})


class ActionDeliveryDetailView(APIView):
    def get(self, request, delivery_id):
        row = ActionOutbox.objects.filter(pk=delivery_id).first()
        if row is None:
            return error_response(SendError(404, 'not_found'), request)
        try:
            require_admin(request, row.company_id)
        except SendError as exc:
            return error_response(exc, request)
        return Response(_delivery_payload(row, include_attempts=True))


class ActionDeliveryRequeueView(APIView):
    def post(self, request, delivery_id):
        row = ActionOutbox.objects.filter(pk=delivery_id).first()
        if row is None:
            return error_response(SendError(404, 'not_found'), request)
        try:
            require_admin(request, row.company_id)
        except SendError as exc:
            return error_response(exc, request)
        row.status = ActionOutbox.STATUS_PENDING
        row.next_attempt_at = timezone.now()
        row.locked_until = None
        row.save(update_fields=['status', 'next_attempt_at', 'locked_until', 'updated_at'])
        return Response(_delivery_payload(row))


def _delivery_payload(row: ActionOutbox, *, include_attempts: bool = False) -> dict:
    data = {
        'id': str(row.pk),
        'company_id': row.company_id,
        'action_rule_id': row.action_rule_id,
        'event_type': row.event_type,
        'status': row.status,
        'attempt_count': row.attempt_count,
        'last_error': row.last_error,
        'created_at': row.created_at.isoformat(),
    }
    if include_attempts:
        data['attempts'] = [
            {
                'attempt_number': attempt.attempt_number,
                'status': attempt.status,
                'http_status': attempt.http_status,
                'error_code': attempt.error_code,
            }
            for attempt in row.delivery_attempts.all()
        ]
        data['envelope'] = row.envelope
    return data


class EventLogListView(APIView):
    def get(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
        except SendError as exc:
            return error_response(exc, request)
        rows = EventLog.objects.filter(company_id=company_id).order_by('-created_at')[:100]
        return Response(
            {
                'events': [
                    {
                        'id': row.pk,
                        'event_type': row.event_type,
                        'data': row.data,
                        'created_at': row.created_at.isoformat(),
                    }
                    for row in rows
                ]
            }
        )


class EventLogDetailView(APIView):
    def get(self, request, pk):
        row = EventLog.objects.filter(pk=pk).first()
        if row is None:
            return error_response(SendError(404, 'not_found'), request)
        try:
            require_admin(request, row.company_id)
        except SendError as exc:
            return error_response(exc, request)
        return Response(
            {
                'id': row.pk,
                'company_id': row.company_id,
                'event_type': row.event_type,
                'data': row.data,
                'created_at': row.created_at.isoformat(),
            }
        )


class EventLogTypesView(APIView):
    def get(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
        except SendError as exc:
            return error_response(exc, request)
        return Response({'types': [event.id for event in all_event_types()]})


class EventLogRetentionView(APIView):
    def get(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
        except SendError as exc:
            return error_response(exc, request)
        from django.conf import settings

        return Response({'retention_days': settings.EVENT_LOG_RETENTION_DAYS, 'company_id': company_id})
