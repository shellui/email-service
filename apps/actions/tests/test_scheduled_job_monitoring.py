"""Scheduled job runs, health, staff API, platform events, metrics, and lane worker heartbeats."""

from __future__ import annotations

import time
from datetime import timedelta
from io import StringIO
from unittest import mock

import redis
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.actions import tasks
from apps.actions.models import (
    ActionOutbox,
    DeliveryAttempt,
    EventLog,
    ScheduledJobCounter,
    ScheduledJobRun,
    ScheduledJobState,
)
from apps.actions.registry import all_event_types
from apps.actions.scheduled_jobs import (
    JOBS,
    PURGE_EXPIRED_DATA,
    RETRY_WEBHOOKS,
    compute_health,
    is_overdue,
    jobs_overview,
    next_expected_at,
    sanitize_error_message,
)
from apps.authapi.principal import EmailPrincipal
from apps.email import tasks as email_tasks
from apps.email.worker_heartbeat import HeartbeatThrottle, read_worker_heartbeats, record_worker_heartbeat
from config.request_context import request_id_var
from config.task_lock import beat_heartbeat_key, lock_key

_SECRET_ERROR = (
    'connect failed https://hooks.example.com/h?token=abc123secret&code=xyz '
    'redis://user:hunter2@redis:6379/0 Authorization: Bearer abcdefghijklmnop password=hunter2'
)
_EMPTY_STATS = {'processed': 0, 'delivered': 0, 'retried': 0, 'dead': 0}
BROKER = 'redis://broker:6379/0'


class FakeRedis:
    """SET NX EX, GET, MGET, PING, and the lock release script."""

    def __init__(self):
        self.store = {}

    def _expire(self):
        now = time.monotonic()
        for key in [k for k, (_, exp) in self.store.items() if exp <= now]:
            del self.store[key]

    def set(self, key, value, nx=False, ex=None):
        self._expire()
        if nx and key in self.store:
            return None
        self.store[key] = (str(value), time.monotonic() + (ex or 3600))
        return True

    def get(self, key):
        self._expire()
        item = self.store.get(key)
        return item[0].encode() if item else None

    def mget(self, keys):
        return [self.get(key) for key in keys]

    def ping(self):
        return True

    def eval(self, script, numkeys, key, token):
        self._expire()
        if key in self.store and self.store[key][0] == token:
            del self.store[key]
            return 1
        return 0


def _counter(job, name):
    row = ScheduledJobCounter.objects.filter(job=job, name=name).first()
    return row.value if row else 0


def _client(*, staff=False, owner_company=None, global_metrics=False):
    client = APIClient()
    client.force_authenticate(
        user=EmailPrincipal(
            user_id=9,
            company_id=owner_company,
            email='someone@acme.com',
            is_staff=staff,
            is_company_owner=owner_company is not None,
            access_global_metrics=global_metrics,
        )
    )
    return client


class FakeRedisMixin:
    def setUp(self):
        super().setUp()
        self.fake = FakeRedis()
        patcher = mock.patch('config.task_lock.get_lock_client', return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)


