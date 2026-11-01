"""OpenAPI schema generation must describe every APIView without spectacular warnings."""

from __future__ import annotations

import tempfile

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from apps.authapi.mcp_openapi import MCP_EXCLUDED_PATH_PREFIXES, validate_mcp_schema


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
            '/api/v1/templates/{template_id}/render-preview',
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
            operation = next(
                op
                for method, op in paths[path].items()
                if method in {'get', 'post', 'put', 'patch', 'delete'} and isinstance(op, dict)
            )
            content = operation['responses']['200']['content']['application/json']['schema']
            self.assertTrue(content.get('properties') or content.get('$ref'), content)

        operation_ids = [
            operation['operationId']
            for path_item in paths.values()
            for operation in path_item.values()
            if isinstance(operation, dict) and 'operationId' in operation
        ]
        self.assertEqual(len(operation_ids), len(set(operation_ids)), operation_ids)
        self.assertIn('api_v1_templates_render_preview', operation_ids)
        validate_mcp_schema(schema)

        for prefix in MCP_EXCLUDED_PATH_PREFIXES:
            for path in paths:
                self.assertFalse(path.startswith(prefix), path)

    def test_validate_mcp_schema_rejects_forbidden_tags(self):
        schema = {
            'paths': {
                '/api/v1/templates': {
                    'get': {
                        'operationId': 'api_v1_service_clients_list',
                        'extensions': {'x-mcp': {'read_only': True}},
                    }
                }
            }
        }
        with self.assertRaises(AssertionError):
            validate_mcp_schema(schema)
