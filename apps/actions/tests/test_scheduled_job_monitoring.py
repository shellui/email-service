"""Scheduled job monitoring: run recording, health, metrics, and the staff API."""

import json
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
    ActionRule,
    DeliveryAttempt,
    EventLog,
    ScheduledJobCounter,
    ScheduledJobRun,
    ScheduledJobState,
)
from apps.actions.registry import all_event_types
from apps.actions.scheduled_jobs import (
    PURGE_EXPIRED_DATA,
    RETRY_WEBHOOKS,
    SWEEP_EMAIL_QUEUE,
    compute_health,
    is_overdue,
    jobs_overview,
    sanitize_error_message,
)
from apps.authapi.principal import EmailPrincipal
from apps.authapi.service_auth import ServicePrincipal
from apps.email import tasks as email_tasks
from apps.email.metrics import metrics_http_body
from config.request_context import request_id_var
from config.task_lock import beat_heartbeat_key, lock_key
from config.tests.test_scheduled_tasks import FakeRedis

_SIGN_IN = 'https://id.shellui.com/api/v1/magic-link/verify?token=batch-secret'
_SECRET_ERROR = (
    f'connect failed {_SIGN_IN} '
    'redis://user:hunter2@redis:6379/0 Authorization: Bearer abcdefghijklmnop password=hunter2'
)


def _counter(job, name):
    row = ScheduledJobCounter.objects.filter(job=job, name=name).first()
    return row.value if row else 0


def _principal(*, staff=False, owner=False, company_id=10, global_metrics=False):
    return EmailPrincipal(
        user_id=1,
        company_id=company_id,
        email='ada@acme.com',
        is_staff=staff,
        is_company_owner=owner,
        access_global_metrics=global_metrics,
    )


class FakeRedisWithGet(FakeRedis):
    def get(self, key):
        self._expire()
        item = self.store.get(key)
        return item[0].encode() if item else None

    def ping(self):
        return True