class RunRecordingTests(FakeRedisMixin, TestCase):
    @mock.patch('apps.actions.management.commands.retry_webhooks.deliver_due_stats')
    def test_command_path_records_a_run(self, deliver):
        deliver.return_value = {'processed': 3, 'delivered': 1, 'retried': 1, 'dead': 1}
        out = StringIO()
        call_command('retry_webhooks', stdout=out)

        run = ScheduledJobRun.objects.get()
        self.assertEqual((run.job, run.trigger, run.status), ('retry_webhooks', 'command', 'succeeded'))
        self.assertEqual(
            run.counts,
            {
                'webhook_deliveries_attempted': 3,
                'webhook_deliveries_succeeded': 1,
                'webhook_deliveries_failed': 1,
                'webhook_deliveries_given_up': 1,
            },
        )
        self.assertIsNotNone(run.finished_at)
        self.assertIsNotNone(run.duration_ms)
        self.assertTrue(run.host)
        self.assertIn(f'run_id={run.pk}', out.getvalue())
        self.assertEqual(deliver.call_args.kwargs['scheduled_job_run_id'], run.pk)

        state = ScheduledJobState.objects.get(job='retry_webhooks')
        self.assertIsNotNone(state.last_success_at)
        self.assertEqual(_counter('retry_webhooks', 'runs.succeeded'), 1)
        self.assertEqual(_counter('retry_webhooks', 'items.webhook_deliveries_attempted'), 3)
        self.assertEqual(_counter('retry_webhooks', 'items.webhook_deliveries_given_up'), 1)

        event = EventLog.objects.get(pk=run.event_log_id)
        self.assertEqual(event.event_type, 'email.scheduled_job.succeeded')
        self.assertIsNone(event.company_id)
        self.assertEqual(event.data['run_id'], run.pk)
        self.assertEqual(event.data['trigger'], 'command')

    def test_each_job_records_through_celery(self):
        for task, job in (
            (tasks.retry_webhooks, 'retry_webhooks'),
            (email_tasks.sweep_email_queue, 'sweep_email_queue'),
            (tasks.purge_expired_data, 'purge_expired_data'),
        ):
            with self.subTest(job=job):
                task.apply().get()
                run = ScheduledJobRun.objects.filter(job=job).get()
                self.assertEqual((run.trigger, run.status), ('celery', 'succeeded'))
                self.assertTrue(set(JOBS[job].item_kinds) <= set(run.counts), run.counts)

    def test_dry_run_is_not_recorded(self):
        call_command('retry_webhooks', '--dry-run', stdout=StringIO())
        self.assertFalse(ScheduledJobRun.objects.exists())

    @mock.patch('config.scheduled_tasks.call_command')
    def test_skipped_locked_counts_without_a_row(self, call):
        self.fake.set(lock_key('retry_webhooks'), 'other-worker', ex=60)
        self.assertEqual(tasks.retry_webhooks.apply().get(), 'skipped')
        call.assert_not_called()
        self.assertFalse(ScheduledJobRun.objects.exists())
        self.assertEqual(_counter('retry_webhooks', 'runs.skipped_locked'), 1)
        self.assertIsNotNone(ScheduledJobState.objects.get(job='retry_webhooks').last_skipped_at)

    @mock.patch('apps.actions.management.commands.retry_webhooks.deliver_due_stats')
    def test_failure_is_recorded_logged_and_sanitized(self, deliver):
        deliver.side_effect = OperationalError(_SECRET_ERROR)
        before = request_id_var.get()

        with self.assertLogs('apps.actions.scheduled_jobs', level='ERROR') as logs:
            with self.assertRaises(CommandError) as ctx:
                call_command('retry_webhooks', stdout=StringIO())

        run = ScheduledJobRun.objects.get()
        self.assertEqual(run.status, 'failed')
        self.assertEqual(run.error_key, 'database_error')
        self.assertEqual(run.error_class, 'OperationalError')
        for secret in ('abc123secret', 'xyz', 'hunter2', 'abcdefghijklmnop', '?token'):
            self.assertNotIn(secret, run.error_message)
            self.assertNotIn(secret, str(ctx.exception))
            self.assertNotIn(secret, logs.output[0])
        self.assertIn('https://hooks.example.com/h', run.error_message)
        self.assertIn(f'run_id={run.pk}', logs.output[0])
        self.assertIn(f'run_id={run.pk}', str(ctx.exception))
        self.assertNotIn('hunter2', ''.join(logs.output))
        self.assertEqual(request_id_var.get(), before, 'request id is restored after the run')

        self.assertEqual(_counter('retry_webhooks', 'runs.failed'), 1)
        state = ScheduledJobState.objects.get(job='retry_webhooks')
        self.assertIsNotNone(state.last_failure_at)
        self.assertIsNone(state.last_success_at)
        event = EventLog.objects.get(pk=run.event_log_id)
        self.assertEqual(event.event_type, 'email.scheduled_job.failed')
        self.assertIsNone(event.company_id)
        self.assertEqual(event.data['error_key'], 'database_error')
        self.assertNotIn('error_message', event.data)

    @mock.patch('apps.email.management.commands.sweep_email_queue.sweep')
    def test_celery_failure_returns_failed_without_raising(self, sweep):
        sweep.side_effect = RuntimeError('boom')
        with self.assertLogs('apps.actions.scheduled_jobs', level='ERROR'):
            self.assertEqual(email_tasks.sweep_email_queue.apply().get(), 'failed')
        run = ScheduledJobRun.objects.get()
        self.assertEqual((run.trigger, run.status, run.error_key), ('celery', 'failed', 'unexpected_error'))
        self.assertEqual(self.fake.store, {}, 'lock released after a failed run')

    def test_redis_down_on_lock_records_a_failed_run(self):
        client = mock.Mock()
        client.set.side_effect = redis.ConnectionError('Error 111 connecting to redis:6379')
        with mock.patch('config.task_lock.get_lock_client', return_value=client):
            with self.assertLogs('apps.actions.scheduled_jobs', level='ERROR'):
                self.assertEqual(tasks.retry_webhooks.apply().get(), 'failed')
        run = ScheduledJobRun.objects.get()
        self.assertEqual((run.trigger, run.status, run.error_key), ('celery', 'failed', 'redis_error'))

    @mock.patch('apps.actions.management.commands.retry_webhooks.deliver_due_stats')
    def test_stale_running_row_is_marked_interrupted(self, deliver):
        deliver.return_value = dict(_EMPTY_STATS)
        stale = ScheduledJobRun.objects.create(
            job='retry_webhooks',
            trigger='celery',
            started_at=timezone.now() - timedelta(minutes=10),
        )
        recent = ScheduledJobRun.objects.create(
            job='retry_webhooks',
            trigger='command',
            started_at=timezone.now() - timedelta(seconds=30),
        )
        with self.assertLogs('apps.actions.scheduled_jobs', level='WARNING'):
            call_command('retry_webhooks', stdout=StringIO())
        stale.refresh_from_db()
        recent.refresh_from_db()
        self.assertEqual((stale.status, stale.error_key), ('failed', 'interrupted'))
        self.assertEqual(recent.status, 'running')


