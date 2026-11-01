"""Shellui Actions admin API. Paths match storage-service and hosting-service."""

from __future__ import annotations

from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view

from apps.authapi.mcp_openapi import mcp_tool
from apps.email.openapi_admin import ACTIONS_RULES_LIST_DESCRIPTION, ACTIONS_RULES_LIST_SUMMARY, EXAMPLE_ACTION_RULE
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.actions.emit import store_rule_secret
from apps.actions.models import ActionOutbox, ActionRule, EventLog
from apps.actions.registry import all_event_types
from apps.actions.serializers import (
    ActionEventListSerializer,
    ActionRuleCreateSerializer,
    ActionRuleListSerializer,
    ActionRuleSerializer,
    ActionRuleUpdateSerializer,
    ActionTestQueuedSerializer,
    DeliveryDetailSerializer,
    DeliveryListSerializer,
    DeliverySerializer,
    EventLogDetailSerializer,
    EventLogListSerializer,
    EventLogRetentionSerializer,
    EventLogTypesSerializer,
)
from apps.email.access import company_from_request, require_admin, require_staff
from apps.email.crypto import decrypt_text
from apps.email.schema import COMPANY_QUERY, ErrorSerializer
from apps.email.service import SendError
from apps.email.views import error_response

_API_ERRORS = {
    400: ErrorSerializer,
    401: ErrorSerializer,
    403: ErrorSerializer,
    404: ErrorSerializer,
}


def _public_webhook_url(url: str) -> str:
    from django.conf import settings

    from apps.actions.ssrf import SSRFError, validate_webhook_url

    try:
        return validate_webhook_url(url, allow_private=settings.ACTIONS_WEBHOOK_ALLOW_PRIVATE)
    except SSRFError as exc:
        raise SendError(400, 'validation_failed', {'url': ['not_public']}) from exc


def _newsletter_filter(company_id: int, event_type: str, raw) -> int | None:
    """Optional rule filter: only events of this newsletter list (``email.newsletter.*``)."""
    if raw in (None, '', 0):
        return None
    if not event_type.startswith('email.newsletter.'):
        raise SendError(400, 'validation_failed', {'newsletter_id': ['not_newsletter_event']})
    from apps.email.models import NewsletterList

    try:
        list_id = int(raw)
    except (TypeError, ValueError):
        raise SendError(400, 'validation_failed', {'newsletter_id': ['invalid']})
    if not NewsletterList.objects.filter(pk=list_id, company_id=company_id).exists():
        raise SendError(400, 'validation_failed', {'newsletter_id': ['not_found']})
    return list_id


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


@extend_schema_view(
    get=extend_schema(
        tags=['actions'],
        operation_id='api_v1_actions_events_list',
        summary='List webhook event types you can subscribe to',
        description='Domain events emitted by email-service for Shellui Actions rules.',
        parameters=[COMPANY_QUERY],
        responses={200: ActionEventListSerializer, **_API_ERRORS},
        **mcp_tool(operation_id='api_v1_actions_events_list', read_only=True),
    ),
)
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


