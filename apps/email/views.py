"""HTTP API for send, rules, templates, provider settings, stats, and health."""

from __future__ import annotations

import json

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authapi.service_auth import ServicePrincipal
from apps.email.access import company_from_request, require_admin, require_staff
from apps.email.catalog import all_definitions, get_definition
from apps.email.crypto import decrypt_json, email_hmac, encrypt_json, mask_email, mask_secret
from apps.email.broadcasts import (
    broadcast_payload,
    clean_audience,
    create_broadcast,
    preview_audience,
    send_broadcast,
    update_broadcast,
)
from apps.email.models import (
    Broadcast,
    CompanyProvider,
    EmailRule,
    EmailTemplate,
    LaneState,
    Message,
    NewsletterSubscriber,
    ServiceClient,
    Suppression,
    TemplateVersion,
    parse_message_id,
)
from apps.email.renderers import PrometheusTextRenderer
from apps.email.schema import (
    COMPANY_QUERY,
    BatchRequestSerializer,
    BroadcastCreateSerializer,
    BroadcastListSerializer,
    BroadcastPatchSerializer,
    BroadcastPreviewRequestSerializer,
    BroadcastPreviewSerializer,
    BroadcastSerializer,
    BatchResponseSerializer,
    CatalogSerializer,
    EmailRuleListSerializer,
    EmailRulePatchSerializer,
    EmailRuleSerializer,
    EmailRuleWriteSerializer,
    ErrorSerializer,
    EventRequestSerializer,
    EventResponseSerializer,
    HealthSerializer,
    LaneStateSerializer,
    LibraryDetailSerializer,
    LibraryListSerializer,
    LibraryWriteSerializer,
    MessageCancelSerializer,
    MessageDetailSerializer,
    MessageListSerializer,
    PrivacyEraseRequestSerializer,
    PrivacyEraseResponseSerializer,
    ProviderSerializer,
    ProviderTestSendRequestSerializer,
    ProviderWriteSerializer,
    SendRequestSerializer,
    SendResponseSerializer,
    ServiceClientCreateSerializer,
    ServiceClientCreatedSerializer,
    ServiceClientListSerializer,
    StatsSerializer,
    SuppressionCreateSerializer,
    SuppressionCreatedSerializer,
    SuppressionListSerializer,
    TemplateListSerializer,
    TemplatePublishResponseSerializer,
    TemplateSummarySerializer,
    TemplateTestSendRequestSerializer,
    TemplateVersionCreateRequestSerializer,
    TemplateVersionCreateResponseSerializer,
    TemplateVersionListSerializer,
    TemplateVersionSerializer,
    TestSendResponseSerializer,
    WebhookAckSerializer,
    WebhookEventSerializer,
)
from apps.email.unsubscribe import unsubscribe_token, unsubscribe_url
from apps.providers.credentials import strip_internal_credentials
from apps.email.document import validate_document
from apps.email.library import SETS as LIBRARY_SETS
from apps.email.library import (
    create_library_template,
    delete_library_template,
    ensure_composed,
    library_detail,
    library_summary,
    library_template,
    set_head,
    update_library_template,
    visible_library,
)
from apps.email.rendering import compose, document_tokens
from apps.email.rules import (
    create_rule,
    create_version,
    definition_for,
    delete_rule,
    ensure_builtin_rules,
    fits_event,
    library_document_for,
    publish_version,
    rule_payload,
    rules_allowed,
    template_summary,
    update_rule,
)
from apps.email.service import (
    SendError,
    accept_batch,
    accept_event,
    accept_send,
    assets_url,
    platform_fallback_configured,
    send_test_message,
    _url_tokens,
)
from apps.email.stats import company_stats
from apps.email.substitution import SubstitutionError, substitute
from apps.email.theming import clean_theme, theme_colors
from apps.email.translations import translated_inbox
from apps.providers.registry import ACTIVE_PROVIDER_NAMES
from apps.providers.webhooks import verify_svix


def error_response(exc: SendError, request) -> Response:
    body = {'error_code': exc.code}
    if exc.field_errors:
        body['field_errors'] = exc.field_errors
    if exc.extra:
        body.update(exc.extra)
    request_id = getattr(request, 'request_id', None)
    if request_id:
        body['request_id'] = request_id
    return Response(body, status=exc.status)


def _request_id(request) -> dict:
    request_id = getattr(request, 'request_id', None)
    return {'request_id': request_id} if request_id else {}


_API_ERRORS = {
    400: ErrorSerializer,
    401: ErrorSerializer,
    403: ErrorSerializer,
    404: ErrorSerializer,
    409: ErrorSerializer,
    422: ErrorSerializer,
}


@extend_schema_view(
    get=extend_schema(
        tags=['health'],
        operation_id='api_v1_health_retrieve',
        auth=[],
        responses={200: HealthSerializer},
    ),
)
class HealthView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        payload = {'status': 'ok', 'version': settings.VERSION}
        return Response(payload)


@extend_schema_view(
    post=extend_schema(
        tags=['send'],
        operation_id='api_v1_send_create',
        request=SendRequestSerializer,
        responses={202: SendResponseSerializer, **_API_ERRORS},
    ),
)
class SendView(APIView):
    def post(self, request):
        if not isinstance(request.user, ServicePrincipal):
            return error_response(SendError(401, 'unauthorized'), request)
        try:
            status, body = accept_send(request.user, request.data)
        except SendError as exc:
            return error_response(exc, request)
        body.update(_request_id(request))
        return Response(body, status=status)


@extend_schema_view(
    post=extend_schema(
        tags=['send'],
        operation_id='api_v1_send_batch_create',
        request=BatchRequestSerializer,
        responses={202: BatchResponseSerializer, **_API_ERRORS},
    ),
)
class SendBatchView(APIView):
    def post(self, request):
        if not isinstance(request.user, ServicePrincipal):
            return error_response(SendError(401, 'unauthorized'), request)
        try:
            status, body = accept_batch(request.user, request.data)
        except SendError as exc:
            return error_response(exc, request)
        body.update(_request_id(request))
        return Response(body, status=status)


@extend_schema_view(
    post=extend_schema(
        tags=['send'],
        operation_id='api_v1_events_create',
        request=EventRequestSerializer,
        responses={202: EventResponseSerializer, **_API_ERRORS},
    ),
)
class EventIngestView(APIView):
    def post(self, request):
        if not isinstance(request.user, ServicePrincipal):
            return error_response(SendError(401, 'unauthorized'), request)
        try:
            status, body = accept_event(request.user, request.data)
        except SendError as exc:
            return error_response(exc, request)
        body.update(_request_id(request))
        return Response(body, status=status)


