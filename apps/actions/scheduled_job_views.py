"""
Staff-only REST API for scheduled job monitoring (``/api/v1/scheduled-jobs``).

Scheduled jobs are platform-level: company owners get 403, even for their own company.
Responses carry keys and enums only (``health``, ``status``, ``error_key``, count kinds);
the admin panel translates them. See docs/scheduled-jobs.md#monitoring.
"""

from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.actions.models import DeliveryAttempt, ScheduledJobRun
from apps.actions.scheduled_jobs import JOBS, jobs_overview, run_payload
from apps.actions.serializers import (
    ScheduledJobRunDetailSerializer,
    ScheduledJobRunListSerializer,
    ScheduledJobsOverviewSerializer,
)
from apps.email.access import require_staff
from apps.email.schema import ErrorSerializer
from apps.email.service import SendError
from apps.email.views import error_response

DEFAULT_RUNS_LIMIT = 20
MAX_RUNS_LIMIT = 100
MAX_CORRELATED = 100

_STAFF_ERRORS = {
    401: ErrorSerializer,
    403: ErrorSerializer,
}


@extend_schema_view(
    get=extend_schema(
        tags=['scheduled-jobs'],
        summary='Scheduled jobs and lane workers health (staff)',
        description=(
            'Per job (`retry_webhooks`, `sweep_email_queue`, `purge_expired_data`): `health` (`healthy`, '
            '`overdue`, `failing`, `disabled`), `overdue` (no successful run within `overdue_after_seconds`), '
            '`last_run`, `last_success_at`, `next_expected_at`, `last_counts` and `last_24h` run counts. '
            'Also `scheduler_enabled`, `redis_reachable` (null without a broker), the beat heartbeat '
            '(`beat_last_seen_at`, `beat_stale`), and one entry per lane worker (`last_seen_at`, `stale`).'
        ),
        operation_id='api_v1_scheduled_jobs_list',
        responses={200: ScheduledJobsOverviewSerializer, **_STAFF_ERRORS},
    ),
)
class ScheduledJobsView(APIView):
    def get(self, request):
        try:
            require_staff(request)
        except SendError as exc:
            return error_response(exc, request)
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
                name='job',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                enum=list(JOBS),
            ),
            OpenApiParameter(
                name='limit',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description=f'1 to {MAX_RUNS_LIMIT} (default {DEFAULT_RUNS_LIMIT}).',
            ),
            OpenApiParameter(
                name='status',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                enum=['running', 'succeeded', 'failed'],
            ),
        ],
        responses={200: ScheduledJobRunListSerializer, 400: ErrorSerializer, 404: ErrorSerializer, **_STAFF_ERRORS},
    ),
)
class ScheduledJobRunsView(APIView):
    def get(self, request, job):
        try:
            require_staff(request)
            if job not in JOBS:
                raise SendError(404, 'not_found')
            try:
                limit = int(request.query_params.get('limit') or DEFAULT_RUNS_LIMIT)
            except (TypeError, ValueError):
                raise SendError(400, 'validation_failed', {'limit': ['invalid']})
            status_filter = (request.query_params.get('status') or '').strip().lower()
            if status_filter and status_filter not in {c[0] for c in ScheduledJobRun.STATUS_CHOICES}:
                raise SendError(400, 'validation_failed', {'status': ['invalid']})
        except SendError as exc:
            return error_response(exc, request)
        limit = min(MAX_RUNS_LIMIT, max(1, limit))
        qs = ScheduledJobRun.objects.filter(job=job).order_by('-started_at', '-id')
        if status_filter:
            qs = qs.filter(status=status_filter)
        return Response({'job': job, 'results': [run_payload(run) for run in qs[:limit]]})


@extend_schema_view(
    get=extend_schema(
        tags=['scheduled-jobs'],
        summary='One scheduled job run with its webhook deliveries (staff)',
        description=(
            f'`webhook_delivery_attempts`: Shellui Actions delivery attempts made by this run (at most '
            f'{MAX_CORRELATED}), across companies. `webhook_delivery_attempts_truncated` is true when more exist.'
        ),
        operation_id='api_v1_scheduled_jobs_runs_retrieve',
        responses={200: ScheduledJobRunDetailSerializer, 404: ErrorSerializer, **_STAFF_ERRORS},
    ),
)
class ScheduledJobRunDetailView(APIView):
    def get(self, request, pk):
        try:
            require_staff(request)
            run = ScheduledJobRun.objects.filter(pk=pk).first()
            if run is None:
                raise SendError(404, 'not_found')
        except SendError as exc:
            return error_response(exc, request)
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
                'error_code': attempt.error_code,
                'attempt_number': attempt.attempt_number,
                'duration_ms': attempt.duration_ms,
                'created_at': attempt.created_at.isoformat(),
            }
            for attempt in attempts[:MAX_CORRELATED]
        ]
        payload['webhook_delivery_attempts_truncated'] = len(attempts) > MAX_CORRELATED
        return Response(payload)
