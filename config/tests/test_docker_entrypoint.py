"""
tools/docker-entrypoint.sh modes and process supervision.

The real gunicorn, celery and python are replaced by stubs on PATH that log their
arguments, so the script runs without Docker or Redis.
"""

import os
import shutil
import signal
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ENTRYPOINT = Path(settings.BASE_DIR) / 'tools' / 'docker-entrypoint.sh'
LANE_WORKERS = [f'python manage.py run_email_worker --lane {lane}' for lane in ('auth', 'transactional', 'bulk')]


def _bash_has_wait_n() -> bool:
    """The supervisor uses `wait -n` (bash 4.3+). macOS ships bash 3.2."""
    bash = shutil.which('bash')
    if not bash:
        return False
    result = subprocess.run(
        [bash, '-c', 'sleep 0 & wait -n'], capture_output=True, text=True, timeout=10
    )
    return result.returncode == 0


# Logs its arguments. Long-running unless it is a one-shot python command or
# STUB_<NAME>_EXIT asks it to exit on its own with that status.
_STUB = textwrap.dedent(
    '''\
    #!/usr/bin/env bash
    name="$(basename "$0")"
    echo "${name} $*" >> "${STUB_LOG}"
    if [ "${name}" = "python" ] && [ "${2:-}" != "run_email_worker" ]; then
      exit 0
    fi
    var="STUB_$(echo "${name}" | tr '[:lower:]' '[:upper:]')_EXIT"
    if [ -n "${!var:-}" ]; then
      sleep 0.2
      exit "${!var}"
    fi
    # The sleep must not keep the test's stdout/stderr pipes open after we exit.
    sleep 30 >/dev/null 2>&1 &
    sleeper=$!
    trap 'echo "${name} TERM" >> "${STUB_LOG}"; kill "${sleeper}"; exit 0' TERM
    wait
    '''
)


@unittest.skipUnless(_bash_has_wait_n(), 'bash 4.3 or later is required')
class DockerEntrypointTests(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='entrypoint-test-'))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        bin_dir = self.tmp / 'bin'
        bin_dir.mkdir()
        for name in ('gunicorn', 'celery', 'python'):
            path = bin_dir / name
            path.write_text(_STUB)
            path.chmod(0o755)
        self.log = self.tmp / 'calls.log'
        self.log.touch()
        self.base_env = {
            'PATH': f'{bin_dir}{os.pathsep}{os.environ.get("PATH", "")}',
            'STUB_LOG': str(self.log),
            'SQLITE_PATH': str(self.tmp / 'data' / 'db.sqlite3'),
            'HOME': str(self.tmp),
        }

    def _env(self, **extra):
        env = dict(self.base_env)
        env.update(extra)
        return env

    def run_entrypoint(self, *args, timeout=15, **env):
        return subprocess.run(
            ['bash', str(ENTRYPOINT), *args],
            env=self._env(**env),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def calls(self):
        return self.log.read_text().splitlines()

    def started(self, name):
        return [line for line in self.calls() if line.startswith(f'{name} ') and line != f'{name} TERM']

    def lane_workers(self):
        return [line for line in self.started('python') if 'run_email_worker' in line]

    def wait_for(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def test_web_starts_everything_and_forwards_sigterm(self):
        proc = subprocess.Popen(
            ['bash', str(ENTRYPOINT)],
            env=self._env(REDIS_URL='redis://redis:6379/0'),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertTrue(
                self.wait_for(
                    lambda: self.started('gunicorn') and self.started('celery') and len(self.lane_workers()) == 3
                ),
                self.calls(),
            )
            # Let the stubs install their TERM traps.
            time.sleep(0.3)
            proc.send_signal(signal.SIGTERM)
            _, stderr = proc.communicate(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()

        self.assertEqual(proc.returncode, 0, stderr)
        calls = self.calls()
        self.assertEqual(calls[0], 'python manage.py migrate --noinput')
        self.assertEqual(sorted(self.lane_workers()), sorted(LANE_WORKERS))
        self.assertIn('gunicorn TERM', calls)
        self.assertIn('celery TERM', calls)
        self.assertEqual(calls.count('python TERM'), 3)
        celery_args = self.started('celery')[0]
        self.assertIn('-A config --quiet worker --beat', celery_args)
        self.assertIn('--schedule /tmp/celerybeat-schedule', celery_args)
        self.assertIn('--pool threads --concurrency 2', celery_args)
        self.assertIn('config.wsgi:application', self.started('gunicorn')[0])

    def test_web_exits_when_the_scheduler_dies(self):
        result = self.run_entrypoint(REDIS_URL='redis://redis:6379/0', STUB_CELERY_EXIT='3')
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn('gunicorn TERM', self.calls())
        self.assertIn('a process exited with status 3', result.stderr)

    def test_web_exits_when_a_lane_worker_dies(self):
        result = self.run_entrypoint(SCHEDULER_ENABLED='false', STUB_PYTHON_EXIT='2')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('gunicorn TERM', self.calls())

    def test_web_exits_non_zero_when_gunicorn_exits(self):
        result = self.run_entrypoint(REDIS_URL='redis://redis:6379/0', STUB_GUNICORN_EXIT='0')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('celery TERM', self.calls())
        self.assertEqual(self.calls().count('python TERM'), 3)

    def test_web_without_redis_warns_and_skips_the_scheduler(self):
        result = self.run_entrypoint('web', STUB_GUNICORN_EXIT='0')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('WARNING: REDIS_URL is not set', result.stderr)
        self.assertFalse(self.started('celery'))
        self.assertEqual(len(self.lane_workers()), 3)

    def test_scheduler_and_workers_can_be_turned_off(self):
        result = self.run_entrypoint(
            REDIS_URL='redis://redis:6379/0',
            SCHEDULER_ENABLED='false',
            EMAIL_WORKERS_ENABLED='false',
            STUB_GUNICORN_EXIT='0',
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('SCHEDULER_ENABLED=false', result.stderr)
        self.assertIn('EMAIL_WORKERS_ENABLED=false', result.stderr)
        self.assertFalse(self.started('celery'))
        self.assertEqual(self.lane_workers(), [])

    def test_celery_broker_url_alone_starts_the_scheduler(self):
        result = self.run_entrypoint(
            CELERY_BROKER_URL='redis://broker:6379/1',
            CELERY_WORKER_CONCURRENCY='4',
            EMAIL_WORKERS_ENABLED='false',
            STUB_CELERY_EXIT='0',
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('--concurrency 4', self.started('celery')[0])

    def test_worker_mode_runs_scheduler_and_lane_workers_without_web(self):
        result = self.run_entrypoint('worker', REDIS_URL='redis://redis:6379/0', STUB_CELERY_EXIT='0')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue(self.started('celery'))
        self.assertEqual(len(self.lane_workers()), 3)
        self.assertFalse(self.started('gunicorn'))
        self.assertNotIn('python manage.py migrate --noinput', self.calls())

    def test_worker_mode_with_nothing_to_run_fails(self):
        result = self.run_entrypoint('worker', EMAIL_WORKERS_ENABLED='false')
        self.assertEqual(result.returncode, 1)
        self.assertIn('worker mode has nothing to run', result.stderr)

    def test_other_command_runs_as_given(self):
        result = self.run_entrypoint('python', 'manage.py', 'purge_expired_data')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.started('python'), ['python manage.py purge_expired_data'])
        self.assertFalse(self.started('gunicorn'))