class SanitizeTests(TestCase):
    def test_sanitize_error_message(self):
        cleaned = sanitize_error_message(_SECRET_ERROR)
        for secret in ('abc123secret', 'hunter2', 'abcdefghijklmnop'):
            self.assertNotIn(secret, cleaned)
        self.assertLessEqual(len(sanitize_error_message('x' * 1000)), 300)


class HealthTests(TestCase):
    def _state(self, job, **fields):
        state, _ = ScheduledJobState.objects.get_or_create(job=job)
        for key, value in fields.items():
            setattr(state, key, value)
        state.save()
        return state

    def test_overdue_after_three_intervals(self):
        now = timezone.now()
        state = self._state('retry_webhooks', last_success_at=now - timedelta(seconds=170))
        self.assertFalse(is_overdue(RETRY_WEBHOOKS, state, now))
        state.last_success_at = now - timedelta(seconds=190)
        self.assertTrue(is_overdue(RETRY_WEBHOOKS, state, now))

    def test_health_values(self):
        now = timezone.now()
        fresh = self._state('retry_webhooks', last_success_at=now, last_started_at=now)
        failed = ScheduledJobRun(job='retry_webhooks', trigger='celery', status='failed')
        self.assertEqual(compute_health(RETRY_WEBHOOKS, fresh, None, now, enabled=True), 'healthy')
        self.assertEqual(compute_health(RETRY_WEBHOOKS, fresh, failed, now, enabled=True), 'failing')
        never = self._state('purge_expired_data', created_at=now - timedelta(hours=3))
        self.assertEqual(compute_health(PURGE_EXPIRED_DATA, never, None, now, enabled=False), 'disabled')
        self.assertEqual(compute_health(PURGE_EXPIRED_DATA, never, None, now, enabled=True), 'overdue')

    def test_next_run_of_the_hourly_job_is_minute_17(self):
        now = timezone.now().replace(minute=30, second=0, microsecond=0)
        state = self._state('purge_expired_data', last_started_at=now)
        expected = next_expected_at(PURGE_EXPIRED_DATA, state, now)
        self.assertEqual((expected.minute, expected - now), (17, timedelta(minutes=47)))

    @override_settings(CELERY_BROKER_URL=BROKER)
    def test_overview_reports_redis_beat_and_workers(self):
        fake = FakeRedis()
        now = time.time()
        fake.set(beat_heartbeat_key(), f'{now:.3f}')
        record_worker_heartbeat('auth', now=now, client=fake)
        record_worker_heartbeat('bulk', now=now - 600, client=fake)
        with mock.patch('config.task_lock.get_lock_client', return_value=fake):
            overview = jobs_overview()
        self.assertTrue(overview['redis_reachable'])
        self.assertFalse(overview['beat_stale'])
        self.assertEqual([job['job'] for job in overview['jobs']], list(JOBS))
        workers = {w['lane']: w for w in overview['workers']}
        self.assertFalse(workers['auth']['stale'])
        self.assertTrue(workers['bulk']['stale'])
        self.assertTrue(workers['transactional']['stale'])
        self.assertIsNone(workers['transactional']['last_seen_at'])

    @override_settings(CELERY_BROKER_URL='')
    def test_overview_without_broker(self):
        overview = jobs_overview()
        self.assertIsNone(overview['redis_reachable'])
        self.assertTrue(overview['beat_stale'])
        self.assertTrue(all(w['stale'] and w['last_seen_at'] is None for w in overview['workers']))


