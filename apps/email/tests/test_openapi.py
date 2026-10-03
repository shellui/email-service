"""OpenAPI schema generation must describe every APIView without spectacular warnings."""

from __future__ import annotations

import tempfile

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase


class OpenApiSchemaTests(SimpleTestCase):
    def test_spectacular_validate_fail_on_warn(self):
        with tempfile.NamedTemporaryFile(suffix='.yml') as handle:
            try:
                call_command(
                    'spectacular',
                    validate=True,
                    fail_on_warn=True,
                    file=handle.name,
                    stdout=handle,
                )
            except CommandError as exc:
                self.fail(str(exc))

        from drf_spectacular.generators import SchemaGenerator

        schema = SchemaGenerator().get_schema(request=None, public=True)
        paths = schema['paths']
        for path in (
            '/api/v1/messages',
            '/api/v1/messages/{message_id}',
            '/api/v1/templates',
            '/api/v1/templates/{template_id}',
            '/api/v1/templates/{template_id}/versions',
            '/api/v1/templates/{template_id}/versions/{number}',
            '/api/v1/rules',
            '/api/v1/actions/deliveries',
            '/api/v1/actions/deliveries/{delivery_id}',
            '/api/v1/actions/event-log',
            '/api/v1/actions/event-log/{id}',
            '/api/v1/actions/rules',
            '/api/v1/actions/rules/{id}',
        ):
            self.assertIn(path, paths)
            operation = paths[path]['get']
            content = operation['responses']['200']['content']['application/json']['schema']
            self.assertTrue(content.get('properties') or content.get('$ref'), content)

        operation_ids = [
            operation['operationId']
            for path_item in paths.values()
            for operation in path_item.values()
            if isinstance(operation, dict) and 'operationId' in operation
        ]
        self.assertEqual(len(operation_ids), len(set(operation_ids)), operation_ids)
        self.assertIn('api_v1_messages_list', operation_ids)
        self.assertIn('api_v1_messages_retrieve', operation_ids)
        self.assertIn('api_v1_templates_list', operation_ids)
        self.assertIn('api_v1_templates_retrieve', operation_ids)
        self.assertIn('api_v1_template_versions_list', operation_ids)
        self.assertIn('api_v1_template_versions_retrieve', operation_ids)
        self.assertIn('api_v1_actions_deliveries_list', operation_ids)
        self.assertIn('api_v1_actions_deliveries_retrieve', operation_ids)
        self.assertIn('api_v1_actions_event_log_list', operation_ids)
        self.assertIn('api_v1_actions_event_log_retrieve', operation_ids)
        self.assertIn('api_v1_actions_rules_list', operation_ids)
        self.assertIn('api_v1_actions_rules_retrieve', operation_ids)
