"""OpenTelemetry: one request span, and the same trace from an event through the send."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from rest_framework.test import APIClient
from shellui_django.telemetry import _reset_for_tests, setup_telemetry

from apps.email.crypto import encrypt_json
from apps.email.keys import issue_service_key
from apps.email.metrics import QUEUE_DEPTH, register_observable_metrics
from apps.email.models import CompanyProvider, Message
from apps.email.tests.helpers import library_content
from apps.providers.registry import fake_provider

ROOT = Path(__file__).resolve().parents[3]


def _enable():
    span_exporter = InMemorySpanExporter()
    metric_reader = InMemoryMetricReader()
    assert setup_telemetry(
        'email-service',
        service_version='test',
        environment='test',
        region='eu',
        span_exporter=span_exporter,
        metric_reader=metric_reader,
    )
    register_observable_metrics()
    return span_exporter, metric_reader


@override_settings(
    EMAIL_DELIVER_SYNC=False,
    EMAIL_FALLBACK_PROVIDER='fake',
    EMAIL_ALLOW_FAKE_PROVIDER=True,
    DEBUG=True,
)
class TelemetryTests(TestCase):
    def setUp(self):
        fake_provider().sent.clear()
        fake_provider().fail_code = ''
        fake_provider().raise_on_send = None
        self.span_exporter, self.metric_reader = _enable()
        self.addCleanup(_reset_for_tests)

    def test_log_formatter_keeps_request_id_and_trace_ids(self):
        formatter = settings.LOGGING['formatters']['console']
        self.assertEqual(formatter['()'], 'shellui_django.telemetry.TraceContextFormatter')
        self.assertIn('request_id', formatter['format'])
        self.assertIn('trace_id', formatter['format'])
        self.assertIn('span_id', formatter['format'])

    def test_request_produces_a_span(self):
        response = Client().get('/api/v1/catalog')
        self.assertEqual(response.status_code, 401)
        spans = self.span_exporter.get_finished_spans()
        self.assertTrue(spans)
        self.assertTrue(any(span.context.trace_id for span in spans))
        for path in ('/api/v1/metrics', '/api/v1/metrics/all'):
            self.assertEqual(Client().get(path).status_code, 404)

    def test_queue_depth_is_exported(self):
        data = self.metric_reader.get_metrics_data()
        names = []
        lanes = set()
        for resource in data.resource_metrics:
            self.assertEqual(resource.resource.attributes['service.name'], 'email-service')
            for scope in resource.scope_metrics:
                for metric in scope.metrics:
                    names.append(metric.name)
                    if metric.name == QUEUE_DEPTH:
                        lanes.update(point.attributes['lane'] for point in metric.data.data_points)
        self.assertIn(QUEUE_DEPTH, names)
        self.assertEqual(lanes, {'auth', 'transactional', 'bulk'})

    def test_event_and_provider_send_share_a_trace_without_recipient_data(self):
        raw, _client = issue_service_key(
            service='hosting',
            allowed_lanes=['transactional'],
            allowed_template_prefixes=['hosting.'],
        )
        CompanyProvider.objects.create(
            company_id=7,
            provider='fake',
            from_email='ops@acme.com',
            from_name='Acme',
            credentials_ciphertext=encrypt_json({'marker': 'company'}, setting='EMAIL_CREDENTIALS_KEY'),
            credentials_hint='hint',
            configured=True,
        )
        from apps.email.rules import create_rule

        create_rule(7, {'event_type': 'hosting.deployment.failed', 'content': library_content()})
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {raw}')
        response = client.post(
            '/api/v1/events',
            {
                'company_id': 7,
                'service': 'hosting',
                'event_type': 'hosting.deployment.failed',
                'payload': {'display_name': 'Docs', 'error': 'artifact_extract_failed'},
                'recipients': [{'email': 'ops@acme.com', 'user_id': 42}],
            },
            format='json',
        )
        self.assertEqual(response.status_code, 202, response.content)
        message = Message.objects.get(company_id=7)
        self.assertTrue(message.traceparent)
        self.assertNotIn('ops@acme.com', message.traceparent)
        trace_id = message.traceparent.split('-')[1]
        self.span_exporter.clear()
        from apps.email.service import deliver_message

        deliver_message(message.pk)
        spans = list(self.span_exporter.get_finished_spans())
        send = next(span for span in spans if span.name == 'email.send')
        provider = next(span for span in spans if span.name == 'email.provider.send')
        self.assertEqual(format(send.context.trace_id, '032x'), trace_id)
        self.assertEqual(provider.context.trace_id, send.context.trace_id)
        self.assertEqual(provider.parent.span_id, send.context.span_id)
        attributes = dict(send.attributes)
        self.assertEqual(attributes['email.lane'], 'transactional')
        self.assertEqual(attributes['email.template_id'], message.template_key)
        self.assertEqual(attributes['email.provider'], 'fake')
        self.assertEqual(attributes['email.status'], 'sent')
        self.assertEqual(attributes['shellui.company_id'], '7')
        self.assertEqual(attributes['shellui.user_id'], '42')
        blob = ' '.join(str(dict(span.attributes or {})) for span in spans)
        self.assertNotIn('ops@acme.com', blob)
        self.assertNotIn('Docs', blob)
        self.assertNotIn('artifact_extract_failed', blob)

    def test_entry_points_configure_telemetry(self):
        for relative in (
            'config/wsgi.py',
            'config/asgi.py',
            'config/celery.py',
            'apps/email/management/commands/run_email_worker.py',
        ):
            self.assertIn('configure_telemetry', (ROOT / relative).read_text(encoding='utf-8'))

    @mock.patch('config.telemetry.configure_telemetry', return_value=False)
    def test_lane_worker_configures_telemetry(self, configure):
        call_command('run_email_worker', '--lane', 'auth', '--once')
        configure.assert_called_once()