class RetentionTests(FakeRedisMixin, TestCase):
    def test_purge_deletes_runs_older_than_seven_days(self):
        old = ScheduledJobRun.objects.create(
            job='retry_webhooks', trigger='celery', status='succeeded', started_at=timezone.now() - timedelta(days=8)
        )
        kept = ScheduledJobRun.objects.create(
            job='retry_webhooks', trigger='celery', status='succeeded', started_at=timezone.now() - timedelta(days=6)
        )
        call_command('purge_expired_data', stdout=StringIO())
        self.assertFalse(ScheduledJobRun.objects.filter(pk=old.pk).exists())
        self.assertTrue(ScheduledJobRun.objects.filter(pk=kept.pk).exists())
        run = ScheduledJobRun.objects.get(job='purge_expired_data')
        self.assertEqual(run.counts['scheduled_job_runs'], 1)


class StaffApiTests(FakeRedisMixin, TestCase):
    def setUp(self):
        super().setUp()
        with mock.patch(
            'apps.actions.management.commands.retry_webhooks.deliver_due_stats', return_value=dict(_EMPTY_STATS)
        ):
            call_command('retry_webhooks', stdout=StringIO())
        self.run = ScheduledJobRun.objects.get()

    def test_permissions(self):
        for path in (
            '/api/v1/scheduled-jobs',
            '/api/v1/scheduled-jobs/retry_webhooks/runs',
            f'/api/v1/scheduled-jobs/runs/{self.run.pk}',
        ):
            with self.subTest(path=path):
                self.assertEqual(APIClient().get(path).status_code, 401)
                self.assertEqual(_client(owner_company=7).get(path).status_code, 403)
                self.assertEqual(_client(global_metrics=True).get(path).status_code, 403)
                self.assertEqual(_client(staff=True).get(path).status_code, 200)

    def test_overview_payload(self):
        body = _client(staff=True).get('/api/v1/scheduled-jobs').json()
        self.assertEqual(set(body), {
            'generated_at', 'scheduler_enabled', 'redis_reachable', 'beat_last_seen_at', 'beat_stale',
            'jobs', 'workers_enabled', 'workers',
        })
        retry = next(job for job in body['jobs'] if job['job'] == 'retry_webhooks')
        self.assertEqual(retry['health'], 'healthy')
        self.assertEqual(retry['last_run']['id'], self.run.pk)
        self.assertEqual(retry['last_24h'], {'succeeded': 1, 'failed': 0})
        self.assertEqual([w['lane'] for w in body['workers']], ['auth', 'transactional', 'bulk'])

    def test_runs_list_filters_and_unknown_job(self):
        staff = _client(staff=True)
        body = staff.get('/api/v1/scheduled-jobs/retry_webhooks/runs?limit=1').json()
        self.assertEqual([r['id'] for r in body['results']], [self.run.pk])
        self.assertEqual(staff.get('/api/v1/scheduled-jobs/retry_webhooks/runs?status=failed').json()['results'], [])
        self.assertEqual(staff.get('/api/v1/scheduled-jobs/retry_webhooks/runs?status=nope').status_code, 400)
        self.assertEqual(staff.get('/api/v1/scheduled-jobs/retry_webhooks/runs?limit=x').status_code, 400)
        self.assertEqual(staff.get('/api/v1/scheduled-jobs/unknown/runs').status_code, 404)
        self.assertEqual(staff.get('/api/v1/scheduled-jobs/runs/999999').status_code, 404)

    def test_run_detail_lists_its_delivery_attempts(self):
        row = ActionOutbox.objects.create(
            company_id=7,
            event_type='email.message.sent',
            envelope={},
            webhook_id='wh_test',
            target_url='https://hooks.example.com/x',
        )
        DeliveryAttempt.objects.create(
            outbox=row, status='success', http_status=200, attempt_number=1, scheduled_job_run_id=self.run.pk
        )
        DeliveryAttempt.objects.create(outbox=row, status='failure', attempt_number=2)
        body = _client(staff=True).get(f'/api/v1/scheduled-jobs/runs/{self.run.pk}').json()
        self.assertEqual(len(body['webhook_delivery_attempts']), 1)
        attempt = body['webhook_delivery_attempts'][0]
        self.assertEqual((attempt['company_id'], attempt['http_status']), (7, 200))
        self.assertFalse(body['webhook_delivery_attempts_truncated'])

    def test_platform_events_are_staff_only(self):
        event_id = self.run.event_log_id
        staff_list = _client(staff=True).get('/api/v1/actions/event-log?scope=platform').json()['events']
        self.assertEqual([e['id'] for e in staff_list], [event_id])
        self.assertEqual(_client(owner_company=7).get('/api/v1/actions/event-log?scope=platform').status_code, 403)
        owner_list = _client(owner_company=7).get('/api/v1/actions/event-log').json()['events']
        self.assertNotIn(event_id, [e['id'] for e in owner_list])
        self.assertEqual(_client(owner_company=7).get(f'/api/v1/actions/event-log/{event_id}').status_code, 403)
        detail = _client(staff=True).get(f'/api/v1/actions/event-log/{event_id}').json()
        self.assertIsNone(detail['company_id'])