@extend_schema_view(
    get=extend_schema(
        tags=['actions'],
        operation_id='api_v1_actions_rules_list',
        summary=ACTIONS_RULES_LIST_SUMMARY,
        description=ACTIONS_RULES_LIST_DESCRIPTION,
        parameters=[COMPANY_QUERY],
        responses={200: ActionRuleListSerializer, **_API_ERRORS},
        **mcp_tool(operation_id='api_v1_actions_rules_list', read_only=True, sensitive=True),
    ),
    post=extend_schema(
        tags=['actions'],
        operation_id='api_v1_actions_rules_create',
        summary='Create a Shellui Actions webhook rule',
        description='Returns the signing secret once in `secret`. Store it before closing the response.',
        parameters=[COMPANY_QUERY],
        request=ActionRuleCreateSerializer,
        responses={201: ActionRuleSerializer, **_API_ERRORS},
        examples=[EXAMPLE_ACTION_RULE],
        **mcp_tool(operation_id='api_v1_actions_rules_create', read_only=False, sensitive=True),
    ),
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
            url = _public_webhook_url(url)
            secret = store_rule_secret(str(request.data.get('secret') or ''))
            config = {'url': url, 'secret': secret}
            newsletter_id = _newsletter_filter(company_id, event_type, request.data.get('newsletter_id'))
            if newsletter_id:
                config['newsletter_id'] = newsletter_id
            rule = ActionRule.objects.create(
                company_id=company_id,
                name=name,
                description=str(request.data.get('description') or ''),
                event_type=event_type,
                enabled=bool(request.data.get('enabled', True)),
                config=config,
            )
        except SendError as exc:
            return error_response(exc, request)
        return Response(_rule_payload(rule, reveal_secret=True), status=201)


@extend_schema_view(
    get=extend_schema(
        tags=['actions'],
        operation_id='api_v1_actions_rules_retrieve',
        summary='Read one webhook rule',
        responses={200: ActionRuleSerializer, **_API_ERRORS},
        **mcp_tool(operation_id='api_v1_actions_rules_retrieve', read_only=True, sensitive=True),
    ),
    patch=extend_schema(
        tags=['actions'],
        operation_id='api_v1_actions_rules_partial_update',
        summary='Update name, URL, or enabled state',
        request=ActionRuleUpdateSerializer,
        responses={200: ActionRuleSerializer, **_API_ERRORS},
        **mcp_tool(operation_id='api_v1_actions_rules_partial_update', read_only=False, sensitive=True),
    ),
    delete=extend_schema(
        tags=['actions'],
        operation_id='api_v1_actions_rules_destroy',
        summary='Delete a webhook rule',
        responses={204: None, **_API_ERRORS},
        **mcp_tool(operation_id='api_v1_actions_rules_destroy', read_only=False, destructive=True),
    ),
)
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
            if 'name' in request.data:
                rule.name = str(request.data.get('name') or rule.name)
            if 'description' in request.data:
                rule.description = str(request.data.get('description') or '')
            if 'enabled' in request.data:
                rule.enabled = bool(request.data.get('enabled'))
            if 'url' in request.data:
                config = dict(rule.config or {})
                config['url'] = _public_webhook_url(str(request.data.get('url') or ''))
                rule.config = config
            if 'newsletter_id' in request.data:
                config = dict(rule.config or {})
                config.pop('newsletter_id', None)
                newsletter_id = _newsletter_filter(rule.company_id, rule.event_type, request.data.get('newsletter_id'))
                if newsletter_id:
                    config['newsletter_id'] = newsletter_id
                rule.config = config
            rule.save()
        except SendError as exc:
            return error_response(exc, request)
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


@extend_schema_view(
    post=extend_schema(
        tags=['actions'],
        operation_id='api_v1_actions_rules_rotate_secret',
        summary='Rotate the webhook signing secret',
        description='Returns the new plaintext secret once.',
        request=None,
        responses={200: ActionRuleSerializer, **_API_ERRORS},
        **mcp_tool(operation_id='api_v1_actions_rules_rotate_secret', read_only=False, sensitive=True),
    ),
)
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


@extend_schema_view(
    post=extend_schema(
        tags=['actions'],
        operation_id='api_v1_actions_rules_send_test',
        summary='Queue a sample webhook for this rule',
        request=None,
        responses={200: ActionTestQueuedSerializer, **_API_ERRORS},
        **mcp_tool(operation_id='api_v1_actions_rules_send_test', read_only=False, sensitive=True),
    ),
)
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


@extend_schema_view(
    get=extend_schema(
        tags=['actions'],
        operation_id='api_v1_actions_deliveries_list',
        summary='List recent webhook delivery attempts',
        parameters=[COMPANY_QUERY],
        responses={200: DeliveryListSerializer, **_API_ERRORS},
        **mcp_tool(operation_id='api_v1_actions_deliveries_list', read_only=True, sensitive=True),
    ),
)
class ActionDeliveryListView(APIView):
    def get(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
        except SendError as exc:
            return error_response(exc, request)
        rows = ActionOutbox.objects.filter(company_id=company_id).order_by('-created_at')
        run_raw = (request.GET.get('scheduled_job_run_id') or '').strip()
        if run_raw:
            if not getattr(request.user, 'is_staff', False):
                return error_response(SendError(403, 'forbidden'), request)
            try:
                run_id = int(run_raw)
            except (TypeError, ValueError):
                return error_response(
                    SendError(400, 'validation_failed', {'scheduled_job_run_id': ['invalid']}),
                    request,
                )
            rows = rows.filter(delivery_attempts__scheduled_job_run_id=run_id).distinct()
        return Response({'deliveries': [_delivery_payload(row) for row in rows[:100]]})


@extend_schema_view(
    get=extend_schema(
        tags=['actions'],
        operation_id='api_v1_actions_deliveries_retrieve',
        summary='Read one delivery with attempts and envelope',
        responses={200: DeliveryDetailSerializer, **_API_ERRORS},
        **mcp_tool(operation_id='api_v1_actions_deliveries_retrieve', read_only=True, sensitive=True),
    ),
)
class ActionDeliveryDetailView(APIView):
    def get(self, request, delivery_id):
        row = ActionOutbox.objects.filter(pk=delivery_id).first()
        if row is None:
            return error_response(SendError(404, 'not_found'), request)
        try:
            require_admin(request, row.company_id)
        except SendError as exc:
            return error_response(exc, request)
        return Response(
            _delivery_payload(
                row,
                include_attempts=True,
                include_run_ids=bool(getattr(request.user, 'is_staff', False)),
            )
        )


@extend_schema_view(
    post=extend_schema(
        tags=['actions'],
        operation_id='api_v1_actions_deliveries_requeue',
        summary='Requeue a failed webhook delivery',
        request=None,
        responses={200: DeliverySerializer, **_API_ERRORS},
        **mcp_tool(operation_id='api_v1_actions_deliveries_requeue', read_only=False, sensitive=True),
    ),
)
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


def _delivery_payload(row: ActionOutbox, *, include_attempts: bool = False, include_run_ids: bool = False) -> dict:
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
        data['attempts'] = []
        for attempt in row.delivery_attempts.all():
            item = {
                'attempt_number': attempt.attempt_number,
                'status': attempt.status,
                'http_status': attempt.http_status,
                'error_code': attempt.error_code,
                'trigger': attempt.trigger or None,
            }
            if include_run_ids:
                item['scheduled_job_run_id'] = attempt.scheduled_job_run_id
            data['attempts'].append(item)
        data['envelope'] = row.envelope
    return data


@extend_schema_view(
    get=extend_schema(
        tags=['actions'],
        operation_id='api_v1_actions_event_log_list',
        summary='List recent domain events for the company',
        description='Company owners use the default scope. `scope=platform` is staff-only and is not an MCP tool.',
        parameters=[COMPANY_QUERY],
        responses={200: EventLogListSerializer, **_API_ERRORS},
        **mcp_tool(operation_id='api_v1_actions_event_log_list', read_only=True, sensitive=True),
    ),
)
class EventLogListView(APIView):
    def get(self, request):
        scope = (request.query_params.get('scope') or '').strip()
        try:
            if scope == 'platform':
                require_staff(request)
                rows = EventLog.objects.filter(company_id__isnull=True).order_by('-created_at')[:100]
            elif scope:
                raise SendError(400, 'validation_failed', {'scope': ['invalid']})
            else:
                company_id = company_from_request(request)
                require_admin(request, company_id)
                rows = EventLog.objects.filter(company_id=company_id).order_by('-created_at')[:100]
        except SendError as exc:
            return error_response(exc, request)
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


@extend_schema_view(
    get=extend_schema(
        tags=['actions'],
        operation_id='api_v1_actions_event_log_retrieve',
        summary='Read one event log entry',
        responses={200: EventLogDetailSerializer, **_API_ERRORS},
        **mcp_tool(operation_id='api_v1_actions_event_log_retrieve', read_only=True, sensitive=True),
    ),
)
class EventLogDetailView(APIView):
    def get(self, request, pk):
        row = EventLog.objects.filter(pk=pk).first()
        if row is None:
            return error_response(SendError(404, 'not_found'), request)
        try:
            if row.company_id is None:
                require_staff(request)
            else:
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


@extend_schema_view(
    get=extend_schema(
        tags=['actions'],
        operation_id='api_v1_actions_event_log_types_list',
        summary='List event type ids for filters',
        parameters=[COMPANY_QUERY],
        responses={200: EventLogTypesSerializer, **_API_ERRORS},
        **mcp_tool(operation_id='api_v1_actions_event_log_types_list', read_only=True),
    ),
)
class EventLogTypesView(APIView):
    def get(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
        except SendError as exc:
            return error_response(exc, request)
        return Response({'types': [event.id for event in all_event_types()]})


@extend_schema_view(
    get=extend_schema(
        tags=['actions'],
        operation_id='api_v1_actions_event_log_retention_retrieve',
        summary='Read event log retention settings',
        parameters=[COMPANY_QUERY],
        responses={200: EventLogRetentionSerializer, **_API_ERRORS},
        **mcp_tool(operation_id='api_v1_actions_event_log_retention_retrieve', read_only=True),
    ),
)
class EventLogRetentionView(APIView):
    def get(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
        except SendError as exc:
            return error_response(exc, request)
        from django.conf import settings

        return Response({'retention_days': settings.EVENT_LOG_RETENTION_DAYS, 'company_id': company_id})