class RunRecordingTests(TestCase):
    def setUp(self):
        self.fake = FakeRedisWithGet()
        patcher = mock.patch('config.task_lock.get_lock_client', return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    @mock.patch('apps.actions.management.commands.retry_webhooks.deliver_due')
    def test_command_path_records_a_run(self, deliver_due):
        deliver_due.return_value = {'processed': 3, 'delivered': 1, 'retried': 1, 'dead': 1}
        out = StringIO()

        call_command('retry_webhooks', stdout=out)

        run = ScheduledJobRun.objects.get()
        self.assertEqual(run.job, 'retry_webhooks')
        self.assertEqual(run.trigger, 'command')
        self.assertEqual(run.status, 'succeeded')
        self.assertIsNotNone(run.finished_at)
        self.assertIsNotNone(run.duration_ms)
        self.assertTrue(run.host)
        self.assertEqual(
            run.counts,
            {
                'webhook_deliveries_attempted': 3,
                'webhook_deliveries_succeeded': 1,
                'webhook_deliveries_failed': 1,
                'webhook_deliveries_given_up': 1,
            },
        )
        self.assertEqual(deliver_due.call_args.kwargs['scheduled_job_run_id'], run.pk)
        self.assertIn(f'run_id={run.pk}', out.getvalue())
        self.assertIn('processed=3', out.getvalue())

        state = ScheduledJobState.objects.get(job='retry_webhooks')
        self.assertEqual(state.last_success_at, run.finished_at)
        self.assertEqual(state.last_started_at, run.started_at)
        self.assertEqual(_counter('retry_webhooks', 'runs.succeeded'), 1)
        self.assertEqual(_counter('retry_webhooks', 'items.webhook_deliveries_attempted'), 3)

        event = EventLog.objects.get(pk=run.event_log_id)
        self.assertIsNone(event.company_id)
        self.assertEqual(event.event_type, 'email.scheduled_job.succeeded')
        self.assertEqual(event.data['run_id'], run.pk)
        self.assertEqual(event.data['trigger'], 'command')
        self.assertNotIn('error_message', event.data)

    def test_sweep_and_purge_record_runs_without_changing_the_summary(self):
        sweep_out = StringIO()
        call_command('sweep_email_queue', stdout=sweep_out)
        self.assertIn('expired=0 released=0 broadcasts=0', sweep_out.getvalue())
        sweep = ScheduledJobRun.objects.get(job='sweep_email_queue')
        self.assertEqual(sweep.status, 'succeeded')
        self.assertEqual(sweep.counts, {'expired': 0, 'released': 0, 'broadcasts': 0})

        summary = email_tasks.sweep_email_queue.apply().get()
        self.assertTrue(summary.startswith('expired=0 released=0 broadcasts=0'))
        self.assertEqual(ScheduledJobRun.objects.filter(job='sweep_email_queue', trigger='celery').count(), 1)

        purge_out = StringIO()
        call_command('purge_expired_data', stdout=purge_out)
        text = purge_out.getvalue()
        self.assertIn('messages=0', text)
        self.assertIn('newsletter_pending=0', text)
        self.assertIn('scheduled_job_runs=0', text)
        self.assertIn('complete=true', text)
        purge = ScheduledJobRun.objects.get(job='purge_expired_data')
        self.assertEqual(purge.counts['events'], 0)
        self.assertIs(purge.counts['complete'], True)

    def test_dry_run_is_not_recorded(self):
        call_command('retry_webhooks', dry_run=True, stdout=StringIO())
        self.assertFalse(ScheduledJobRun.objects.exists())

    @mock.patch('config.scheduled_tasks.call_command')
    def test_skipped_locked_counts_without_a_row(self, call):
        self.fake.set(lock_key('retry_webhooks'), 'other-worker', ex=60)
        self.assertEqual(tasks.retry_webhooks.apply().get(), 'skipped')
        call.assert_not_called()
        self.assertFalse(ScheduledJobRun.objects.filter(job='retry_webhooks').exists())
        self.assertEqual(_counter('retry_webhooks', 'runs.skipped_locked'), 1)
        self.assertIsNotNone(ScheduledJobState.objects.get(job='retry_webhooks').last_skipped_at)

    @mock.patch('apps.actions.management.commands.retry_webhooks.deliver_due')
    def test_failure_is_recorded_logged_and_sanitized(self, deliver_due):
        deliver_due.side_effect = OperationalError(_SECRET_ERROR)
        before = request_id_var.get()

        with self.assertLogs('apps.actions.scheduled_jobs', level='ERROR') as logs:
            with self.assertRaises(CommandError) as ctx:
                call_command('retry_webhooks', stdout=StringIO())

        run = ScheduledJobRun.objects.get()
        self.assertEqual(run.status, 'failed')
        self.assertEqual(run.error_key, 'database_error')
        self.assertEqual(run.error_class, 'OperationalError')
        for secret in ('batch-secret', 'magic-link/verify', 'hunter2', 'abcdefghijklmnop', '?token'):
            self.assertNotIn(secret, run.error_message)
            self.assertNotIn(secret, str(ctx.exception))
            self.assertNotIn(secret, ''.join(logs.output))
        self.assertIn('[Filtered]', run.error_message)
        self.assertIn(f'run_id={run.pk}', logs.output[0])
        self.assertIn(f'run_id={run.pk}', str(ctx.exception))
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
        self.assertNotIn('batch-secret', str(event.data))

    @mock.patch('apps.actions.management.commands.retry_webhooks.deliver_due')
    def test_celery_failure_returns_failed_without_raising(self, deliver_due):
        deliver_due.side_effect = RuntimeError('boom')
        with self.assertLogs('apps.actions.scheduled_jobs', level='ERROR'):
            self.assertEqual(tasks.retry_webhooks.apply().get(), 'failed')
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

    @mock.patch('apps.actions.management.commands.retry_webhooks.deliver_due')
    def test_stale_running_row_is_marked_interrupted(self, deliver_due):
        deliver_due.return_value = {'processed': 0, 'delivered': 0, 'retried': 0, 'dead': 0}
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
        cleaned = sanitize_error_message(
            _SECRET_ERROR + ' eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.c2lnbmF0dXJl ' + 'a' * 40
            + ' https://email.example/u/unsubscribe-token https://email.example/n/confirm/confirm-token'
        )
        for secret in (
            'batch-secret',
            'hunter2',
            'abcdefghijklmnop',
            'eyJhbGci',
            'a' * 40,
            'unsubscribe-token',
            'confirm-token',
            'magic-link/verify',
        ):
            self.assertNotIn(secret, cleaned)
        self.assertIn('redis://redis:6379/0', cleaned)
        self.assertLessEqual(len(sanitize_error_message('x' * 1000)), 300)
        self.assertEqual(sanitize_error_message('line one\nline two'), 'line one line two')


class HealthTests(TestCase):
    def _state(self, job, **fields):
        state, _created = ScheduledJobState.objects.get_or_create(job=job)
        for key, value in fields.items():
            setattr(state, key, value)
        state.save()
        return state

    def test_overdue_after_three_intervals(self):
        now = timezone.now()
        retry = self._state('retry_webhooks', last_success_at=now - timedelta(minutes=2))
        self.assertFalse(is_overdue(RETRY_WEBHOOKS, retry, now))
        retry.last_success_at = now - timedelta(minutes=3, seconds=1)
        self.assertTrue(is_overdue(RETRY_WEBHOOKS, retry, now))

        sweep = self._state('sweep_email_queue', last_success_at=now - timedelta(seconds=179))
        self.assertFalse(is_overdue(SWEEP_EMAIL_QUEUE, sweep, now))
        sweep.last_success_at = now - timedelta(seconds=181)
        self.assertTrue(is_overdue(SWEEP_EMAIL_QUEUE, sweep, now))

        purge = self._state('purge_expired_data', last_success_at=now - timedelta(hours=2))
        self.assertFalse(is_overdue(PURGE_EXPIRED_DATA, purge, now))
        purge.last_success_at = now - timedelta(hours=2, minutes=16)
        self.assertTrue(is_overdue(PURGE_EXPIRED_DATA, purge, now))

    def test_before_the_first_success_the_clock_starts_at_state_creation(self):
        now = timezone.now()
        state = self._state('purge_expired_data', created_at=now - timedelta(minutes=30), last_success_at=None)
        self.assertFalse(is_overdue(PURGE_EXPIRED_DATA, state, now))
        state.created_at = now - timedelta(hours=3)
        self.assertTrue(is_overdue(PURGE_EXPIRED_DATA, state, now))

    def test_health_values(self):
        now = timezone.now()
        state = self._state('retry_webhooks', created_at=now - timedelta(hours=1))
        self.assertEqual(compute_health(RETRY_WEBHOOKS, state, None, now, enabled=False), 'disabled')
        self.assertEqual(compute_health(RETRY_WEBHOOKS, state, None, now, enabled=True), 'overdue')
        state.last_started_at = now
        state.last_success_at = now - timedelta(seconds=30)
        ok = ScheduledJobRun(job='retry_webhooks', trigger='command', status='succeeded')
        failed = ScheduledJobRun(job='retry_webhooks', trigger='command', status='failed')
        self.assertEqual(compute_health(RETRY_WEBHOOKS, state, ok, now, enabled=True), 'healthy')
        self.assertEqual(compute_health(RETRY_WEBHOOKS, state, ok, now, enabled=False), 'healthy')
        self.assertEqual(compute_health(RETRY_WEBHOOKS, state, failed, now, enabled=True), 'failing')

    def test_next_run_of_the_hourly_job_is_minute_17(self):
        moment = timezone.now().replace(hour=10, minute=20, second=5, microsecond=0)
        self.assertEqual(PURGE_EXPIRED_DATA.next_after(moment), moment.replace(hour=11, minute=17, second=0))
        self.assertEqual(
            PURGE_EXPIRED_DATA.next_after(moment.replace(minute=5)),
            moment.replace(minute=17, second=0),
        )
        self.assertEqual(RETRY_WEBHOOKS.next_after(moment), moment + timedelta(seconds=60))
        self.assertEqual(SWEEP_EMAIL_QUEUE.next_after(moment), moment + timedelta(seconds=60))

    @override_settings(CELERY_BROKER_URL='redis://broker:6379/0', SCHEDULER_ENABLED=True)
    def test_overview_reports_redis_and_beat(self):
        fake = FakeRedisWithGet()
        with mock.patch('config.task_lock.get_lock_client', return_value=fake):
            stale = jobs_overview()
            from config.celery import _beat_heartbeat

            _beat_heartbeat(sender='actions.retry_webhooks')
            _beat_heartbeat(sender='some.other.task')
            fresh = jobs_overview()
        self.assertTrue(stale['redis_reachable'])
        self.assertTrue(stale['beat_stale'])
        self.assertIsNone(stale['beat_last_seen_at'])
        self.assertIn(beat_heartbeat_key(), fake.store)
        self.assertFalse(fresh['beat_stale'])
        self.assertIsNotNone(fresh['beat_last_seen_at'])

    @override_settings(CELERY_BROKER_URL='redis://broker:6379/0')
    def test_overview_reports_redis_unreachable(self):
        client = mock.Mock()
        client.ping.side_effect = redis.ConnectionError('down')
        client.get.side_effect = redis.ConnectionError('down')
        with mock.patch('config.task_lock.get_lock_client', return_value=client):
            data = jobs_overview()
        self.assertFalse(data['redis_reachable'])
        self.assertIsNone(data['beat_last_seen_at'])

    @override_settings(CELERY_BROKER_URL='')
    def test_overview_without_broker(self):
        data = jobs_overview()
        self.assertIsNone(data['redis_reachable'])
        self.assertEqual(
            [job['job'] for job in data['jobs']],
            ['retry_webhooks', 'sweep_email_queue', 'purge_expired_data'],
        )


class RetentionTests(TestCase):
    def test_purge_deletes_runs_older_than_seven_days(self):
        now = timezone.now()
        old = ScheduledJobRun.objects.create(job='retry_webhooks', trigger='celery', status='succeeded')
        ScheduledJobRun.objects.filter(pk=old.pk).update(started_at=now - timedelta(days=8))
        kept = ScheduledJobRun.objects.create(job='retry_webhooks', trigger='celery', status='succeeded')
        ScheduledJobRun.objects.filter(pk=kept.pk).update(started_at=now - timedelta(days=6))

        call_command('purge_expired_data', stdout=StringIO())

        remaining = set(ScheduledJobRun.objects.values_list('pk', flat=True))
        self.assertIn(kept.pk, remaining)
        self.assertNotIn(old.pk, remaining)
        current = ScheduledJobRun.objects.get(job='purge_expired_data', status='succeeded')
        self.assertEqual(current.counts['scheduled_job_runs'], 1)

    def test_platform_events_expire_with_event_log_retention(self):
        now = timezone.now()
        event = EventLog.objects.create(
            company_id=None,
            event_type='email.scheduled_job.succeeded',
            data={'run_id': 1},
        )
        EventLog.objects.filter(pk=event.pk).update(created_at=now - timedelta(days=8))
        kept = EventLog.objects.create(company_id=None, event_type='email.scheduled_job.failed', data={'run_id': 2})
        call_command('purge_expired_data', stdout=StringIO())
        self.assertFalse(EventLog.objects.filter(pk=event.pk).exists())
        self.assertTrue(EventLog.objects.filter(pk=kept.pk).exists())


class StaffApiTests(TestCase):
    def setUp(self):
        self.run = ScheduledJobRun.objects.create(
            job='retry_webhooks',
            trigger='celery',
            status='failed',
            finished_at=timezone.now(),
            duration_ms=120,
            counts={'webhook_deliveries_attempted': 1},
            error_key='network_error',
            error_class='ConnectionError',
            error_message='connect failed',
        )

    def _client(self, **kwargs):
        client = APIClient()
        client.force_authenticate(user=_principal(**kwargs))
        return client

    def test_permissions(self):
        paths = (
            '/api/v1/scheduled-jobs',
            '/api/v1/scheduled-jobs/retry_webhooks/runs',
            f'/api/v1/scheduled-jobs/runs/{self.run.pk}',
        )
        anonymous = APIClient()
        owner = self._client(owner=True)
        staff = self._client(staff=True)
        global_metrics = self._client(owner=True, global_metrics=True)
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(staff.get(path).status_code, 200)
                denied = owner.get(path)
                self.assertEqual(denied.status_code, 403)
                self.assertEqual(denied.json()['error_code'], 'forbidden')
                self.assertEqual(global_metrics.get(path).status_code, 403)
                missing = anonymous.get(path)
                self.assertEqual(missing.status_code, 401)
                self.assertEqual(missing.json()['error_code'], 'unauthorized')

    def test_service_keys_are_forbidden(self):
        client = APIClient()
        client.force_authenticate(
            user=ServicePrincipal(
                service='identity',
                client_id=1,
                allowed_lanes=('auth',),
                allowed_template_prefixes=('identity.',),
                allowed_company_ids=None,
                key_prefix='esk_test',
            )
        )
        response = client.get('/api/v1/scheduled-jobs')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error_code'], 'forbidden')

    def test_overview_payload_uses_keys_only(self):
        data = self._client(staff=True).get('/api/v1/scheduled-jobs').json()
        self.assertEqual(
            set(data),
            {
                'generated_at',
                'scheduler_enabled',
                'redis_reachable',
                'beat_last_seen_at',
                'beat_stale',
                'jobs',
            },
        )
        self.assertEqual(
            [job['job'] for job in data['jobs']],
            ['retry_webhooks', 'sweep_email_queue', 'purge_expired_data'],
        )
        retry = next(job for job in data['jobs'] if job['job'] == 'retry_webhooks')
        self.assertEqual(retry['health'], 'failing')
        self.assertEqual(retry['last_run']['id'], self.run.pk)
        self.assertEqual(retry['last_run']['error_key'], 'network_error')
        self.assertEqual(retry['last_24h'], {'succeeded': 0, 'failed': 1})
        self.assertEqual(retry['interval_seconds'], 60)
        self.assertEqual(retry['overdue_after_seconds'], 180)
        self.assertIn('next_expected_at', retry)

    def test_runs_list_limit_and_unknown_job(self):
        for _ in range(3):
            ScheduledJobRun.objects.create(job='retry_webhooks', trigger='command', status='succeeded')
        staff = self._client(staff=True)
        data = staff.get('/api/v1/scheduled-jobs/retry_webhooks/runs?limit=2').json()
        self.assertEqual(len(data['results']), 2)
        failed = staff.get('/api/v1/scheduled-jobs/retry_webhooks/runs?status=failed').json()
        self.assertEqual([row['id'] for row in failed['results']], [self.run.pk])
        missing = staff.get('/api/v1/scheduled-jobs/nope/runs')
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()['error_code'], 'not_found')
        invalid = staff.get('/api/v1/scheduled-jobs/retry_webhooks/runs?limit=x')
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()['error_code'], 'validation_failed')
        self.assertEqual(invalid.json()['field_errors']['limit'], ['invalid'])

    def test_run_detail_lists_webhook_attempts_and_empty_email_events(self):
        rule = ActionRule.objects.create(
            company_id=10,
            name='hook',
            event_type='email.message.sent',
            config={'url': 'https://example.com/hook', 'secret': 's'},
        )
        outbox = ActionOutbox.objects.create(
            company_id=10,
            action_rule=rule,
            event_type='email.message.sent',
            envelope={'type': 'email.message.sent'},
            webhook_id='msgwh_test_run',
            target_url='https://example.com/hook',
        )
        DeliveryAttempt.objects.create(
            outbox=outbox,
            status='success',
            attempt_number=2,
            trigger='automatic_retry',
            scheduled_job_run_id=self.run.pk,
        )
        DeliveryAttempt.objects.create(outbox=outbox, status='failure', attempt_number=1, trigger='dispatch')
        staff = self._client(staff=True)
        data = staff.get(f'/api/v1/scheduled-jobs/runs/{self.run.pk}').json()
        self.assertEqual(len(data['webhook_delivery_attempts']), 1)
        self.assertEqual(data['webhook_delivery_attempts'][0]['delivery_id'], str(outbox.pk))
        self.assertEqual(data['webhook_delivery_attempts'][0]['company_id'], 10)
        self.assertEqual(data['webhook_delivery_attempts'][0]['trigger'], 'automatic_retry')
        self.assertEqual(data['email_events'], [])
        self.assertFalse(data['email_events_truncated'])
        self.assertFalse(data['webhook_delivery_attempts_truncated'])
        self.assertNotIn('batch-secret', str(data))

        staff_view = staff.get(f'/api/v1/actions/deliveries/{outbox.pk}').json()
        retry_attempt = next(item for item in staff_view['attempts'] if item['attempt_number'] == 2)
        self.assertEqual(retry_attempt['scheduled_job_run_id'], self.run.pk)
        self.assertEqual(retry_attempt['trigger'], 'automatic_retry')
        owner_view = self._client(owner=True).get(f'/api/v1/actions/deliveries/{outbox.pk}').json()
        for attempt in owner_view['attempts']:
            self.assertNotIn('scheduled_job_run_id', attempt)
        self.assertEqual({item['trigger'] for item in owner_view['attempts']}, {'automatic_retry', 'dispatch'})

        listed = staff.get(f'/api/v1/actions/deliveries?scheduled_job_run_id={self.run.pk}&company_id=10').json()
        self.assertEqual([row['id'] for row in listed['deliveries']], [str(outbox.pk)])
        denied = self._client(owner=True).get(
            f'/api/v1/actions/deliveries?scheduled_job_run_id={self.run.pk}&company_id=10'
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()['error_code'], 'forbidden')

    def test_platform_events_are_staff_only(self):
        EventLog.objects.create(company_id=None, event_type='email.scheduled_job.failed', data={'run_id': 1})
        EventLog.objects.create(company_id=10, event_type='email.message.sent', data={})

        staff = self._client(staff=True)
        company_log = staff.get('/api/v1/actions/event-log?company_id=10').json()
        self.assertEqual([row['event_type'] for row in company_log['events']], ['email.message.sent'])
        platform = staff.get('/api/v1/actions/event-log?scope=platform').json()
        self.assertEqual([row['event_type'] for row in platform['events']], ['email.scheduled_job.failed'])
        denied = self._client(owner=True).get('/api/v1/actions/event-log?scope=platform')
        self.assertEqual(denied.status_code, 403)
        invalid = staff.get('/api/v1/actions/event-log?scope=nope')
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()['field_errors']['scope'], ['invalid'])

        types = set(self._client(owner=True).get('/api/v1/actions/event-log/types').json()['types'])
        self.assertNotIn('email.scheduled_job.succeeded', types)
        self.assertNotIn('email.scheduled_job.failed', types)
        self.assertNotIn('email.scheduled_job.succeeded', {event.id for event in all_event_types()})