def _can_read_message(user, message: Message) -> bool:
    if isinstance(user, ServicePrincipal):
        return message.service == user.service and user.allows_company(message.company_id)
    if getattr(user, 'is_staff', False):
        return True
    if getattr(user, 'is_company_owner', False) and user.company_id == message.company_id:
        return True
    return False


@extend_schema_view(
    get=extend_schema(
        tags=['messages'],
        operation_id='api_v1_messages_retrieve',
        responses={200: MessageDetailSerializer, **_API_ERRORS},
    ),
)
class MessageDetailView(APIView):
    def get(self, request, message_id):
        try:
            message = Message.objects.get(pk=parse_message_id(message_id))
        except (Message.DoesNotExist, ValueError):
            return error_response(SendError(404, 'message_not_found'), request)
        if not _can_read_message(request.user, message):
            return error_response(SendError(403, 'forbidden'), request)
        return Response(
            {
                'id': message.public_id,
                'company_id': message.company_id,
                'service': message.service,
                'template_key': message.template_key,
                'lane': message.lane,
                'status': message.status,
                'provider': message.provider,
                'accepted_at': message.accepted_at.isoformat().replace('+00:00', 'Z'),
                'expires_at': message.expires_at.isoformat().replace('+00:00', 'Z') if message.expires_at else None,
                'events': [
                    {
                        'type': event.type,
                        'at': event.occurred_at.isoformat().replace('+00:00', 'Z'),
                    }
                    for event in message.events.all()
                ],
            }
        )


@extend_schema_view(
    post=extend_schema(
        tags=['messages'],
        operation_id='api_v1_messages_cancel',
        request=None,
        responses={200: MessageCancelSerializer, **_API_ERRORS},
    ),
)
class MessageCancelView(APIView):
    def post(self, request, message_id):
        try:
            message = Message.objects.get(pk=parse_message_id(message_id))
        except (Message.DoesNotExist, ValueError):
            return error_response(SendError(404, 'message_not_found'), request)
        if not _can_read_message(request.user, message):
            return error_response(SendError(403, 'forbidden'), request)
        if message.status not in {Message.STATUS_QUEUED, Message.STATUS_RETRYING}:
            return error_response(SendError(409, 'message_not_cancellable'), request)
        message.status = Message.STATUS_CANCELLED
        message.variables_ciphertext = ''
        message.save(update_fields=['status', 'variables_ciphertext'])
        return Response({'id': message.public_id, 'status': message.status})


