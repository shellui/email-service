"""Pin the MCP tool catalog an agent sees on ``tools/list``.

Renaming a tool, dropping one, or changing its input schema, enums, required
fields, or risk annotations fails this test. Access flags are pinned beside
the client payload because approval routing reads ``readOnlyHint`` and
``destructiveHint``, not the REST method.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from django.test import TestCase, override_settings

from apps.authapi.mcp_openapi import MCP_REQUIRED_OPERATION_IDS, MCP_TOOL_NAMES
from apps.authapi.mcp_tool_schema import TRANSPORT_ARGUMENT_NAMES, published_mcp_tools
from apps.authapi.tests.test_mcp_endpoint import MCP_SETTINGS, _agent_token, _rpc
from apps.authapi.tests.test_mcp_tool_calls import McpToolHappyPathTests, SPECS
from shellui_django.mcp.openapi_tools import _client_prop_schema, tools_from_schema
from shellui_django.mcp.write_gate import decide_write

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'mcp_tools_list.json'

# Dotted paths that must publish an enum. Nested arrays use ``items``.
MUST_ENUM: dict[str, set[str]] = {
    'email_list_templates': {'event_type'},
    'email_list_rules': {'service'},
    'email_list_messages': {'status', 'lane'},
    'email_get_stats': {'lane', 'event_type'},
    'email_create_rules': {'event_type', 'service', 'recipient_mode', 'language'},
    'email_update_rules': {'recipient_mode', 'language'},
    'email_create_action_rules': {'event_type'},
    'email_replace_provider': {'provider'},
    'email_create_suppressions': {'reason', 'lanes.items'},
    'email_create_newsletters': {'default_language'},
    'email_update_newsletters': {'default_language'},
    'email_create_newsletters_subscribers': {'mode', 'language'},
    'email_import_newsletter_subscribers': {'mode'},
    'email_list_newsletters_subscribers': {'status'},
    'email_export_newsletter_subscribers': {'status'},
    'email_create_broadcasts': {'language', 'audience.mode', 'audience.roles.items', 'audience.access'},
    'email_update_broadcasts': {'audience.mode', 'audience.roles.items', 'audience.access'},
    'email_broadcasts_preview': {'audience.mode', 'audience.roles.items', 'audience.access'},
}

_BANNED_COPY = re.compile(r'\b(easy|simple|quick|seamless|robust|powerful|just|very|really|simply)\b', re.I)


def _access_row(tool: dict) -> dict:
    meta = tool['_meta']
    method = str(meta['method'])
    read_only = bool(meta['read_only'])
    destructive = bool(meta['destructive'])
    ask = decide_write(
        method=method,
        access_mode='ask',
        destructive=destructive,
        allow_destructive_without_asking=False,
        tool_read_only=read_only,
    )
    full = decide_write(
        method=method,
        access_mode='full',
        destructive=destructive,
        allow_destructive_without_asking=False,
        tool_read_only=read_only,
    )
    return {
        'method': method,
        'path': meta['path'],
        'read_only': read_only,
        'destructive': destructive,
        'needs_approval_ask': ask.needs_approval,
        'needs_approval_full': full.needs_approval,
        'body_required': meta.get('body_required') or [],
        'query_required': meta.get('query_required') or [],
        'path_params': meta.get('path_params') or [],
    }


@override_settings(**MCP_SETTINGS)
class McpToolContractTests(TestCase):
    def test_tools_list_matches_pinned_catalog(self):
        from apps.authapi.mcp_identity import reset_revocation_cache_for_tests
        from apps.authapi.mcp_views import _schema, reset_mcp_server_cache
        from apps.authapi.tests.test_mcp_endpoint import (
            _snapshot_agent_id,
            make_identity_get_side_effect,
            make_identity_post_side_effect,
        )
        from unittest.mock import patch
        import uuid
        from apps.authapi.tests import test_mcp_endpoint as endpoint_tests

        endpoint_tests._SNAPSHOT_AGENT_ID = str(uuid.uuid4())
        reset_mcp_server_cache()
        reset_revocation_cache_for_tests()

        def _agents_fn():
            return [{'agent_id': _snapshot_agent_id(), 'company_id': 42}]

        with (
            patch(
                'apps.authapi.mcp_identity.requests.get',
                side_effect=make_identity_get_side_effect(_agents_fn),
            ),
            patch(
                'apps.authapi.mcp_identity.requests.post',
                side_effect=make_identity_post_side_effect(_agents_fn),
            ),
        ):
            token, _agent_id = _agent_token()
            response = _rpc(self.client, token, 'tools/list')

        self.assertEqual(response.status_code, 200, response.content)
        listed = response.json()['result']['tools']
        listed_public = [{k: v for k, v in tool.items() if not str(k).startswith('_')} for tool in listed]
        schema_tools = tools_from_schema(_schema())
        self.assertEqual([tool['name'] for tool in listed_public], [tool['name'] for tool in schema_tools])

        pinned = json.loads(FIXTURE.read_text(encoding='utf-8'))
        self.assertEqual(listed_public, pinned['tools'])
        access = {tool['name']: _access_row(tool) for tool in schema_tools}
        self.assertEqual(access, pinned['access'])
        self.assertEqual(set(access), set(MCP_TOOL_NAMES.values()))
        for name, row in access.items():
            schema = next(tool['inputSchema'] for tool in listed_public if tool['name'] == name)
            pinned_schema = next(tool['inputSchema'] for tool in pinned['tools'] if tool['name'] == name)
            self.assertEqual(schema['required'], pinned_schema['required'])
            for field in row['body_required']:
                self.assertIn(field, schema['required'], name)
            for field in row['path_params']:
                self.assertIn(field, schema['required'], name)
            self.assertFalse(TRANSPORT_ARGUMENT_NAMES & set(schema['properties']), name)
            self.assertFalse(TRANSPORT_ARGUMENT_NAMES & set(schema['required']), name)

    def test_published_names_match_gateway_email_prefix(self):
        from apps.authapi.mcp_views import _schema

        self.assertEqual(set(MCP_TOOL_NAMES), set(MCP_REQUIRED_OPERATION_IDS))
        published = set(MCP_TOOL_NAMES.values())
        self.assertEqual(len(published), len(MCP_TOOL_NAMES))
        for name in published:
            self.assertTrue(name.startswith('email_'), name)
            self.assertFalse(name.startswith('api_v1_'), name)
        listed = {tool['name'] for tool in tools_from_schema(_schema())}
        self.assertEqual(listed, published)
        for name in listed:
            self.assertTrue(name.startswith('email_'), name)
            self.assertFalse(name.startswith('api_v1_'), name)

    def test_every_tool_has_a_happy_path_call_test(self):
        happy = {spec['tool'] for spec in SPECS}
        methods = {
            name.removeprefix('test_happy_')
            for name in dir(McpToolHappyPathTests)
            if name.startswith('test_happy_')
        }
        self.assertEqual(happy, methods)
        pinned = json.loads(FIXTURE.read_text(encoding='utf-8'))
        self.assertEqual(methods, {tool['name'] for tool in pinned['tools']})

    def test_published_tools_are_flat_annotated_and_enum_complete(self):
        from apps.authapi.mcp_views import _schema

        schema = _schema()
        tools = published_mcp_tools(schema)
        by_name = {tool['name']: tool for tool in tools}
        self.assertEqual(set(by_name), set(MCP_TOOL_NAMES.values()))
        openapi_enums = _openapi_input_enums(schema)
        for tool in tools:
            name = tool['name']
            props = tool['inputSchema']['properties']
            required = tool['inputSchema']['required']
            self.assertFalse(TRANSPORT_ARGUMENT_NAMES & set(props), name)
            self.assertFalse(TRANSPORT_ARGUMENT_NAMES & set(required), name)
            self.assertEqual(tool['inputSchema']['additionalProperties'], False, name)
            annotations = tool['annotations']
            meta = tool['_meta']
            self.assertEqual(annotations['readOnlyHint'], bool(meta['read_only']), name)
            self.assertEqual(annotations['destructiveHint'], bool(meta['destructive']), name)
            self.assertFalse(annotations['readOnlyHint'] and annotations['destructiveHint'], name)
            if meta['read_only']:
                self.assertTrue(annotations['readOnlyHint'], name)
            description = tool['description']
            self.assertTrue(description, name)
            self.assertNotIn('—', description, name)
            self.assertNotIn('–', description, name)
            self.assertIsNone(_BANNED_COPY.search(description), name)
            for bad in ('Shell' + 'UI', 'Shell' + 'Ui', 'shell' + 'UI'):
                self.assertNotIn(bad, description, name)
            plain = re.sub(r'`[^`]*`', '', description)
            self.assertLessEqual(len(re.findall(r'[.!?]', plain)), 2, description)
            for dotted, enum in openapi_enums.get(name, {}).items():
                actual = _dotted(props, dotted)
                self.assertIsNotNone(actual, (name, dotted))
                self.assertEqual(actual.get('enum'), enum, (name, dotted))
        for name, paths in MUST_ENUM.items():
            props = by_name[name]['inputSchema']['properties']
            for dotted in paths:
                node = _dotted(props, dotted)
                self.assertIsNotNone(node, (name, dotted))
                self.assertTrue(node.get('enum'), (name, dotted))
                self.assertTrue(str(node.get('description') or '').strip(), (name, dotted))


def _dotted(properties: dict, path: str) -> dict | None:
    node: dict | None = {'properties': properties}
    for part in path.split('.'):
        if node is None:
            return None
        if part == 'items':
            child = node.get('items')
            node = child if isinstance(child, dict) else None
            continue
        props = node.get('properties') if isinstance(node.get('properties'), dict) else None
        if props is None:
            return None
        child = props.get(part)
        node = child if isinstance(child, dict) else None
    return node


def _openapi_input_enums(schema: dict) -> dict[str, dict[str, list]]:
    """Enums on MCP operation inputs, keyed by published tool name and dotted field."""
    found: dict[str, dict[str, list]] = {}
    for path_item in (schema.get('paths') or {}).values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in {'get', 'post', 'put', 'patch', 'delete'} or not isinstance(operation, dict):
                continue
            if 'mcp-tools' not in (operation.get('tags') or []):
                continue
            name = str(operation.get('operationId') or '')
            enums: dict[str, list] = {}
            for param in operation.get('parameters') or []:
                if not isinstance(param, dict) or param.get('in') not in {'query', 'path'}:
                    continue
                _collect_enums(param.get('schema') or {}, schema, str(param.get('name') or ''), enums)
            body = ((operation.get('requestBody') or {}).get('content') or {}).get('application/json', {}).get('schema')
            if isinstance(body, dict):
                client = _client_prop_schema(body, schema)
                for key, prop in (client.get('properties') or {}).items():
                    _collect_client_enums(prop, str(key), enums)
            if enums:
                found[name] = enums
    return found


def _collect_enums(node: dict, root: dict, prefix: str, into: dict[str, list]) -> None:
    if not isinstance(node, dict) or not prefix:
        return
    client = _client_prop_schema(node, root)
    _collect_client_enums(client, prefix, into)


def _collect_client_enums(node: dict, prefix: str, into: dict[str, list]) -> None:
    if not isinstance(node, dict):
        return
    enum = node.get('enum')
    if isinstance(enum, list) and enum:
        into[prefix] = enum
    items = node.get('items')
    if isinstance(items, dict):
        _collect_client_enums(items, f'{prefix}.items', into)
    for key, child in (node.get('properties') or {}).items():
        if isinstance(child, dict):
            _collect_client_enums(child, f'{prefix}.{key}', into)
