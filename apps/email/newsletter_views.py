"""Newsletter lists: the public sign-up endpoint and the admin API."""

from __future__ import annotations

from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.email import newsletters
from apps.email.access import company_from_request, require_admin
from apps.email.models import NewsletterList
from apps.email.schema import (
    COMPANY_QUERY,
    ErrorSerializer,
    NewsletterImportRequestSerializer,
    NewsletterImportSerializer,
    NewsletterListSerializer,
    NewsletterSerializer,
    NewsletterSubscribeRequestSerializer,
    NewsletterSubscribeResponseSerializer,
    NewsletterSubscriberAddedSerializer,
    NewsletterSubscriberCreateSerializer,
    NewsletterSubscriberPageSerializer,
    NewsletterWriteSerializer,
)
from apps.email.service import SendError
from apps.email.views import _API_ERRORS, error_response

STATUS_QUERY = OpenApiParameter(
    'status', OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, enum=['pending', 'confirmed', 'unsubscribed']
)


@extend_schema_view(
    post=extend_schema(
        tags=['newsletters'],
        operation_id='api_v1_public_newsletters_subscribe',
        auth=[],
        description=(
            'Public sign-up from a website. Sends a confirmation email. The answer is the same whether '
            'or not the address was already on the list. Leave website empty: it is a honeypot.'
        ),
        request=NewsletterSubscribeRequestSerializer,
        responses={
            202: NewsletterSubscribeResponseSerializer,
            400: ErrorSerializer,
            403: ErrorSerializer,
            404: ErrorSerializer,
            429: ErrorSerializer,
            503: ErrorSerializer,
        },
    ),
)
class NewsletterSubscribeView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, public_key):
        try:
            if newsletters.allowed_origins_for(public_key) is None:
                raise SendError(404, 'newsletter_not_found')
            origin = request.headers.get('Origin', '')
            if origin and not newsletters.origin_allowed(public_key, origin):
                raise SendError(403, 'origin_not_allowed')
            newsletter = NewsletterList.objects.filter(public_key=public_key).first()
            if newsletter is None:
                raise SendError(404, 'newsletter_not_found')
            newsletters.subscribe(
                newsletter,
                request.data,
                ip=newsletters.client_ip(request),
                accept_language=request.META.get('HTTP_ACCEPT_LANGUAGE', ''),
            )
        except SendError as exc:
            return error_response(exc, request)
        return Response({'status': 'pending'}, status=202)


def _company_newsletter(request, newsletter_id) -> NewsletterList:
    newsletter = NewsletterList.objects.select_related('confirmation_template').filter(pk=newsletter_id).first()
    if newsletter is None:
        raise SendError(404, 'newsletter_not_found')
    require_admin(request, newsletter.company_id)
    return newsletter


@extend_schema_view(
    get=extend_schema(
        tags=['newsletters'],
        operation_id='api_v1_newsletters_list',
        parameters=[COMPANY_QUERY],
        responses={200: NewsletterListSerializer, **_API_ERRORS},
    ),
    post=extend_schema(
        tags=['newsletters'],
        operation_id='api_v1_newsletters_create',
        description='Creates the list with its own confirmation email, ready to send.',
        parameters=[COMPANY_QUERY],
        request=NewsletterWriteSerializer,
        responses={201: NewsletterSerializer, **_API_ERRORS},
    ),
)
class NewsletterListView(APIView):
    def get(self, request):
        try:
            company_id = company_from_request(request)
            require_admin(request, company_id)
        except SendError as exc:
            return error_response(exc, request)
        rows = NewsletterList.objects.filter(company_id=company_id)
        return Response({'newsletters': [newsletters.list_payload(row) for row in rows]})

    def post(self, request):
        try:
            company_id = company_from_request(request)
            user = require_admin(request, company_id)
            newsletter = newsletters.create_list(company_id, request.data, getattr(user, 'user_id', None))
        except SendError as exc:
            return error_response(exc, request)
        return Response(newsletters.list_payload(newsletter, detail=True), status=201)


@extend_schema_view(
    get=extend_schema(
        tags=['newsletters'],
        operation_id='api_v1_newsletters_retrieve',
        responses={200: NewsletterSerializer, **_API_ERRORS},
    ),
    patch=extend_schema(
        tags=['newsletters'],
        operation_id='api_v1_newsletters_partial_update',
        request=NewsletterWriteSerializer,
        responses={200: NewsletterSerializer, **_API_ERRORS},
    ),
    delete=extend_schema(
        tags=['newsletters'],
        operation_id='api_v1_newsletters_destroy',
        description='Deletes the list, its subscribers and its confirmation email.',
        responses={204: None, **_API_ERRORS},
    ),
)
class NewsletterDetailView(APIView):
    def get(self, request, newsletter_id):
        try:
            newsletter = _company_newsletter(request, newsletter_id)
        except SendError as exc:
            return error_response(exc, request)
        return Response(newsletters.list_payload(newsletter, detail=True))

    def patch(self, request, newsletter_id):
        try:
            newsletter = _company_newsletter(request, newsletter_id)
            newsletters.update_list(newsletter, request.data)
        except SendError as exc:
            return error_response(exc, request)
        return Response(newsletters.list_payload(newsletter, detail=True))

    def delete(self, request, newsletter_id):
        try:
            newsletter = _company_newsletter(request, newsletter_id)
            newsletters.delete_list(newsletter)
        except SendError as exc:
            return error_response(exc, request)
        return Response(status=204)


