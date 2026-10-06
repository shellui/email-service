"""Celery tasks for the scheduled jobs, their Redis lock, the beat schedule and settings."""

import os
import subprocess
import sys
import time
from datetime import timedelta
from unittest import mock

import redis
from celery.schedules import crontab
from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings

from apps.actions import tasks as action_tasks
from apps.email import tasks as email_tasks
from config.celery import app as celery_app
from config.settings import _celery_broker_url
from config.task_lock import lock_key, task_lock


class FakeRedis:
    """The two Redis calls the lock uses: SET NX EX and the compare-and-delete script."""

    def __init__(self):
        self.store = {}
        self.set_calls = []

    def _expire(self):
        now = time.monotonic()
        for key in [k for k, (_, exp) in self.store.items() if exp <= now]:
            del self.store[key]

    def set(self, key, value, nx=False, ex=None):
        self.set_calls.append({'key': key, 'nx': nx, 'ex': ex})
        self._expire()
        if nx and key in self.store:
            return None
        self.store[key] = (value, time.monotonic() + (ex or 3600))
        return True

    def eval(self, script, numkeys, key, token):
        self._expire()
        if key in self.store and self.store[key][0] == token:
            del self.store[key]
            return 1
        return 0


class TaskLockTests(SimpleTestCase):
    def test_lock_uses_set_nx_with_ttl_and_service_prefix(self):
        fake = FakeRedis()
        with task_lock('job', ttl=120, client=fake) as acquired:
            self.assertTrue(acquired)
        self.assertEqual(
            fake.set_calls,
            [{'key': 'email-service:scheduler:lock:job', 'nx': True, 'ex': 120}],
        )

    def test_second_holder_is_refused_while_the_first_runs(self):
        fake = FakeRedis()
        with task_lock('job', ttl=60, client=fake) as first:
            with task_lock('job', ttl=60, client=fake) as second:
                self.assertTrue(first)
                self.assertFalse(second)
            # The refused run must not release the lock it never held.
            self.assertIn(lock_key('job'), fake.store)
        self.assertNotIn(lock_key('job'), fake.store)

    def test_released_after_an_exception(self):
        fake = FakeRedis()
        with self.assertRaises(RuntimeError):
            with task_lock('job', ttl=60, client=fake):
                raise RuntimeError('boom')
        with task_lock('job', ttl=60, client=fake) as acquired:
            self.assertTrue(acquired)

    def test_release_keeps_a_lock_taken_by_someone_else(self):
        fake = FakeRedis()
        with task_lock('job', ttl=60, client=fake):
            # Our TTL ran out and another run took the lock.
            fake.store[lock_key('job')] = ('other-token', time.monotonic() + 60)
        self.assertEqual(fake.store[lock_key('job')][0], 'other-token')

    def test_redis_down_on_acquire_fails_the_task(self):
        client = mock.Mock()
        client.set.side_effect = redis.ConnectionError('down')
        with self.assertRaises(redis.ConnectionError):
            with task_lock('job', ttl=60, client=client):
                self.fail('must not run without the lock')

    @override_settings(CELERY_BROKER_URL='redis://broker:6379/2')
    def test_lock_client_uses_the_broker_url(self):
        with mock.patch('config.task_lock._client', None), mock.patch(
            'config.task_lock.redis.Redis.from_url'
        ) as from_url:
            from config.task_lock import get_lock_client

            get_lock_client()
        self.assertEqual(from_url.call_args.args[0], 'redis://broker:6379/2')


