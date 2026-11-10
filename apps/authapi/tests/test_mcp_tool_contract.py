"""Pin the MCP tool catalog an agent sees on ``tools/list``.

Renaming a tool, dropping one, or changing its input schema or required fields
fails this test. Access flags (read-only, destructive, approval) are pinned
beside the client payload because ``tools/list`` does not echo ``_meta``.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.test import TestCase, override_settings

from apps.authapi.mcp_openapi import MCP_REQUIRED_OPERATION_IDS
from apps.authapi.tests.test_mcp_endpoint import MCP_SETTINGS, _agent_token, _rpc
from apps.authapi.tests.test_mcp_tool_calls import McpToolHappyPathTests, SPECS
from shellui_django.mcp.openapi_tools import tools_from_schema
from shellui_django.mcp.write_gate import decide_write

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'mcp_tools_list.json'


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
        self.assertEqual(set(access), set(MCP_REQUIRED_OPERATION_IDS))
        for name, row in access.items():
            schema = next(tool['inputSchema'] for tool in listed_public if tool['name'] == name)
            self.assertEqual(schema['required'], pinned['tools'][next(
                index for index, tool in enumerate(pinned['tools']) if tool['name'] == name
            )]['inputSchema']['required'])
            body_required = (schema['properties'].get('body') or {}).get('required') or []
            self.assertEqual(body_required, row['body_required'])

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
