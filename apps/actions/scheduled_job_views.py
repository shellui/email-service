"""
Staff-only REST API for scheduled job monitoring (``/api/v1/scheduled-jobs``).

Scheduled jobs are platform-level: company owners get 403, even for their own company.
Responses carry keys and enums only (``health``, ``status``, ``error_key``, count kinds);
the admin panel translates them. See docs/scheduled-jobs.md#monitoring.
"""

from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.actions.models import DeliveryAttempt, ScheduledJobRun
from apps.actions.scheduled_jobs import JOBS, jobs_overview, run_payload
from apps.actions.serializers import (
    ScheduledJobRunDetailSerializer,
    ScheduledJobRunsSerializer,
    ScheduledJobsOverviewSerializer,
)
from apps.authapi.permissions import IsAuthenticatedPrincipal
from apps.email.service import SendError
from apps.email.views import error_response

DEFAULT_RUNS_LIMIT = 20
MAX_RUNS_LIMIT = 100
MAX_CORRELATED = 100

_STAFF_RESPONSES = {
    401: OpenApiResponse(description='Missing or invalid Bearer token'),
    403: OpenApiResponse(description='Not staff (company owners included)'),
}


def _require_platform_staff(request) -> Response | None:
    """Django ``is_staff`` only. No company is needed: the data is not company-scoped."""
    user = getattr(request, 'user', None)
    if not user or not getattr(user, 'is_authenticated', False):
        return error_response(SendError(401, 'unauthorized'), request)
    if not getattr(user, 'is_staff', False):
        return error_response(SendError(403, 'forbidden'), request)
    return None


@extend_schema_view(
    get=extend_schema(
        tags=['scheduled-jobs'],
        summary='Scheduled jobs health (staff)',
        description=(
            'Per job (`retry_webhooks`, `sweep_email_queue`, `purge_expired_data`): `health` '
            '(`healthy`, `overdue`, `failing`, `disabled`), `overdue` (no successful run within '
            '`overdue_after_seconds`), `last_run`, `last_success_at`, `next_expected_at`, `last_counts` '
            'and `last_24h` run counts. Also `scheduler_enabled`, `redis_reachable` (null without a broker) '
            'and the beat heartbeat (`beat_last_seen_at`, `beat_stale`).'
        ),
        operation_id='api_v1_scheduled_jobs_list',
        responses={200: ScheduledJobsOverviewSerializer, **_STAFF_RESPONSES},
    ),
)
class ScheduledJobsView(APIView):
    permission_classes = [IsAuthenticatedPrincipal]
    serializer_class = ScheduledJobsOverviewSerializer

    def get(self, request):
        err = _require_platform_staff(request)
        if err:
            return err
        return Response(jobs_overview())


@extend_schema_view(
    get=extend_schema(
        tags=['scheduled-jobs'],
        summary='Recent runs of one scheduled job (staff)',
        description=(
            'Newest first. Runs are kept 7 days. Runs skipped because another container held the lock '
            'are not stored; see `skipped_locked_total` and the `runs_total` metric.'
        ),
        operation_id='api_v1_scheduled_jobs_runs_list',
        parameters=[
            OpenApiParameter(
                name='limit',
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description=f'1 to {MAX_RUNS_LIMIT} (default {DEFAULT_RUNS_LIMIT}).',
            ),
            OpenApiParameter(
                name='status',
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                enum=['running', 'succeeded', 'failed'],
            ),
        ],
        responses={
            200: ScheduledJobRunsSerializer,
            400: OpenApiResponse(description='Invalid limit or status'),
            404: OpenApiResponse(description='Unknown job'),
            **_STAFF_RESPONSES,
        },
    ),
)
class ScheduledJobRunsView(APIView):
    permission_classes = [IsAuthenticatedPrincipal]
    serializer_class = ScheduledJobRunsSerializer

    def get(self, request, job):
        err = _require_platform_staff(request)
        if err:
            return err
        if job not in JOBS:
            return error_response(SendError(404, 'not_found'), request)
        try:
            limit = int(request.GET.get('limit') or DEFAULT_RUNS_LIMIT)
        except (TypeError, ValueError):
            return error_response(SendError(400, 'validation_failed', {'limit': ['invalid']}), request)
        limit = min(MAX_RUNS_LIMIT, max(1, limit))
        qs = ScheduledJobRun.objects.filter(job=job).order_by('-started_at', '-id')
        st = (request.GET.get('status') or '').strip().lower()
        if st:
            if st not in {choice[0] for choice in ScheduledJobRun.STATUS_CHOICES}:
                return error_response(SendError(400, 'validation_failed', {'status': ['invalid']}), request)
            qs = qs.filter(status=st)
        return Response({'job': job, 'results': [run_payload(row) for row in qs[:limit]]})


@extend_schema_view(
    get=extend_schema(
        tags=['scheduled-jobs'],
        summary='One scheduled job run with its webhook deliveries (staff)',
        description=(
            f'`webhook_delivery_attempts`: attempts made by this run (at most {MAX_CORRELATED}), across '
            'companies. `email_events` is always an empty list: email-service does not retry posts to '
            f'itself. `*_truncated` is true when more webhook attempts exist. The keys match '
            'identity-service so the admin panel can reuse its scheduled jobs view.'
        ),
        operation_id='api_v1_scheduled_jobs_runs_retrieve',
        responses={
            200: ScheduledJobRunDetailSerializer,
            404: OpenApiResponse(description='Unknown run'),
            **_STAFF_RESPONSES,
        },
    ),
)
class ScheduledJobRunDetailView(APIView):
    permission_classes = [IsAuthenticatedPrincipal]
    serializer_class = ScheduledJobRunDetailSerializer

    def get(self, request, pk):
        err = _require_platform_staff(request)
        if err:
            return err
        run = ScheduledJobRun.objects.filter(pk=pk).first()
        if run is None:
            return error_response(SendError(404, 'not_found'), request)
        attempts = list(
            DeliveryAttempt.objects.filter(scheduled_job_run_id=run.pk)
            .select_related('outbox')
            .order_by('created_at', 'id')[: MAX_CORRELATED + 1]
        )
        payload = run_payload(run)
        payload['webhook_delivery_attempts'] = [
            {
                'id': attempt.pk,
                'delivery_id': str(attempt.outbox_id),
                'company_id': attempt.outbox.company_id,
                'event_type': attempt.outbox.event_type,
                'status': attempt.status,
                'http_status': attempt.http_status,
                'attempt_number': attempt.attempt_number,
                'duration_ms': attempt.duration_ms,
                'trigger': attempt.trigger or None,
                'created_at': attempt.created_at.isoformat(),
            }
            for attempt in attempts[:MAX_CORRELATED]
        ]
        payload['webhook_delivery_attempts_truncated'] = len(attempts) > MAX_CORRELATED
        # email-service is the mail destination. It does not keep an email-event outbox.
        payload['email_events'] = []
        payload['email_events_truncated'] = False
        return Response(payload)