class MetricsTests(TestCase):
    def _client(self, **kwargs):
        client = APIClient()
        client.force_authenticate(user=_principal(**kwargs))
        return client

    def test_global_metrics_include_scheduled_jobs(self):
        call_command('retry_webhooks', stdout=StringIO())
        body = self._client(staff=True).get('/api/v1/metrics/all').content.decode()
        self.assertIn('shellui_email_scheduled_job_runs_total{job="retry_webhooks",status="succeeded"} 1.0', body)
        self.assertIn('shellui_email_scheduled_job_runs_total{job="sweep_email_queue",status="failed"} 0.0', body)
        self.assertIn('shellui_email_scheduled_job_runs_total{job="purge_expired_data",status="failed"} 0.0', body)
        self.assertIn(
            'shellui_email_scheduled_job_items_total{job="retry_webhooks",kind="webhook_deliveries_attempted"} 0.0',
            body,
        )
        self.assertIn('shellui_email_scheduled_job_overdue{job="retry_webhooks"} 0.0', body)
        self.assertIn('shellui_email_scheduled_job_last_success_timestamp_seconds{job="retry_webhooks"}', body)
        self.assertIn('shellui_email_scheduled_job_last_run_duration_seconds{job="retry_webhooks"}', body)
        self.assertIn('shellui_email_scheduler_enabled', body)
        self.assertIn('shellui_email_queue_depth', body)

        owner = self._client(owner=True).get('/api/v1/metrics/all')
        self.assertEqual(owner.status_code, 403)
        self.assertEqual(json.loads(owner.content)['error_code'], 'forbidden')
        granted = self._client(owner=True, global_metrics=True).get('/api/v1/metrics/all')
        self.assertEqual(granted.status_code, 200)
        self.assertIn(b'shellui_email_scheduler_enabled', granted.content)
        missing = APIClient().get('/api/v1/metrics/all')
        self.assertEqual(missing.status_code, 401)

    def test_company_metrics_never_include_scheduled_jobs(self):
        ScheduledJobCounter.objects.create(job='retry_webhooks', name='runs.succeeded', value=4)
        body = metrics_http_body(company_id=10)
        self.assertNotIn(b'scheduled_job', body)
        self.assertNotIn(b'shellui_email_scheduler', body)

        response = self._client(owner=True).get('/api/v1/metrics?company_id=10')
        self.assertEqual(response.status_code, 200)
        text = response.content.decode()
        self.assertNotIn('shellui_email_scheduled_job', text)
        self.assertNotIn('shellui_email_scheduler', text)
        self.assertIn('shellui_email_queue_depth', text)

        staff_company = self._client(staff=True).get('/api/v1/metrics')
        self.assertEqual(staff_company.status_code, 200)
        self.assertNotIn('shellui_email_scheduled_job', staff_company.content.decode())