class ScheduledTaskTests(TestCase):
    def setUp(self):
        self.fake = FakeRedis()
        patcher = mock.patch('config.task_lock.get_lock_client', return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_each_task_runs_its_command_under_the_lock(self):
        for task, name, ttl in (
            (action_tasks.retry_webhooks, 'retry_webhooks', action_tasks.RETRY_WEBHOOKS_LOCK_TTL),
            (email_tasks.sweep_email_queue, 'sweep_email_queue', email_tasks.SWEEP_EMAIL_QUEUE_LOCK_TTL),
            (action_tasks.purge_expired_data, 'purge_expired_data', action_tasks.PURGE_EXPIRED_DATA_LOCK_TTL),
        ):
            with self.subTest(name=name):
                self.fake.set_calls.clear()
                with self.assertLogs('config.scheduled_tasks', level='INFO') as logs:
                    result = task.apply().get()
                self.assertTrue(result)
                self.assertIn(f'{name}: {result}', logs.output[0])
                self.assertEqual(self.fake.set_calls[0]['key'], lock_key(name))
                self.assertEqual(self.fake.set_calls[0]['ex'], ttl)
                self.assertEqual(self.fake.store, {}, 'lock is released after the run')

    def test_sweep_summary(self):
        self.assertEqual(email_tasks.sweep_email_queue.apply().get(), 'expired=0 released=0 broadcasts=0')

    @mock.patch('config.scheduled_tasks.call_command')
    def test_task_skips_while_another_run_holds_the_lock(self, call_command):
        for task, name in (
            (action_tasks.retry_webhooks, 'retry_webhooks'),
            (email_tasks.sweep_email_queue, 'sweep_email_queue'),
            (action_tasks.purge_expired_data, 'purge_expired_data'),
        ):
            with self.subTest(name=name):
                self.fake.set(lock_key(name), 'held-by-other-worker', ex=60)
                with self.assertLogs('config.scheduled_tasks', level='INFO') as logs:
                    self.assertEqual(task.apply().get(), 'skipped')
                self.assertIn('skipped', logs.output[0])
        call_command.assert_not_called()

    def test_tasks_are_registered_under_the_beat_names(self):
        for entry in settings.CELERY_BEAT_SCHEDULE.values():
            self.assertIn(entry['task'], celery_app.tasks)


class BeatScheduleTests(SimpleTestCase):
    def test_schedule_contents(self):
        schedule = settings.CELERY_BEAT_SCHEDULE
        self.assertEqual(set(schedule), {'retry-webhooks', 'sweep-email-queue', 'purge-expired-data'})

        for key, task in (('retry-webhooks', 'actions.retry_webhooks'), ('sweep-email-queue', 'email.sweep_email_queue')):
            with self.subTest(key=key):
                self.assertEqual(schedule[key]['task'], task)
                self.assertEqual(schedule[key]['schedule'], timedelta(seconds=60))
                self.assertLess(schedule[key]['options']['expires'], 60)

        purge = schedule['purge-expired-data']
        self.assertEqual(purge['task'], 'actions.purge_expired_data')
        self.assertEqual(purge['schedule'], crontab(minute=17))
        self.assertLess(purge['options']['expires'], 3600)

    def test_celery_app_reads_django_settings(self):
        conf = celery_app.conf
        self.assertEqual(conf.beat_schedule, settings.CELERY_BEAT_SCHEDULE)
        self.assertEqual(conf.task_default_queue, 'email-service')
        self.assertEqual(conf.timezone, 'UTC')
        self.assertTrue(conf.task_ignore_result)
        self.assertFalse(conf.worker_hijack_root_logger)
        self.assertEqual(conf.accept_content, ['json'])


class CelerySettingsTests(SimpleTestCase):
    def test_broker_defaults_to_redis_url(self):
        self.assertEqual(_celery_broker_url('redis://redis:6379/0', ''), 'redis://redis:6379/0')

    def test_celery_broker_url_overrides(self):
        self.assertEqual(
            _celery_broker_url('redis://redis:6379/0', ' redis://broker:6379/3 '),
            'redis://broker:6379/3',
        )

    def test_empty_without_redis(self):
        self.assertEqual(_celery_broker_url('', ''), '')

    def test_celery_logs_go_to_stdout_handler(self):
        loggers = settings.LOGGING['loggers']
        for name in ('celery', 'celery.app.trace', 'celery.worker.strategy'):
            with self.subTest(logger=name):
                self.assertEqual(loggers[name]['handlers'], ['console'])

    def test_celery_integration_enabled_with_sentry_dsn(self):
        # Settings run sentry_sdk.init at import, so check in a fresh interpreter.
        code = (
            'import django, sentry_sdk; django.setup(); '
            'print(sorted(sentry_sdk.get_client().integrations))'
        )
        env = dict(os.environ)
        env.update(
            {
                'DJANGO_SETTINGS_MODULE': 'config.settings',
                'SENTRY_DSN': 'https://public@sentry.invalid/1',
                'SECRET_KEY': settings.SECRET_KEY,
                'DEBUG': 'true',
            }
        )
        result = subprocess.run(
            [sys.executable, '-c', code],
            cwd=settings.BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("'celery'", result.stdout)