class EventCatalogTests(TestCase):
    def test_job_events_are_not_subscribable(self):
        ids = {event.id for event in all_event_types()}
        self.assertNotIn('email.scheduled_job.succeeded', ids)
        self.assertNotIn('email.scheduled_job.failed', ids)


class MetricsTests(FakeRedisMixin, TestCase):
    @override_settings(CELERY_BROKER_URL=BROKER)
    def test_global_metrics_include_scheduled_jobs_and_workers(self):
        with mock.patch(
            'apps.actions.management.commands.retry_webhooks.deliver_due_stats',
            return_value={'processed': 2, 'delivered': 2, 'retried': 0, 'dead': 0},
        ):
            call_command('retry_webhooks', stdout=StringIO())
        self.fake.set(beat_heartbeat_key(), f'{time.time():.3f}')
        record_worker_heartbeat('auth', client=self.fake)

        body = _client(staff=True).get('/api/v1/metrics').content.decode()
        self.assertIn('shellui_email_scheduled_job_runs_total{job="retry_webhooks",status="succeeded"} 1.0', body)
        self.assertIn(
            'shellui_email_scheduled_job_items_total{job="retry_webhooks",kind="webhook_deliveries_succeeded"} 2.0',
            body,
        )
        self.assertIn('shellui_email_scheduled_job_overdue{job="purge_expired_data"}', body)
        self.assertIn('shellui_email_scheduler_redis_up 1.0', body)
        self.assertIn('shellui_email_scheduler_beat_last_seen_timestamp_seconds', body)
        self.assertIn('shellui_email_lane_worker_up{lane="auth"} 1.0', body)
        self.assertIn('shellui_email_lane_worker_up{lane="bulk"} 0.0', body)
        self.assertIn('shellui_email_lane_worker_last_seen_timestamp_seconds{lane="auth"}', body)
        self.assertIn('shellui_email_queue_depth', body)

        global_body = _client(global_metrics=True).get('/api/v1/metrics').content.decode()
        self.assertIn('shellui_email_scheduled_job_runs_total', global_body)

    def test_company_metrics_never_include_scheduled_jobs(self):
        for client, path in (
            (_client(owner_company=7), '/api/v1/metrics'),
            (_client(staff=True), '/api/v1/metrics?company_id=7'),
        ):
            with self.subTest(path=path):
                body = client.get(path).content.decode()
                self.assertIn('shellui_email_queue_depth', body)
                self.assertNotIn('scheduled_job', body)
                self.assertNotIn('lane_worker', body)