@extend_schema_view(
    post=extend_schema(
        tags=['newsletters'],
        operation_id='api_v1_newsletters_rotate_key',
        description='New public key. Forms using the old key stop working.',
        request=None,
        responses={200: NewsletterSerializer, **_API_ERRORS},
    ),
)
class NewsletterRotateKeyView(APIView):
    def post(self, request, newsletter_id):
        try:
            newsletter = _company_newsletter(request, newsletter_id)
            newsletters.rotate_key(newsletter)
        except SendError as exc:
            return error_response(exc, request)
        return Response(newsletters.list_payload(newsletter, detail=True))


@extend_schema_view(
    get=extend_schema(
        tags=['newsletters'],
        operation_id='api_v1_newsletters_subscribers_list',
        parameters=[
            STATUS_QUERY,
            OpenApiParameter('email', OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description='Exact address.'),
            OpenApiParameter('page', OpenApiTypes.INT, OpenApiParameter.QUERY, required=False),
        ],
        responses={200: NewsletterSubscriberPageSerializer, **_API_ERRORS},
    ),
    post=extend_schema(
        tags=['newsletters'],
        operation_id='api_v1_newsletters_subscribers_create',
        request=NewsletterSubscriberCreateSerializer,
        responses={201: NewsletterSubscriberAddedSerializer, **_API_ERRORS},
    ),
)
class NewsletterSubscriberListView(APIView):
    def get(self, request, newsletter_id):
        try:
            newsletter = _company_newsletter(request, newsletter_id)
            try:
                page = int(request.query_params.get('page') or 1)
            except ValueError:
                raise SendError(400, 'validation_failed', {'page': ['invalid']})
            payload = newsletters.list_subscribers(
                newsletter,
                status=request.query_params.get('status') or '',
                email=(request.query_params.get('email') or '').strip(),
                page=page,
            )
        except SendError as exc:
            return error_response(exc, request)
        return Response(payload)

    def post(self, request, newsletter_id):
        try:
            newsletter = _company_newsletter(request, newsletter_id)
            outcome, row = newsletters.add_subscriber(newsletter, request.data)
        except SendError as exc:
            return error_response(exc, request)
        return Response({'outcome': outcome, 'subscriber': newsletters.subscriber_payload(row)}, status=201)


@extend_schema_view(
    delete=extend_schema(
        tags=['newsletters'],
        operation_id='api_v1_newsletters_subscribers_destroy',
        description='Removes the subscriber and their address. They can sign up again.',
        responses={204: None, **_API_ERRORS},
    ),
)
class NewsletterSubscriberDetailView(APIView):
    def delete(self, request, newsletter_id, subscriber_id):
        try:
            newsletter = _company_newsletter(request, newsletter_id)
            deleted, _ = newsletter.subscribers.filter(pk=subscriber_id).delete()
            if not deleted:
                raise SendError(404, 'subscriber_not_found')
        except SendError as exc:
            return error_response(exc, request)
        return Response(status=204)


@extend_schema_view(
    get=extend_schema(
        tags=['newsletters'],
        operation_id='api_v1_newsletters_subscribers_export',
        parameters=[STATUS_QUERY],
        responses={(200, 'text/csv'): OpenApiResponse(OpenApiTypes.STR), **_API_ERRORS},
    ),
)
class NewsletterExportView(APIView):
    def get(self, request, newsletter_id):
        try:
            newsletter = _company_newsletter(request, newsletter_id)
            body = newsletters.export_csv(newsletter, status=request.query_params.get('status') or '')
        except SendError as exc:
            return error_response(exc, request)
        response = HttpResponse(body, content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="newsletter-{newsletter.pk}-subscribers.csv"'
        response['Cache-Control'] = 'no-store'
        return response


@extend_schema_view(
    post=extend_schema(
        tags=['newsletters'],
        operation_id='api_v1_newsletters_subscribers_import',
        description=(
            'confirm (default) sends each new address a confirmation email. consented adds them as '
            'confirmed: use it only when they already agreed. Unsubscribed addresses are skipped.'
        ),
        request=NewsletterImportRequestSerializer,
        responses={200: NewsletterImportSerializer, **_API_ERRORS},
    ),
)
class NewsletterImportView(APIView):
    def post(self, request, newsletter_id):
        try:
            newsletter = _company_newsletter(request, newsletter_id)
            data = request.data if isinstance(request.data, dict) else {}
            result = newsletters.import_csv(newsletter, data.get('csv'), data.get('mode'))
        except SendError as exc:
            return error_response(exc, request)
        return Response(result)