@extend_schema_view(
    get=extend_schema(
        tags=['messages'],
        operation_id='api_v1_messages_list',
        parameters=[
            COMPANY_QUERY,
            OpenApiParameter('status', str, OpenApiParameter.QUERY, required=False),
            OpenApiParameter('lane', str, OpenApiParameter.QUERY, required=False),
            OpenApiParameter('template_key', str, OpenApiParameter.QUERY, required=False),
            OpenApiParameter('limit', int, OpenApiParameter.QUERY, required=False),
        ],
        responses={200: MessageListSerializer, **_API_ERRORS},
    ),
)
class MessageListView(APIView):
    def get(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
        except SendError as exc:
            return error_response(exc, request)
        qs = Message.objects.filter(company_id=company_id).order_by('-accepted_at')
        for field in ('status', 'lane', 'template_key'):
            value = request.query_params.get(field)
            if value:
                qs = qs.filter(**{field: value})
        try:
            limit = min(int(request.query_params.get('limit') or 50), 200)
        except ValueError:
            limit = 50
        rows = []
        for message in qs[:limit]:
            rows.append(
                {
                    'id': message.public_id,
                    'template_key': message.template_key,
                    'lane': message.lane,
                    'status': message.status,
                    'to': mask_email(message.to_email),
                    'accepted_at': message.accepted_at.isoformat().replace('+00:00', 'Z'),
                }
            )
        return Response({'company_id': company_id, 'messages': rows})


@extend_schema_view(
    get=extend_schema(
        tags=['catalog'],
        operation_id='api_v1_catalog_retrieve',
        responses={200: CatalogSerializer, **_API_ERRORS},
    ),
)
class CatalogView(APIView):
    def get(self, request):
        user = request.user
        if isinstance(user, ServicePrincipal) or getattr(user, 'is_staff', False) or getattr(user, 'is_company_owner', False):
            events = []
            for row in all_definitions():
                events.append(
                    {
                        'service': row['owner_service'],
                        'event_type': row['event_type'],
                        'template_key': row['key'],
                        'label': row['label'],
                        'lane_class': row['lane_class'],
                        'default_lane': row['default_lane'],
                        'default_enabled': row['default_enabled'],
                        'category': row['category'],
                        'default_ttl_seconds': row['default_ttl_seconds'],
                        'variables': row['variables'],
                        'link_token': row['link_token'],
                        'default_template': row['default_template'],
                        'rules_allowed': rules_allowed(row),
                        'suggested': {
                            language: {
                                'subject': pack['subject'],
                                'preheader': pack['preheader'],
                            }
                            for language, pack in row['languages'].items()
                        },
                    }
                )
            return Response({'auth_link_hosts': list(settings.EMAIL_AUTH_LINK_HOSTS), 'events': events})
        return error_response(SendError(401, 'unauthorized'), request)


@extend_schema_view(
    get=extend_schema(
        tags=['library'],
        operation_id='api_v1_library_list',
        parameters=[COMPANY_QUERY],
        responses={200: LibraryListSerializer, **_API_ERRORS},
    ),
    post=extend_schema(
        tags=['library'],
        operation_id='api_v1_library_create',
        parameters=[COMPANY_QUERY],
        request=LibraryWriteSerializer,
        responses={201: LibraryDetailSerializer, **_API_ERRORS},
    ),
)
class LibraryListView(APIView):
    def get(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
        except SendError as exc:
            return error_response(exc, request)
        rows = [ensure_composed(row) for row in visible_library(company_id)]
        return Response(
            {
                'sets': [{'key': key, 'name': name} for key, name in LIBRARY_SETS],
                'templates': [library_summary(row) for row in rows],
            }
        )

    def post(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
            row = create_library_template(company_id, request.data)
        except SubstitutionError:
            return error_response(SendError(400, 'validation_failed', {'document': ['template_tags_forbidden']}), request)
        except SendError as exc:
            return error_response(exc, request)
        return Response(library_detail(row), status=201)


@extend_schema_view(
    get=extend_schema(
        tags=['library'],
        operation_id='api_v1_library_retrieve',
        parameters=[COMPANY_QUERY],
        responses={200: LibraryDetailSerializer, **_API_ERRORS},
    ),
    put=extend_schema(
        tags=['library'],
        operation_id='api_v1_library_update',
        parameters=[COMPANY_QUERY],
        request=LibraryWriteSerializer,
        responses={200: LibraryDetailSerializer, **_API_ERRORS},
    ),
    delete=extend_schema(
        tags=['library'],
        operation_id='api_v1_library_destroy',
        parameters=[COMPANY_QUERY],
        responses={204: None, **_API_ERRORS},
    ),
)
class LibraryDetailView(APIView):
    def _row(self, request, library_id):
        company_id = company_from_request(request)
        require_admin(request, company_id)
        return library_template(company_id, library_id)

    def get(self, request, library_id):
        try:
            row = ensure_composed(self._row(request, library_id))
        except SendError as exc:
            return error_response(exc, request)
        return Response(library_detail(row))

    def put(self, request, library_id):
        try:
            row = update_library_template(self._row(request, library_id), request.data)
        except SubstitutionError:
            return error_response(SendError(400, 'validation_failed', {'document': ['template_tags_forbidden']}), request)
        except SendError as exc:
            return error_response(exc, request)
        return Response(library_detail(row))

    def delete(self, request, library_id):
        try:
            delete_library_template(self._row(request, library_id))
        except SendError as exc:
            return error_response(exc, request)
        return Response(status=204)


def _company_template(request, template_id):
    template = EmailTemplate.objects.filter(pk=template_id).first()
    if template is None:
        raise SendError(404, 'template_not_found')
    user = require_admin(request, template.company_id)
    return template, user


def _require_editable(template: EmailTemplate) -> None:
    if template.kind != EmailTemplate.KIND_BROADCAST:
        return
    if Broadcast.objects.filter(template=template).exclude(state=Broadcast.STATE_DRAFT).exists():
        raise SendError(409, 'broadcast_not_draft')


@extend_schema_view(
    get=extend_schema(
        tags=['templates'],
        operation_id='api_v1_templates_list',
        parameters=[
            COMPANY_QUERY,
            OpenApiParameter('event_type', OpenApiTypes.STR, OpenApiParameter.QUERY, required=False),
        ],
        responses={200: TemplateListSerializer, **_API_ERRORS},
    ),
)
class TemplateListView(APIView):
    def get(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
            event_type = str(request.query_params.get('event_type') or '')
            definition = None
            if event_type:
                definition = get_definition(event_type)
                if definition is None:
                    raise SendError(400, 'event_unknown')
        except SendError as exc:
            return error_response(exc, request)
        rows = (
            EmailTemplate.objects.filter(company_id=company_id)
            .exclude(kind__in=EmailTemplate.OWNED_KINDS)
            .order_by('event_type', 'language', 'id')
        )
        if definition is not None:
            rows = [row for row in rows if fits_event(row, definition)]
        return Response({'templates': [template_summary(row) for row in rows]})


@extend_schema_view(
    get=extend_schema(
        tags=['templates'],
        operation_id='api_v1_templates_retrieve',
        responses={200: TemplateSummarySerializer, **_API_ERRORS},
    ),
    delete=extend_schema(
        tags=['templates'],
        operation_id='api_v1_templates_destroy',
        responses={204: None, **_API_ERRORS},
    ),
)
class TemplateDetailView(APIView):
    def get(self, request, template_id):
        try:
            template, _user = _company_template(request, template_id)
        except SendError as exc:
            return error_response(exc, request)
        return Response(template_summary(template))

    def delete(self, request, template_id):
        from django.db.models import ProtectedError

        try:
            template, _user = _company_template(request, template_id)
            template.delete()
        except ProtectedError:
            return error_response(SendError(409, 'template_in_use'), request)
        except SendError as exc:
            return error_response(exc, request)
        return Response(status=204)


def _version_payload(version: TemplateVersion) -> dict:
    published_at = None
    if version.published_at:
        published_at = version.published_at.isoformat().replace('+00:00', 'Z')
    return {
        'number': version.number,
        'state': version.state,
        'subject': version.subject,
        'preheader': version.preheader,
        'document': version.document,
        'translations': version.translations or {},
        'theme': version.theme or {},
        'published_at': published_at,
    }


@extend_schema_view(
    get=extend_schema(
        tags=['templates'],
        operation_id='api_v1_template_versions_list',
        responses={200: TemplateVersionListSerializer, **_API_ERRORS},
    ),
    post=extend_schema(
        tags=['templates'],
        operation_id='api_v1_template_versions_create',
        request=TemplateVersionCreateRequestSerializer,
        responses={201: TemplateVersionCreateResponseSerializer, **_API_ERRORS},
    ),
)
class TemplateVersionListView(APIView):
    def get(self, request, template_id):
        try:
            template, _user = _company_template(request, template_id)
        except SendError as exc:
            return error_response(exc, request)
        return Response({'versions': [_version_payload(version) for version in template.versions.all()]})

    def post(self, request, template_id):
        try:
            template, _user = _company_template(request, template_id)
            _require_editable(template)
            data = request.data if isinstance(request.data, dict) else {}
            latest = template.versions.order_by('-number').first()
            subject = str(data.get('subject') or (latest.subject if latest else ''))
            preheader = str(data['preheader'] or '') if 'preheader' in data else (latest.preheader if latest else '')
            translations = data['translations'] if 'translations' in data else (latest.translations if latest else {})
            theme = data['theme'] if 'theme' in data else (latest.theme if latest else {})
            if data.get('library_id') not in (None, ''):
                source = library_template(template.company_id, data.get('library_id'))
                document = library_document_for(template, source)
                template.source_key = source.key
                template.set = source.set
                translations = translated_inbox(translations)
                theme = source.theme or theme
            else:
                document = data.get('document')
                if not isinstance(document, dict):
                    raise SendError(400, 'validation_failed', {'document': ['required']})
            version = create_version(
                template,
                document=document,
                subject=subject,
                preheader=preheader,
                translations=translations,
                theme=theme,
                user_id=getattr(request.user, 'user_id', None),
            )
            template.save(update_fields=['source_key', 'set'])
        except SubstitutionError:
            return error_response(SendError(400, 'validation_failed', {'document': ['template_tags_forbidden']}), request)
        except SendError as exc:
            return error_response(exc, request)
        return Response({'number': version.number, 'state': version.state}, status=201)


@extend_schema_view(
    post=extend_schema(
        tags=['templates'],
        operation_id='api_v1_template_versions_publish',
        request=None,
        responses={200: TemplatePublishResponseSerializer, **_API_ERRORS},
    ),
)
class TemplatePublishView(APIView):
    def post(self, request, template_id, number):
        try:
            template, _user = _company_template(request, template_id)
            _require_editable(template)
            version = TemplateVersion.objects.filter(template=template, number=number).first()
            if version is None:
                raise SendError(404, 'template_not_found')
            publish_version(template, version)
        except SubstitutionError:
            return error_response(SendError(400, 'validation_failed', {'document': ['template_tags_forbidden']}), request)
        except SendError as exc:
            return error_response(exc, request)
        return Response({'number': version.number, 'state': version.state, 'checksum': version.checksum})


@extend_schema_view(
    get=extend_schema(
        tags=['templates'],
        operation_id='api_v1_template_versions_retrieve',
        responses={200: TemplateVersionSerializer, **_API_ERRORS},
    ),
)
class TemplateVersionDetailView(APIView):
    def get(self, request, template_id, number):
        try:
            template, _user = _company_template(request, template_id)
            version = TemplateVersion.objects.filter(template=template, number=number).first()
            if version is None:
                raise SendError(404, 'template_not_found')
        except SendError as exc:
            return error_response(exc, request)
        return Response(_version_payload(version))


def _example_variables(definition: dict | None) -> dict:
    variables = {
        'system.message_id': 'msg_test',
        'system.assets_url': assets_url(),
    }
    for item in (definition or {}).get('variables') or []:
        example = item.get('example') or ''
        if example:
            variables[item['token']] = example
    return variables


@extend_schema_view(
    post=extend_schema(
        tags=['templates'],
        operation_id='api_v1_templates_send_test',
        request=TemplateTestSendRequestSerializer,
        responses={200: TestSendResponseSerializer, **_API_ERRORS},
    ),
)
class TemplateTestSendView(APIView):
    def post(self, request, template_id):
        try:
            template, user = _company_template(request, template_id)
            jwt_email = (getattr(user, 'email', '') or '').strip()
            requested = request.data.get('to') if 'to' in request.data else None
            if requested in (None, ''):
                to_email = jwt_email
            else:
                to_email = str(requested).strip()
                if not getattr(user, 'is_staff', False) and to_email.lower() != jwt_email.lower():
                    raise SendError(403, 'forbidden')
            if '@' not in to_email:
                raise SendError(400, 'validation_failed', {'to': ['required']})
            company_id = template.company_id
            if 'document' in request.data:
                subject = str(request.data.get('subject') or '')
                preheader = str(request.data.get('preheader') or '')
                if not subject:
                    raise SendError(400, 'validation_failed', {'subject': ['required']})
                document = validate_document(request.data.get('document'))
                document_tokens(document, subject, preheader)
                colors = theme_colors(clean_theme(request.data.get('theme')))
                html, text = compose(document, head=set_head(template.set), preheader=preheader, colors=colors)
            else:
                draft = template.versions.filter(state=TemplateVersion.STATE_DRAFT).order_by('-number').first()
                if draft is None:
                    raise SendError(400, 'validation_failed', {'version': ['draft_required']})
                subject, html, text = draft.subject, draft.html, draft.text
                if not html:
                    html, text = compose(
                        draft.document,
                        head=set_head(template.set),
                        preheader=draft.preheader,
                        colors=theme_colors(draft.theme),
                    )
            definition = definition_for(template)
            variables = _example_variables(definition)
            if not definition or definition.get('lane_class') != 'auth':
                category = (definition or {}).get('lane_class') or 'transactional'
                link = unsubscribe_url(int(company_id), to_email, category)
                variables['system.unsubscribe_url'] = link
                variables['system.preferences_url'] = link
            url_tokens = _url_tokens(definition) if definition else {}
            subject, _missing_subject = substitute(subject, variables, html=False, subject=True, url_tokens=url_tokens)
            html, _missing_html = substitute(
                html, variables, html=True, url_tokens=url_tokens, allow_http_localhost=settings.DEBUG
            )
            text, _missing_text = substitute(
                text, variables, html=False, url_tokens=url_tokens, allow_http_localhost=settings.DEBUG
            )
            result = send_test_message(company_id=company_id, to_email=to_email, subject=subject, html=html, text=text)
        except SubstitutionError:
            return error_response(SendError(400, 'validation_failed', {'document': ['template_tags_forbidden']}), request)
        except SendError as exc:
            return error_response(exc, request)
        return Response(result)


@extend_schema_view(
    get=extend_schema(
        tags=['rules'],
        operation_id='api_v1_rules_list',
        parameters=[
            COMPANY_QUERY,
            OpenApiParameter('service', OpenApiTypes.STR, OpenApiParameter.QUERY, required=False),
        ],
        responses={200: EmailRuleListSerializer, **_API_ERRORS},
    ),
    post=extend_schema(
        tags=['rules'],
        operation_id='api_v1_rules_create',
        parameters=[COMPANY_QUERY],
        request=EmailRuleWriteSerializer,
        responses={201: EmailRuleSerializer, **_API_ERRORS},
    ),
)
class RuleListView(APIView):
    def get(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
        except SendError as exc:
            return error_response(exc, request)
        ensure_builtin_rules(company_id)
        rows = EmailRule.objects.filter(company_id=company_id).select_related('template')
        service = str(request.query_params.get('service') or '')
        if service:
            rows = rows.filter(service=service)
        return Response({'company_id': company_id, 'rules': [rule_payload(row) for row in rows]})

    def post(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
            rule = create_rule(company_id, request.data, user_id=getattr(request.user, 'user_id', None))
        except SendError as exc:
            return error_response(exc, request)
        return Response(rule_payload(rule), status=201)


@extend_schema_view(
    get=extend_schema(
        tags=['rules'],
        operation_id='api_v1_rules_retrieve',
        parameters=[COMPANY_QUERY],
        responses={200: EmailRuleSerializer, **_API_ERRORS},
    ),
    patch=extend_schema(
        tags=['rules'],
        operation_id='api_v1_rules_partial_update',
        parameters=[COMPANY_QUERY],
        request=EmailRulePatchSerializer,
        responses={200: EmailRuleSerializer, **_API_ERRORS},
    ),
    delete=extend_schema(
        tags=['rules'],
        operation_id='api_v1_rules_destroy',
        parameters=[COMPANY_QUERY],
        responses={204: None, **_API_ERRORS},
    ),
)
class RuleDetailView(APIView):
    def _rule(self, request, rule_id):
        company_id = company_from_request(request)
        require_admin(request, company_id)
        rule = EmailRule.objects.filter(pk=rule_id, company_id=company_id).select_related('template').first()
        if rule is None:
            raise SendError(404, 'not_found')
        return rule

    def get(self, request, rule_id):
        try:
            rule = self._rule(request, rule_id)
        except SendError as exc:
            return error_response(exc, request)
        return Response(rule_payload(rule))

    def patch(self, request, rule_id):
        try:
            rule = update_rule(self._rule(request, rule_id), request.data)
        except SendError as exc:
            return error_response(exc, request)
        return Response(rule_payload(rule))

    def delete(self, request, rule_id):
        try:
            delete_rule(self._rule(request, rule_id))
        except SendError as exc:
            return error_response(exc, request)
        return Response(status=204)


def _provider_common() -> dict:
    return {
        'fallback_provider': settings.EMAIL_FALLBACK_PROVIDER,
        'fallback_configured': platform_fallback_configured(),
        'smtp_allowed': bool(settings.EMAIL_ALLOW_COMPANY_SMTP),
        'auth_link_hosts': list(settings.EMAIL_AUTH_LINK_HOSTS),
    }


def _provider_payload(row: CompanyProvider | None) -> dict:
    common = _provider_common()
    if row is None:
        return {
            'configured': False,
            'provider': None,
            'from_email': settings.DEFAULT_FROM_EMAIL,
            'from_name': '',
            'sending_domain': '',
            'bulk_from_email': settings.BULK_FROM_EMAIL,
            'credentials_hint': '',
            'webhook_configured': False,
            'webhook_hint': '',
            **common,
        }
    return {
        'configured': row.configured,
        'provider': row.provider,
        'from_email': row.from_email,
        'from_name': row.from_name,
        'sending_domain': row.sending_domain,
        'bulk_from_email': row.bulk_from_email,
        'credentials_hint': row.credentials_hint,
        'webhook_configured': bool(row.webhook_ciphertext),
        'webhook_hint': row.webhook_hint,
        **common,
    }


def _kept_text(request, key: str, existing: CompanyProvider | None, default: str = '') -> str:
    """Omitted fields keep the stored value. A present empty string clears it."""
    if key not in request.data:
        if existing is not None:
            return getattr(existing, key) or ''
        return default
    value = request.data.get(key)
    if value is None:
        return ''
    return str(value)


@extend_schema_view(
    get=extend_schema(
        tags=['provider'],
        operation_id='api_v1_provider_retrieve',
        parameters=[COMPANY_QUERY],
        responses={200: ProviderSerializer, **_API_ERRORS},
    ),
    put=extend_schema(
        tags=['provider'],
        operation_id='api_v1_provider_update',
        parameters=[COMPANY_QUERY],
        request=ProviderWriteSerializer,
        responses={200: ProviderSerializer, **_API_ERRORS},
    ),
)
class ProviderView(APIView):
    def get(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
        except SendError as exc:
            return error_response(exc, request)
        row = CompanyProvider.objects.filter(company_id=company_id).first()
        payload = _provider_payload(row)
        payload['company_id'] = company_id
        return Response(payload)

    def put(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
            provider = str(request.data.get('provider') or '')
            if provider not in ACTIVE_PROVIDER_NAMES:
                raise SendError(400, 'provider_not_available', {'provider': ['not_available']})
            from_email = str(request.data.get('from_email') or '')
            if '@' not in from_email:
                raise SendError(400, 'validation_failed', {'from_email': ['invalid_format']})
            platform_from = {
                settings.DEFAULT_FROM_EMAIL.lower(),
                settings.BULK_FROM_EMAIL.lower(),
            }
            if from_email.lower() in platform_from and company_id not in settings.EMAIL_PLATFORM_COMPANY_IDS:
                raise SendError(403, 'platform_sender_not_allowed', {'from_email': ['platform_from']})
            existing = CompanyProvider.objects.filter(company_id=company_id).first()
            bulk_from = _kept_text(request, 'bulk_from_email', existing)
            if bulk_from.lower() in platform_from and company_id not in settings.EMAIL_PLATFORM_COMPANY_IDS:
                raise SendError(403, 'platform_sender_not_allowed', {'bulk_from_email': ['platform_from']})
            if provider == 'smtp' and not settings.EMAIL_ALLOW_COMPANY_SMTP:
                raise SendError(400, 'company_smtp_disabled')
            credentials = request.data.get('credentials')
            if credentials is None and existing and existing.provider == provider:
                ciphertext = existing.credentials_ciphertext
                hint = existing.credentials_hint
            else:
                if not isinstance(credentials, dict) or not credentials:
                    raise SendError(400, 'validation_failed', {'credentials': ['required']})
                if provider == 'smtp':
                    from apps.actions.ssrf import SSRFError, resolve_public_host

                    host = str(credentials.get('host') or '').strip()
                    try:
                        port = int(credentials.get('port') or 587)
                    except (TypeError, ValueError):
                        raise SendError(400, 'validation_failed', {'credentials': ['invalid_port']})
                    try:
                        resolve_public_host(host, port)
                    except SSRFError:
                        raise SendError(400, 'provider_host_not_public', {'host': ['not_public']})
                secret = credentials.get('api_key') or credentials.get('password') or ''
                credentials = strip_internal_credentials(credentials)
                ciphertext = encrypt_json(credentials, setting='EMAIL_CREDENTIALS_KEY')
                hint = mask_secret(str(secret))
            webhook_secret = request.data.get('webhook_secret')
            if webhook_secret:
                webhook_ciphertext = encrypt_json({'secret': webhook_secret}, setting='EMAIL_CREDENTIALS_KEY')
                webhook_hint = mask_secret(str(webhook_secret))
            elif existing:
                webhook_ciphertext = existing.webhook_ciphertext
                webhook_hint = existing.webhook_hint
            else:
                webhook_ciphertext = ''
                webhook_hint = ''
            row, _created = CompanyProvider.objects.update_or_create(
                company_id=company_id,
                defaults={
                    'provider': provider,
                    'from_email': from_email,
                    'from_name': _kept_text(request, 'from_name', existing),
                    'sending_domain': _kept_text(request, 'sending_domain', existing),
                    'bulk_from_email': bulk_from,
                    'credentials_ciphertext': ciphertext,
                    'credentials_hint': hint,
                    'webhook_ciphertext': webhook_ciphertext,
                    'webhook_hint': webhook_hint,
                    'configured': True,
                },
            )
        except SendError as exc:
            return error_response(exc, request)
        payload = _provider_payload(row)
        payload['company_id'] = company_id
        return Response(payload)


@extend_schema_view(
    post=extend_schema(
        tags=['provider'],
        operation_id='api_v1_provider_test_send',
        parameters=[COMPANY_QUERY],
        request=ProviderTestSendRequestSerializer,
        responses={200: TestSendResponseSerializer, **_API_ERRORS},
    ),
)
class ProviderTestSendView(APIView):
    def post(self, request):
        try:
            company_id = company_from_request(request)
            user = require_admin(request, company_id)
            to_email = str(request.data.get('to') or getattr(user, 'email', '') or '')
            if '@' not in to_email:
                raise SendError(400, 'validation_failed', {'to': ['required']})
            if not getattr(user, 'is_staff', False) and to_email.lower() != (getattr(user, 'email', '') or '').lower():
                raise SendError(403, 'forbidden')
            result = send_test_message(company_id=company_id, to_email=to_email)
        except SendError as exc:
            return error_response(exc, request)
        return Response(result)


@extend_schema_view(
    get=extend_schema(
        tags=['stats'],
        operation_id='api_v1_stats_retrieve',
        parameters=[
            COMPANY_QUERY,
            OpenApiParameter('from', str, OpenApiParameter.QUERY, required=False),
            OpenApiParameter('to', str, OpenApiParameter.QUERY, required=False),
            OpenApiParameter('lane', str, OpenApiParameter.QUERY, required=False),
            OpenApiParameter('event_type', str, OpenApiParameter.QUERY, required=False),
        ],
        responses={200: StatsSerializer, **_API_ERRORS},
    ),
)
class StatsView(APIView):
    def get(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
        except SendError as exc:
            return error_response(exc, request)
        payload = company_stats(
            company_id,
            raw_from=request.query_params.get('from'),
            raw_to=request.query_params.get('to'),
            lane=request.query_params.get('lane') or None,
            event_type=request.query_params.get('event_type') or None,
        )
        return Response(payload)


@extend_schema_view(
    get=extend_schema(
        tags=['suppressions'],
        operation_id='api_v1_suppressions_list',
        parameters=[COMPANY_QUERY],
        responses={200: SuppressionListSerializer, **_API_ERRORS},
    ),
    post=extend_schema(
        tags=['suppressions'],
        operation_id='api_v1_suppressions_create',
        parameters=[COMPANY_QUERY],
        request=SuppressionCreateSerializer,
        responses={201: SuppressionCreatedSerializer, **_API_ERRORS},
    ),
)
class SuppressionListView(APIView):
    def get(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
        except SendError as exc:
            return error_response(exc, request)
        rows = Suppression.objects.filter(company_id=company_id).order_by('-created_at')[:200]
        return Response(
            {
                'suppressions': [
                    {
                        'id': row.pk,
                        'email_masked': row.email_masked,
                        'reason': row.reason,
                        'lanes': row.lanes,
                        'expires_at': row.expires_at.isoformat().replace('+00:00', 'Z') if row.expires_at else None,
                    }
                    for row in rows
                ]
            }
        )

    def post(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
            email = str(request.data.get('email') or '')
            if '@' not in email:
                raise SendError(400, 'validation_failed', {'email': ['invalid_format']})
            reason = str(request.data.get('reason') or Suppression.REASON_MANUAL)
            row = Suppression.objects.create(
                company_id=company_id,
                email_hmac=email_hmac(email),
                email_masked=mask_email(email),
                reason=reason,
                lanes=request.data.get('lanes') or [],
            )
        except SendError as exc:
            return error_response(exc, request)
        return Response({'id': row.pk, 'email_masked': row.email_masked, 'reason': row.reason}, status=201)


@extend_schema_view(
    delete=extend_schema(
        tags=['suppressions'],
        operation_id='api_v1_suppressions_destroy',
        responses={204: None, **_API_ERRORS},
    ),
)
class SuppressionDetailView(APIView):
    def delete(self, request, suppression_id):
        row = Suppression.objects.filter(pk=suppression_id).first()
        if row is None:
            return error_response(SendError(404, 'not_found'), request)
        try:
            require_admin(request, row.company_id, allow_platform=row.company_id is None)
        except SendError as exc:
            return error_response(exc, request)
        row.delete()
        return Response(status=204)


@extend_schema_view(
    post=extend_schema(
        tags=['lanes'],
        operation_id='api_v1_lanes_switch',
        request=None,
        responses={200: LaneStateSerializer, **_API_ERRORS},
    ),
)
class LanePauseView(APIView):
    def post(self, request, lane, action):
        try:
            require_staff(request)
        except SendError as exc:
            return error_response(exc, request)
        if lane not in {'auth', 'transactional', 'bulk'}:
            return error_response(SendError(404, 'not_found'), request)
        if action not in {'pause', 'resume'}:
            return error_response(SendError(404, 'not_found'), request)
        paused = action == 'pause'
        defaults = {
            'paused': paused,
            'paused_at': timezone.now() if paused else None,
            'reason': 'staff' if paused else '',
        }
        state = LaneState.objects.filter(lane=lane, company_id__isnull=True).first()
        if state is None:
            state = LaneState.objects.create(lane=lane, company_id=None, **defaults)
        else:
            for key, value in defaults.items():
                setattr(state, key, value)
            state.save()
        return Response({'lane': state.lane, 'paused': state.paused})


@extend_schema_view(
    post=extend_schema(
        tags=['privacy'],
        operation_id='api_v1_privacy_erase',
        request=PrivacyEraseRequestSerializer,
        responses={200: PrivacyEraseResponseSerializer, **_API_ERRORS},
    ),
)
class PrivacyEraseView(APIView):
    def post(self, request):
        user = request.user
        is_identity = isinstance(user, ServicePrincipal) and user.service == 'identity'
        if not is_identity and not getattr(user, 'is_staff', False):
            return error_response(SendError(403, 'forbidden'), request)
        try:
            company_id = int(request.data.get('company_id'))
        except (TypeError, ValueError):
            return error_response(SendError(400, 'validation_failed', {'company_id': ['required']}), request)
        email = str(request.data.get('email') or '')
        if '@' not in email:
            return error_response(SendError(400, 'validation_failed', {'email': ['invalid_format']}), request)
        hmac_value = email_hmac(email)
        messages = Message.objects.filter(company_id=company_id, email_hmac=hmac_value)
        message_ids = list(messages.values_list('id', flat=True))
        from apps.email.models import MessageEvent

        MessageEvent.objects.filter(message_id__in=message_ids).delete()
        deleted, _details = messages.delete()
        from apps.email.models import BroadcastRecipient

        BroadcastRecipient.objects.filter(broadcast__company_id=company_id, email_hmac=hmac_value).delete()
        NewsletterSubscriber.objects.filter(list__company_id=company_id, email_hmac=hmac_value).delete()
        return Response({'deleted_messages': deleted, 'email_masked': mask_email(email)})


@extend_schema_view(
    get=extend_schema(
        tags=['platform-metrics'],
        operation_id='api_v1_metrics_retrieve',
        parameters=[COMPANY_QUERY],
        responses={
            (200, 'text/plain'): OpenApiResponse(
                response=OpenApiTypes.STR,
                description='text/plain Prometheus exposition',
            ),
            401: ErrorSerializer,
            403: ErrorSerializer,
        },
    ),
)
class MetricsView(APIView):
    renderer_classes = [PrometheusTextRenderer]

    def get(self, request):
        from apps.email.metrics import METRICS_CONTENT_TYPE, metrics_http_body

        user = request.user
        if isinstance(user, ServicePrincipal) or not getattr(user, 'is_authenticated', False):
            return error_response(SendError(401, 'unauthorized'), request)
        company_param = request.query_params.get('company_id')
        if company_param:
            try:
                company_id = int(company_param)
                require_admin(request, company_id)
            except SendError as exc:
                return error_response(exc, request)
            body = metrics_http_body(company_id=company_id)
        elif getattr(user, 'is_staff', False) or getattr(user, 'access_global_metrics', False):
            body = metrics_http_body()
        elif getattr(user, 'is_company_owner', False) and user.company_id is not None:
            body = metrics_http_body(company_id=int(user.company_id))
        else:
            return error_response(SendError(403, 'forbidden'), request)
        return HttpResponse(body, content_type=METRICS_CONTENT_TYPE)


@extend_schema_view(
    post=extend_schema(
        tags=['provider'],
        operation_id='api_v1_provider_webhooks_resend_create',
        auth=[],
        parameters=[OpenApiParameter('company_id', int, OpenApiParameter.QUERY, required=False)],
        request=WebhookEventSerializer,
        responses={200: WebhookAckSerializer, 400: ErrorSerializer, 401: ErrorSerializer},
    ),
)
class ProviderWebhookView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, stream):
        raw = request.body or b''
        headers = {key.lower(): value for key, value in request.headers.items()}
        company_id = request.query_params.get('company_id')
        if company_id:
            try:
                company_int = int(company_id)
            except (TypeError, ValueError):
                return error_response(SendError(401, 'unauthorized'), request)
            row = CompanyProvider.objects.filter(company_id=company_int, provider='resend').first()
            secret = ''
            if row and row.webhook_ciphertext:
                try:
                    secret = decrypt_json(row.webhook_ciphertext, setting='EMAIL_CREDENTIALS_KEY').get('secret') or ''
                except Exception:
                    secret = ''
        else:
            secret = settings.RESEND_WEBHOOK_SECRET
        if not secret or not verify_svix(secret=secret, body=raw, headers=headers):
            return error_response(SendError(401, 'unauthorized'), request)
        try:
            payload = json.loads(raw.decode('utf-8'))
        except json.JSONDecodeError:
            return error_response(SendError(400, 'validation_failed'), request)
        event_name = str(payload.get('type') or '')
        data = payload.get('data') or {}
        if not isinstance(data, dict):
            return error_response(SendError(400, 'validation_failed'), request)
        from apps.email.broadcasts import apply_broadcast_event, apply_contact_unsubscribe

        if event_name == 'contact.updated':
            company_ids = [company_int] if company_id else list(settings.EMAIL_PLATFORM_COMPANY_IDS)
            apply_contact_unsubscribe(company_ids, data)
            return Response({'status': 'ok'})
        if data.get('broadcast_id'):
            apply_broadcast_event(event_name, data)
            return Response({'status': 'ok'})
        provider_message_id = str(data.get('email_id') or data.get('id') or '')
        provider_event_id = headers.get('svix-id') or ''
        from apps.email.service import apply_provider_event

        apply_provider_event(
            provider_message_id=provider_message_id,
            event_name=event_name,
            provider_event_id=provider_event_id,
            detail={'stream': stream, 'bounce_type': (data.get('bounce') or {}).get('type') or data.get('bounce_type') or 'permanent'},
        )
        return Response({'status': 'ok'})


def _unsubscribe_token(company_id: int, email: str, category: str) -> str:
    return unsubscribe_token(company_id, email, category)


@extend_schema_view(
    get=extend_schema(
        tags=['service-clients'],
        operation_id='api_v1_service_clients_list',
        responses={200: ServiceClientListSerializer, **_API_ERRORS},
    ),
    post=extend_schema(
        tags=['service-clients'],
        operation_id='api_v1_service_clients_create',
        request=ServiceClientCreateSerializer,
        responses={201: ServiceClientCreatedSerializer, **_API_ERRORS},
    ),
)
class ServiceClientListView(APIView):
    def get(self, request):
        try:
            require_staff(request)
        except SendError as exc:
            return error_response(exc, request)
        rows = ServiceClient.objects.all().order_by('service', 'id')
        return Response(
            {
                'clients': [
                    {
                        'id': row.pk,
                        'service': row.service,
                        'name': row.name,
                        'key_prefix': row.key_prefix,
                        'allowed_lanes': row.allowed_lanes,
                        'allowed_template_prefixes': row.allowed_template_prefixes,
                        'active': row.active,
                        'last_used_at': row.last_used_at.isoformat().replace('+00:00', 'Z') if row.last_used_at else None,
                    }
                    for row in rows
                ]
            }
        )

    def post(self, request):
        try:
            require_staff(request)
        except SendError as exc:
            return error_response(exc, request)
        from apps.email.keys import issue_service_key

        service = str(request.data.get('service') or '')
        if not service:
            return error_response(SendError(400, 'validation_failed', {'service': ['required']}), request)
        raw, row = issue_service_key(
            service=service,
            name=str(request.data.get('name') or service),
            allowed_lanes=request.data.get('allowed_lanes') or ['transactional'],
            allowed_template_prefixes=request.data.get('allowed_template_prefixes') or [f'{service}.'],
            allowed_company_ids=request.data.get('allowed_company_ids'),
        )
        return Response(
            {
                'id': row.pk,
                'service': row.service,
                'key': raw,
                'key_prefix': row.key_prefix,
            },
            status=201,
        )


def _company_broadcast(request, broadcast_id):
    broadcast = Broadcast.objects.select_related('template').filter(pk=broadcast_id).first()
    if broadcast is None:
        raise SendError(404, 'broadcast_not_found')
    user = require_admin(request, broadcast.company_id)
    return broadcast, user


def _audience_authorization(request, user, company_id: int) -> str:
    """Identity answers for the company on the token, so it must be the broadcast's company."""
    token_company = getattr(user, 'company_id', None)
    if token_company is None or int(token_company) != int(company_id):
        raise SendError(403, 'company_mismatch')
    return request.META.get('HTTP_AUTHORIZATION', '')


@extend_schema_view(
    get=extend_schema(
        tags=['broadcasts'],
        operation_id='api_v1_broadcasts_list',
        parameters=[COMPANY_QUERY],
        responses={200: BroadcastListSerializer, **_API_ERRORS},
    ),
    post=extend_schema(
        tags=['broadcasts'],
        operation_id='api_v1_broadcasts_create',
        parameters=[COMPANY_QUERY],
        request=BroadcastCreateSerializer,
        responses={201: BroadcastSerializer, **_API_ERRORS},
    ),
)
class BroadcastListView(APIView):
    def get(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
        except SendError as exc:
            return error_response(exc, request)
        rows = Broadcast.objects.select_related('template').filter(company_id=company_id)
        return Response({'broadcasts': [broadcast_payload(row) for row in rows]})

    def post(self, request):
        try:
            company_id = company_from_request(request)
            user = require_admin(request, company_id)
            broadcast = create_broadcast(company_id, request.data, getattr(user, 'user_id', None))
        except SubstitutionError:
            return error_response(SendError(400, 'validation_failed', {'document': ['template_tags_forbidden']}), request)
        except SendError as exc:
            return error_response(exc, request)
        return Response(broadcast_payload(broadcast, detail=True), status=201)


@extend_schema_view(
    get=extend_schema(
        tags=['broadcasts'],
        operation_id='api_v1_broadcasts_retrieve',
        responses={200: BroadcastSerializer, **_API_ERRORS},
    ),
    patch=extend_schema(
        tags=['broadcasts'],
        operation_id='api_v1_broadcasts_partial_update',
        request=BroadcastPatchSerializer,
        responses={200: BroadcastSerializer, **_API_ERRORS},
    ),
    delete=extend_schema(
        tags=['broadcasts'],
        operation_id='api_v1_broadcasts_destroy',
        responses={204: None, **_API_ERRORS},
    ),
)
class BroadcastDetailView(APIView):
    def get(self, request, broadcast_id):
        try:
            broadcast, _user = _company_broadcast(request, broadcast_id)
        except SendError as exc:
            return error_response(exc, request)
        return Response(broadcast_payload(broadcast, detail=True))

    def patch(self, request, broadcast_id):
        try:
            broadcast, _user = _company_broadcast(request, broadcast_id)
            update_broadcast(broadcast, request.data)
        except SendError as exc:
            return error_response(exc, request)
        return Response(broadcast_payload(broadcast, detail=True))

    def delete(self, request, broadcast_id):
        from django.db import transaction

        try:
            broadcast, _user = _company_broadcast(request, broadcast_id)
            if broadcast.state != Broadcast.STATE_DRAFT:
                raise SendError(409, 'broadcast_not_draft')
        except SendError as exc:
            return error_response(exc, request)
        with transaction.atomic():
            template = broadcast.template
            broadcast.delete()
            template.delete()
        return Response(status=204)


@extend_schema_view(
    post=extend_schema(
        tags=['broadcasts'],
        operation_id='api_v1_broadcasts_preview',
        description='Who would get the broadcast now: counts per send language and skips. Asks identity with your token.',
        request=BroadcastPreviewRequestSerializer,
        responses={200: BroadcastPreviewSerializer, **_API_ERRORS},
    ),
)
class BroadcastPreviewView(APIView):
    def post(self, request, broadcast_id):
        try:
            broadcast, user = _company_broadcast(request, broadcast_id)
            authorization = _audience_authorization(request, user, broadcast.company_id)
            data = request.data if isinstance(request.data, dict) else {}
            audience = clean_audience(data['audience'] if 'audience' in data else broadcast.audience)
            preview = preview_audience(broadcast.company_id, authorization, audience, broadcast.template)
        except SendError as exc:
            return error_response(exc, request)
        return Response(preview)


@extend_schema_view(
    post=extend_schema(
        tags=['broadcasts'],
        operation_id='api_v1_broadcasts_send',
        description=(
            'Snapshot the saved audience and send the published content. Each recipient gets their '
            'language when the content has it, else the main language. Unsubscribed and suppressed '
            'addresses are skipped.'
        ),
        request=None,
        responses={202: BroadcastSerializer, **_API_ERRORS},
    ),
)
class BroadcastSendView(APIView):
    def post(self, request, broadcast_id):
        try:
            broadcast, user = _company_broadcast(request, broadcast_id)
            authorization = _audience_authorization(request, user, broadcast.company_id)
            broadcast = send_broadcast(broadcast, authorization)
        except SendError as exc:
            return error_response(exc, request)
        return Response(broadcast_payload(broadcast, detail=True), status=202)
