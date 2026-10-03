"""HTTP API for send, rules, templates, provider settings, stats, and health."""

from __future__ import annotations

import json

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
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
from apps.email.models import (
    CompanyProvider,
    EmailRule,
    EmailTemplate,
    LaneState,
    Message,
    ServiceClient,
    Suppression,
    TemplateVersion,
    Unsubscribe,
    parse_message_id,
)
from apps.email.palette import PaletteError, stored_palette
from apps.email.renderers import HtmlRenderer, PrometheusTextRenderer
from apps.email.schema import (
    COMPANY_QUERY,
    BatchRequestSerializer,
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
    MessageCancelSerializer,
    MessageDetailSerializer,
    MessageListSerializer,
    PrivacyEraseRequestSerializer,
    PrivacyEraseResponseSerializer,
    ProviderSerializer,
    ProviderTestSendRequestSerializer,
    ProviderWriteSerializer,
    RenderRequestSerializer,
    RenderResponseSerializer,
    SendRequestSerializer,
    SendResponseSerializer,
    ServiceClientCreateSerializer,
    ServiceClientCreatedSerializer,
    ServiceClientListSerializer,
    StatsSerializer,
    SuppressionCreateSerializer,
    SuppressionCreatedSerializer,
    SuppressionListSerializer,
    TemplateCreateRequestSerializer,
    TemplateCreateResponseSerializer,
    TemplateDefaultsSerializer,
    TemplateListSerializer,
    TemplatePublishResponseSerializer,
    TemplateSummarySerializer,
    TemplateTestSendRequestSerializer,
    TemplateVersionCreateRequestSerializer,
    TemplateVersionCreateResponseSerializer,
    TemplateVersionListSerializer,
    SettingsSerializer,
    SettingsWriteSerializer,
    TemplateVersionSerializer,
    TestSendResponseSerializer,
    ThemeItemSerializer,
    WebhookAckSerializer,
    WebhookEventSerializer,
)
from apps.email.unsubscribe import parse_unsubscribe_token, unsubscribe_token, unsubscribe_url
from apps.providers.credentials import strip_internal_credentials
from apps.email.rendering import document_tokens, render_document
from apps.email.rules import (
    company_theme,
    create_rule,
    delete_rule,
    ensure_builtin_rules,
    fits_event,
    publish_version,
    rule_payload,
    template_summary,
    templates_using_other_theme,
    update_company_theme,
    update_rule,
)
from apps.email.themes import DEFAULT_THEME, is_theme, theme_catalog
from apps.email.service import (
    SendError,
    accept_batch,
    accept_event,
    accept_send,
    platform_fallback_configured,
    send_test_message,
    _url_tokens,
)
from apps.email.stats import company_stats
from apps.email.substitution import SubstitutionError, substitute
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
        tags=['templates'],
        operation_id='api_v1_templates_defaults_retrieve',
        parameters=[
            COMPANY_QUERY,
            OpenApiParameter('template_key', str, OpenApiParameter.QUERY, required=True),
            OpenApiParameter('languages', str, OpenApiParameter.QUERY, required=False),
        ],
        responses={200: TemplateDefaultsSerializer, **_API_ERRORS},
    ),
)
class TemplateDefaultsView(APIView):
    def get(self, request):
        try:
            company_id = company_from_request(request, allow_null=True)
            require_admin(request, company_id if company_id is not None else None, allow_platform=True)
        except SendError as exc:
            return error_response(exc, request)
        key = request.query_params.get('template_key') or ''
        definition = get_definition(key)
        if definition is None:
            return error_response(SendError(404, 'template_not_found'), request)
        languages = [item.strip() for item in (request.query_params.get('languages') or 'en,fr').split(',') if item.strip()]
        packs = {}
        for language in languages:
            pack = definition['languages'].get(language)
            if pack:
                packs[language] = pack
        return Response({'template_key': key, 'languages': packs, 'variables': definition['variables']})