class DeliveryCorrelationTests(FakeRedisMixin, TestCase):
    @mock.patch('apps.actions.delivery.resolve_webhook_endpoint', return_value='resolved')
    @mock.patch('apps.actions.delivery._post_pinned')
    def test_attempts_carry_the_run_id(self, post, _resolve):
        post.return_value = mock.Mock(status_code=200, headers={})
        row = ActionOutbox.objects.create(
            company_id=7,
            event_type='email.message.sent',
            envelope={'id': 'evt'},
            webhook_id='wh_corr',
            target_url='https://hooks.example.com/x',
            next_attempt_at=timezone.now(),
        )
        call_command('retry_webhooks', stdout=StringIO())
        run = ScheduledJobRun.objects.get()
        attempt = DeliveryAttempt.objects.get(outbox=row)
        self.assertEqual(attempt.scheduled_job_run_id, run.pk)
        self.assertEqual(run.counts['webhook_deliveries_succeeded'], 1)


class WorkerHeartbeatTests(TestCase):
    def test_record_and_read(self):
        fake = FakeRedis()
        self.assertTrue(record_worker_heartbeat('auth', now=100.0, client=fake))
        self.assertEqual(read_worker_heartbeats(client=fake), {'auth': 100.0})
        self.assertEqual(fake.store['email-service:scheduler:worker:auth:heartbeat'][0], '100.000')

    def test_redis_errors_never_raise(self):
        client = mock.Mock()
        client.set.side_effect = redis.ConnectionError('down')
        client.mget.side_effect = redis.ConnectionError('down')
        with self.assertLogs('apps.email.worker_heartbeat', level='WARNING'):
            self.assertFalse(record_worker_heartbeat('auth', client=client))
        self.assertEqual(read_worker_heartbeats(client=client), {})

    @override_settings(CELERY_BROKER_URL='')
    def test_no_broker_stores_nothing(self):
        self.assertFalse(record_worker_heartbeat('auth'))

    def test_throttle(self):
        throttle = HeartbeatThrottle('bulk', interval=15)
        with mock.patch('apps.email.worker_heartbeat.record_worker_heartbeat') as record:
            self.assertTrue(throttle.beat(now=0))
            self.assertFalse(throttle.beat(now=10))
            self.assertTrue(throttle.beat(now=16))
        self.assertEqual(record.call_count, 2)

    @override_settings(CELERY_BROKER_URL=BROKER)
    def test_worker_command_beats(self):
        fake = FakeRedis()
        with mock.patch('config.task_lock.get_lock_client', return_value=fake):
            call_command('run_email_worker', '--lane', 'transactional', '--once', stdout=StringIO())
            self.assertIn('transactional', read_worker_heartbeats())
