"""HTTP API for send, rules, templates, provider settings, stats, and health."""

from __future__ import annotations

import hashlib
import hmac
import json

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework.permissions import AllowAny
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
from apps.email.rendering import checksum, document_tokens, render_document
from apps.email.service import (
    SendError,
    accept_batch,
    accept_event,
    accept_send,
    platform_fallback_configured,
    send_test_message,
)
from apps.email.stats import company_stats
from apps.email.substitution import SubstitutionError, substitute
from apps.providers.registry import ACTIVE_PROVIDER_NAMES
from apps.providers.webhooks import verify_svix


def error_response(exc: SendError, request) -> Response:
    body = {'error_code': exc.code}
    if exc.field_errors:
        body['field_errors'] = exc.field_errors
    request_id = getattr(request, 'request_id', None)
    if request_id:
        body['request_id'] = request_id
    return Response(body, status=exc.status)


def _request_id(request) -> dict:
    request_id = getattr(request, 'request_id', None)
    return {'request_id': request_id} if request_id else {}


class HealthView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        payload = {'status': 'ok', 'version': settings.VERSION}
        return Response(payload)


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
            return Response({'events': events})
        return error_response(SendError(401, 'unauthorized'), request)


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


class TemplateListView(APIView):
    def get(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
        except SendError as exc:
            return error_response(exc, request)
        rows = EmailTemplate.objects.filter(company_id=company_id).order_by('template_key', 'language')
        return Response(
            {
                'templates': [
                    {
                        'id': row.pk,
                        'template_key': row.template_key,
                        'language': row.language,
                        'company_id': row.company_id,
                        'active_version': row.active_version,
                    }
                    for row in rows
                ]
            }
        )

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
            template, _created = EmailTemplate.objects.get_or_create(
                template_key=key,
                company_id=company_id,
                language=language,
            )
            pack = definition['languages'][language]
            number = (template.versions.order_by('-number').values_list('number', flat=True).first() or 0) + 1
            TemplateVersion.objects.create(
                template=template,
                number=number,
                state=TemplateVersion.STATE_DRAFT,
                subject=pack['subject'],
                preheader=pack['preheader'],
                document=pack['document'],
                created_by_user_id=getattr(request.user, 'user_id', None),
            )
        except SendError as exc:
            return error_response(exc, request)
        return Response({'id': template.pk, 'template_key': key, 'language': language, 'draft_version': number}, status=201)


class TemplateDetailView(APIView):
    def get(self, request, template_id):
        template = EmailTemplate.objects.filter(pk=template_id).first()
        if template is None:
            return error_response(SendError(404, 'template_not_found'), request)
        try:
            require_admin(request, template.company_id, allow_platform=template.company_id is None)
        except SendError as exc:
            return error_response(exc, request)
        return Response(
            {
                'id': template.pk,
                'template_key': template.template_key,
                'language': template.language,
                'company_id': template.company_id,
                'active_version': template.active_version,
            }
        )

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
        template.delete()
        return Response(status=204)


class TemplateVersionListView(APIView):
    def get(self, request, template_id):
        template = EmailTemplate.objects.filter(pk=template_id).first()
        if template is None:
            return error_response(SendError(404, 'template_not_found'), request)
        try:
            require_admin(request, template.company_id, allow_platform=template.company_id is None)
        except SendError as exc:
            return error_response(exc, request)
        return Response(
            {
                'versions': [
                    {
                        'number': version.number,
                        'state': version.state,
                        'subject': version.subject,
                        'published_at': version.published_at.isoformat().replace('+00:00', 'Z') if version.published_at else None,
                    }
                    for version in template.versions.all()
                ]
            }
        )

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
            number = (template.versions.order_by('-number').values_list('number', flat=True).first() or 0) + 1
            TemplateVersion.objects.create(
                template=template,
                number=number,
                subject=subject,
                preheader=str(request.data.get('preheader') or ''),
                document=document,
                theme_name=str(request.data.get('theme_name') or 'shellui'),
                theme_palette=request.data.get('theme_palette') or {},
                created_by_user_id=getattr(request.user, 'user_id', None),
            )
        except SubstitutionError:
            return error_response(SendError(400, 'validation_failed', {'document': ['template_tags_forbidden']}), request)
        except SendError as exc:
            return error_response(exc, request)
        return Response({'number': number, 'state': 'draft'}, status=201)


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
            definition = get_definition(template.template_key)
            tokens = document_tokens(version.document, version.subject, version.preheader)
            if definition and definition.get('lane_class') == 'auth':
                from apps.email.auth_templates import validate_auth_template

                validate_auth_template(definition, version.document, version.subject, version.preheader)
            if definition and definition['lane_class'] == 'bulk' and 'system.unsubscribe_url' not in tokens:
                raise SendError(400, 'unsubscribe_link_missing')
            html, text, renderer = render_document(version.document)
            version.html = html
            version.text = text
            version.renderer_version = renderer
            version.checksum = checksum(version.subject, html, text)
            version.state = TemplateVersion.STATE_PUBLISHED
            version.published_at = timezone.now()
            version.save()
            TemplateVersion.objects.filter(template=template, state=TemplateVersion.STATE_PUBLISHED).exclude(pk=version.pk).update(
                state=TemplateVersion.STATE_ARCHIVED
            )
            template.active_version = version.number
            template.save(update_fields=['active_version'])
        except SubstitutionError:
            return error_response(SendError(400, 'validation_failed', {'document': ['template_tags_forbidden']}), request)
        except SendError as exc:
            return error_response(exc, request)
        return Response({'number': version.number, 'state': version.state, 'checksum': version.checksum})


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
            html, text, _renderer = render_document(document)
            rendered_subject, missing_subject = substitute(subject, variables, html=False, subject=True)
            rendered_html, missing_html = substitute(html, variables, html=True, allow_http_localhost=settings.DEBUG)
            rendered_text, missing_text = substitute(text, variables, html=False, allow_http_localhost=settings.DEBUG)
        except SubstitutionError as exc:
            return error_response(SendError(400, exc.code), request)
        missing = sorted(set(missing_subject + missing_html + missing_text))
        return Response(
            {
                'subject': rendered_subject,
                'html': rendered_html,
                'text': rendered_text,
                'missing_variables': missing,
            }
        )


class TemplateTestSendView(APIView):
    def post(self, request, template_id):
        template = EmailTemplate.objects.filter(pk=template_id).first()
        if template is None:
            return error_response(SendError(404, 'template_not_found'), request)
        try:
            require_admin(request, template.company_id, allow_platform=template.company_id is None)
        except SendError as exc:
            return error_response(exc, request)
        to_email = getattr(request.user, 'email', '') or ''
        if not to_email:
            return error_response(SendError(400, 'validation_failed', {'to': ['required']}), request)
        company_id = template.company_id or getattr(request.user, 'company_id', None) or 0
        try:
            result = send_test_message(company_id=company_id, to_email=to_email)
        except SendError as exc:
            return error_response(exc, request)
        return Response(result)


class RuleListView(APIView):
    def get(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
        except SendError as exc:
            return error_response(exc, request)
        rules = []
        for definition in all_definitions():
            row = EmailRule.objects.filter(company_id=company_id, event_type=definition['event_type']).first()
            platform = EmailRule.objects.filter(company_id__isnull=True, event_type=definition['event_type']).first()
            source = row or platform
            rules.append(
                {
                    'event_type': definition['event_type'],
                    'service': definition['owner_service'],
                    'template_key': source.template_key if source else definition['key'],
                    'enabled': source.enabled if source else definition['default_enabled'],
                    'language': source.language if source else '',
                    'recipient_mode': source.recipient_mode if source else EmailRule.MODE_HINTS,
                    'static_recipients': source.static_recipients if source else [],
                    'customized': row is not None,
                    'default_enabled': definition['default_enabled'],
                }
            )
        return Response({'company_id': company_id, 'rules': rules})

    def post(self, request):
        return self._save(request)

    def patch(self, request):
        return self._save(request)

    def _save(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
            event_type = str(request.data.get('event_type') or '')
            definition = get_definition(event_type)
            if definition is None:
                raise SendError(400, 'unknown_event')
            enabled = request.data.get('enabled')
            if enabled is None:
                enabled = definition['default_enabled']
            rule, _created = EmailRule.objects.update_or_create(
                company_id=company_id,
                event_type=event_type,
                defaults={
                    'service': definition['owner_service'],
                    'enabled': bool(enabled),
                    'template_key': str(request.data.get('template_key') or definition['key']),
                    'language': str(request.data.get('language') or ''),
                    'recipient_mode': str(request.data.get('recipient_mode') or EmailRule.MODE_HINTS),
                    'static_recipients': request.data.get('static_recipients') or [],
                },
            )
        except SendError as exc:
            return error_response(exc, request)
        return Response(
            {
                'event_type': rule.event_type,
                'enabled': rule.enabled,
                'template_key': rule.template_key,
                'recipient_mode': rule.recipient_mode,
            }
        )


class RuleDetailView(APIView):
    def delete(self, request, event_type):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
        except SendError as exc:
            return error_response(exc, request)
        EmailRule.objects.filter(company_id=company_id, event_type=event_type).delete()
        return Response(status=204)


def _provider_payload(row: CompanyProvider | None) -> dict:
    if row is None:
        return {
            'configured': False,
            'provider': None,
            'fallback_provider': settings.EMAIL_FALLBACK_PROVIDER,
            'fallback_configured': platform_fallback_configured(),
            'from_email': settings.DEFAULT_FROM_EMAIL,
            'bulk_from_email': settings.BULK_FROM_EMAIL,
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
        'fallback_provider': settings.EMAIL_FALLBACK_PROVIDER,
        'fallback_configured': platform_fallback_configured(),
    }


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
            bulk_from = str(request.data.get('bulk_from_email') or '')
            if bulk_from.lower() in platform_from and company_id not in settings.EMAIL_PLATFORM_COMPANY_IDS:
                raise SendError(403, 'platform_sender_not_allowed', {'bulk_from_email': ['platform_from']})
            if provider == 'smtp' and not settings.EMAIL_ALLOW_COMPANY_SMTP:
                raise SendError(400, 'company_smtp_disabled')
            existing = CompanyProvider.objects.filter(company_id=company_id).first()
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
                    'from_name': str(request.data.get('from_name') or ''),
                    'sending_domain': str(request.data.get('sending_domain') or ''),
                    'bulk_from_email': str(request.data.get('bulk_from_email') or ''),
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


class MetricsView(APIView):
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


def unsubscribe_signature(company_id: int, email_hmac_value: str, category: str) -> str:
    return hmac.new(
        settings.EMAIL_HASH_PEPPER.encode('utf-8'),
        f'{company_id}|{email_hmac_value}|{category}'.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def _unsubscribe_token(company_id: int, email: str, category: str) -> str:
    digest = email_hmac(email)
    signature = unsubscribe_signature(company_id, digest, category)
    return f'{company_id}.{category}.{digest}.{signature}'


def parse_unsubscribe_token(token: str) -> tuple[int, str, str] | None:
    parts = (token or '').split('.')
    if len(parts) != 4:
        return None
    try:
        company_id = int(parts[0])
    except ValueError:
        return None
    category, digest, signature = parts[1], parts[2], parts[3]
    if not category or not digest or not signature:
        return None
    expected = unsubscribe_signature(company_id, digest, category)
    if not hmac.compare_digest(expected, signature):
        return None
    return company_id, category, digest


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