@extend_schema_view(
    get=extend_schema(
        tags=['templates'],
        operation_id='api_v1_templates_list',
        parameters=[COMPANY_QUERY],
        responses={200: TemplateListSerializer, **_API_ERRORS},
    ),
    post=extend_schema(
        tags=['templates'],
        operation_id='api_v1_templates_create',
        parameters=[COMPANY_QUERY],
        request=TemplateCreateRequestSerializer,
        responses={201: TemplateCreateResponseSerializer, **_API_ERRORS},
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
        chosen = company_theme(company_id)
        rows = EmailTemplate.objects.filter(company_id=company_id).order_by('template_key', 'language')
        if definition is not None:
            rows = [row for row in rows if fits_event(row, definition)]
        return Response({'templates': [template_summary(row, company_theme_name=chosen) for row in rows]})

    def post(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
            key = str(request.data.get('template_key') or '')
            language = str(request.data.get('language') or 'en')
            definition = get_definition(key)
            if definition is None:
                raise SendError(404, 'template_not_found')
            if language not in definition['languages']:
                raise SendError(400, 'language_not_available')
            template, created = EmailTemplate.objects.get_or_create(
                template_key=key,
                company_id=company_id,
                language=language,
            )
            if created or not template.name:
                template.name = definition['label'][:120]
                template.event_type = definition['event_type']
                template.save(update_fields=['name', 'event_type'])
            pack = definition['languages'][language]
            number = (template.versions.order_by('-number').values_list('number', flat=True).first() or 0) + 1
            TemplateVersion.objects.create(
                template=template,
                number=number,
                state=TemplateVersion.STATE_DRAFT,
                subject=pack['subject'],
                preheader=pack['preheader'],
                document=pack['document'],
                theme_name=company_theme(company_id),
                created_by_user_id=getattr(request.user, 'user_id', None),
            )
        except SendError as exc:
            return error_response(exc, request)
        return Response({'id': template.pk, 'template_key': key, 'language': language, 'draft_version': number}, status=201)


@extend_schema_view(
    get=extend_schema(
        tags=['templates'],
        operation_id='api_v1_templates_retrieve',
        responses={200: TemplateSummarySerializer, **_API_ERRORS},
    ),
    patch=extend_schema(
        tags=['templates'],
        operation_id='api_v1_templates_partial_update',
        request=None,
        responses={400: ErrorSerializer},
    ),
    delete=extend_schema(
        tags=['templates'],
        operation_id='api_v1_templates_destroy',
        responses={204: None, **_API_ERRORS},
    ),
)
class TemplateDetailView(APIView):
    def get(self, request, template_id):
        template = EmailTemplate.objects.filter(pk=template_id).first()
        if template is None:
            return error_response(SendError(404, 'template_not_found'), request)
        try:
            require_admin(request, template.company_id, allow_platform=template.company_id is None)
        except SendError as exc:
            return error_response(exc, request)
        chosen = company_theme(template.company_id) if template.company_id is not None else DEFAULT_THEME
        return Response(template_summary(template, company_theme_name=chosen))

    def patch(self, request, template_id):
        return error_response(SendError(400, 'validation_failed', {'template': ['use_versions']}), request)

    def delete(self, request, template_id):
        template = EmailTemplate.objects.filter(pk=template_id).first()
        if template is None:
            return error_response(SendError(404, 'template_not_found'), request)
        try:
            require_admin(request, template.company_id, allow_platform=template.company_id is None)
        except SendError as exc:
            return error_response(exc, request)
        from django.db.models import ProtectedError

        try:
            template.delete()
        except ProtectedError:
            return error_response(SendError(409, 'template_in_use'), request)
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
        'theme_name': version.theme_name,
        'theme_palette': version.theme_palette or {},
        'published_at': published_at,
    }


def _theme_name(raw) -> str:
    if not isinstance(raw, str) or not is_theme(raw.strip()):
        raise SendError(400, 'theme_unknown')
    return raw.strip()


def _palette_or_error(raw) -> dict:
    try:
        return stored_palette(raw)
    except PaletteError as exc:
        raise SendError(400, 'validation_failed', {'theme_palette': ['invalid_color']}) from exc


def _rendered(document: dict, palette, theme: str | None = None) -> tuple[str, str, str]:
    try:
        return render_document(document, palette, theme)
    except PaletteError as exc:
        raise SendError(400, 'validation_failed', {'theme_palette': ['invalid_color']}) from exc


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
        template = EmailTemplate.objects.filter(pk=template_id).first()
        if template is None:
            return error_response(SendError(404, 'template_not_found'), request)
        try:
            require_admin(request, template.company_id, allow_platform=template.company_id is None)
        except SendError as exc:
            return error_response(exc, request)
        return Response({'versions': [_version_payload(version) for version in template.versions.all()]})

    def post(self, request, template_id):
        template = EmailTemplate.objects.filter(pk=template_id).first()
        if template is None:
            return error_response(SendError(404, 'template_not_found'), request)
        try:
            require_admin(request, template.company_id, allow_platform=template.company_id is None)
            document = request.data.get('document')
            subject = str(request.data.get('subject') or '')
            if not isinstance(document, dict) or not subject:
                raise SendError(400, 'validation_failed', {'document': ['required']})
            document_tokens(document, subject, str(request.data.get('preheader') or ''))
            if 'theme_name' in request.data:
                theme_name = _theme_name(request.data.get('theme_name'))
            elif template.company_id is not None:
                theme_name = company_theme(template.company_id)
            else:
                theme_name = DEFAULT_THEME
            theme_palette = _palette_or_error(request.data.get('theme_palette') if 'theme_palette' in request.data else None)
            number = (template.versions.order_by('-number').values_list('number', flat=True).first() or 0) + 1
            TemplateVersion.objects.create(
                template=template,
                number=number,
                subject=subject,
                preheader=str(request.data.get('preheader') or ''),
                document=document,
                theme_name=theme_name,
                theme_palette=theme_palette,
                created_by_user_id=getattr(request.user, 'user_id', None),
            )
        except SubstitutionError:
            return error_response(SendError(400, 'validation_failed', {'document': ['template_tags_forbidden']}), request)
        except SendError as exc:
            return error_response(exc, request)
        return Response({'number': number, 'state': 'draft'}, status=201)


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
        template = EmailTemplate.objects.filter(pk=template_id).first()
        if template is None:
            return error_response(SendError(404, 'template_not_found'), request)
        version = TemplateVersion.objects.filter(template=template, number=number).first()
        if version is None:
            return error_response(SendError(404, 'template_not_found'), request)
        try:
            require_admin(request, template.company_id, allow_platform=template.company_id is None)
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
        template = EmailTemplate.objects.filter(pk=template_id).first()
        if template is None:
            return error_response(SendError(404, 'template_not_found'), request)
        version = TemplateVersion.objects.filter(template=template, number=number).first()
        if version is None:
            return error_response(SendError(404, 'template_not_found'), request)
        try:
            require_admin(request, template.company_id, allow_platform=template.company_id is None)
        except SendError as exc:
            return error_response(exc, request)
        return Response(_version_payload(version))


@extend_schema_view(
    post=extend_schema(
        tags=['templates'],
        operation_id='api_v1_render_create',
        request=RenderRequestSerializer,
        responses={200: RenderResponseSerializer, **_API_ERRORS},
    ),
)
class RenderView(APIView):
    def post(self, request):
        user = request.user
        if not (
            isinstance(user, ServicePrincipal)
            or getattr(user, 'is_staff', False)
            or getattr(user, 'is_company_owner', False)
        ):
            return error_response(SendError(401, 'unauthorized'), request)
        language = str(request.data.get('language') or 'en')
        variables = request.data.get('variables') or {}
        document = request.data.get('document')
        subject = ''
        if document is None:
            definition = get_definition(str(request.data.get('template_key') or ''))
            if definition is None:
                return error_response(SendError(404, 'template_not_found'), request)
            pack = definition['languages'].get(language) or definition['languages'].get('en')
            if pack is None:
                return error_response(SendError(400, 'language_not_available'), request)
            document = pack['document']
            subject = pack['subject']
            if not variables:
                variables = {
                    item['token']: item.get('example') or ''
                    for item in definition['variables']
                }
        else:
            subject = str(request.data.get('subject') or '')
        try:
            palette = request.data.get('theme_palette') if 'theme_palette' in request.data else None
            theme = request.data.get('theme_name') if 'theme_name' in request.data else None
            if theme is not None and not is_theme(str(theme)):
                raise SendError(400, 'theme_unknown')
            html, text, _renderer = _rendered(document, palette, str(theme) if theme else None)
            rendered_subject, missing_subject = substitute(subject, variables, html=False, subject=True)
            rendered_html, missing_html = substitute(html, variables, html=True, allow_http_localhost=settings.DEBUG)
            rendered_text, missing_text = substitute(text, variables, html=False, allow_http_localhost=settings.DEBUG)
        except SubstitutionError as exc:
            return error_response(SendError(400, exc.code), request)
        except SendError as exc:
            return error_response(exc, request)
        missing = sorted(set(missing_subject + missing_html + missing_text))
        return Response(
            {
                'subject': rendered_subject,
                'html': rendered_html,
                'text': rendered_text,
                'missing_variables': missing,
            }
        )


def _example_variables(definition: dict | None) -> dict:
    variables = {
        'system.message_id': 'msg_test',
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
        template = EmailTemplate.objects.filter(pk=template_id).first()
        if template is None:
            return error_response(SendError(404, 'template_not_found'), request)
        try:
            user = require_admin(request, template.company_id, allow_platform=template.company_id is None)
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
            company_id = template.company_id or getattr(user, 'company_id', None) or 0
            if 'document' in request.data:
                document = request.data.get('document')
                if not isinstance(document, dict):
                    raise SendError(400, 'validation_failed', {'document': ['required']})
                subject = str(request.data.get('subject') or '')
                preheader = str(request.data.get('preheader') or '')
                if not subject:
                    raise SendError(400, 'validation_failed', {'subject': ['required']})
                palette = _palette_or_error(request.data.get('theme_palette') if 'theme_palette' in request.data else None)
                theme = request.data.get('theme_name') if 'theme_name' in request.data else None
                if theme is not None and not is_theme(str(theme)):
                    raise SendError(400, 'theme_unknown')
                theme = str(theme) if theme else (company_theme(template.company_id) if template.company_id else DEFAULT_THEME)
            else:
                draft = template.versions.filter(state=TemplateVersion.STATE_DRAFT).order_by('-number').first()
                if draft is None:
                    raise SendError(400, 'validation_failed', {'version': ['draft_required']})
                document = draft.document
                subject = draft.subject
                preheader = draft.preheader
                palette = draft.theme_palette or {}
                theme = draft.theme_name
            document_tokens(document, subject, preheader)
            html, text, _renderer = _rendered(document, palette, theme)
            definition = get_definition(template.template_key)
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


_SAMPLE = {
    'en': {
        'preview': 'A short note from Shellui',
        'heading': 'Hello from Shellui',
        'text': 'This is a short sample so you can see the theme.',
        'button': 'Continue',
    },
    'fr': {
        'preview': 'Un court message de Shellui',
        'heading': 'Bonjour de Shellui',
        'text': 'Ceci est un court exemple pour voir le theme.',
        'button': 'Continuer',
    },
}


@extend_schema_view(
    get=extend_schema(
        tags=['themes'],
        operation_id='api_v1_themes_list',
        parameters=[COMPANY_QUERY],
        responses={200: ThemeItemSerializer(many=True), **_API_ERRORS},
    ),
)
class ThemeListView(APIView):
    def get(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
        except SendError as exc:
            return error_response(exc, request)
        themes = [
            {
                'key': key,
                'name': spec['name'],
                'preview_url': f'/api/v1/themes/{key}/preview?language=en',
            }
            for key, spec in theme_catalog().items()
        ]
        return Response(themes)


@extend_schema_view(
    get=extend_schema(
        tags=['themes'],
        operation_id='api_v1_themes_preview',
        parameters=[
            COMPANY_QUERY,
            OpenApiParameter('language', OpenApiTypes.STR, OpenApiParameter.QUERY, required=False),
        ],
        responses={
            (200, 'text/html'): OpenApiResponse(response=OpenApiTypes.STR, description='text/html sample email'),
            400: ErrorSerializer,
            401: ErrorSerializer,
            403: ErrorSerializer,
            404: ErrorSerializer,
        },
    ),
)
class ThemePreviewView(APIView):
    renderer_classes = [HtmlRenderer, JSONRenderer]

    def get(self, request, key):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
            if not is_theme(key):
                raise SendError(400, 'theme_unknown')
            language = str(request.query_params.get('language') or 'en')
            sample = _SAMPLE.get(language)
            if sample is None:
                raise SendError(400, 'language_not_available')
        except SendError as exc:
            return error_response(exc, request)
        document = {
            'preview': sample['preview'],
            'blocks': [
                {'type': 'heading', 'text': sample['heading']},
                {'type': 'text', 'text': sample['text']},
                {'type': 'button', 'text': sample['button'], 'href': 'https://shellui.com'},
            ],
        }
        html, _text, _renderer = render_document(document, None, key)
        return HttpResponse(html, content_type='text/html; charset=utf-8')


@extend_schema_view(
    get=extend_schema(
        tags=['settings'],
        operation_id='api_v1_settings_retrieve',
        parameters=[COMPANY_QUERY],
        responses={200: SettingsSerializer, **_API_ERRORS},
    ),
    put=extend_schema(
        tags=['settings'],
        operation_id='api_v1_settings_update',
        parameters=[COMPANY_QUERY],
        request=SettingsWriteSerializer,
        responses={200: SettingsSerializer, **_API_ERRORS},
    ),
)
class SettingsView(APIView):
    def get(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
        except SendError as exc:
            return error_response(exc, request)
        return Response(
            {
                'theme': company_theme(company_id),
                'templates_using_other_theme': templates_using_other_theme(company_id),
            }
        )

    def put(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
            if not isinstance(request.data, dict) or 'theme' not in request.data or 'apply_to_existing' not in request.data:
                raise SendError(400, 'validation_failed', {'theme': ['required']})
            if not isinstance(request.data.get('apply_to_existing'), bool):
                raise SendError(400, 'validation_failed', {'apply_to_existing': ['invalid']})
            theme, updated = update_company_theme(
                company_id,
                str(request.data.get('theme') or ''),
                apply_to_existing=request.data.get('apply_to_existing'),
            )
        except SendError as exc:
            return error_response(exc, request)
        return Response({'theme': theme, 'updated_templates': updated})


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


@csrf_exempt
def unsubscribe_page(request, token):
    parsed = parse_unsubscribe_token(token)
    if parsed is None:
        return HttpResponse('Unknown token.', status=404, content_type='text/plain')
    company_id, category, hmac_value = parsed
    if request.method == 'GET':
        return HttpResponse(
            '<!doctype html><title>Shellui</title><p>Confirm unsubscribe by submitting this page.</p>',
            content_type='text/html',
        )
    if request.method != 'POST':
        return HttpResponse(status=405)
    Unsubscribe.objects.get_or_create(
        company_id=company_id,
        email_hmac=hmac_value,
        category=category,
        defaults={'source': 'one_click'},
    )
    from apps.actions.emit import emit_email_event

    emit_email_event(
        company_id=company_id,
        event_type='email.unsubscribe.created',
        data={'category': category, 'source': 'one_click'},
    )
    return HttpResponse(status=200)


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
