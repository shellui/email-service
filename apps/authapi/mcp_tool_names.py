"""Published Shellui MCP tool names for email-service.

``shellui-django`` 0.5.1 names tools from the OpenAPI ``operationId`` (``api_v1_*``).
The gateway on ai-service ``develop`` already exports these ``email_*`` names
(``apps/connector/tests/fixtures/mcp_tool_release_snapshot.json``). This map is that
list. ``x-mcp.tool_name`` carries the same string, and the MCP schema copy used by
``POST /mcp`` swaps ``operationId`` to it so ``tools/list`` matches the gateway.
Public OpenAPI ``operationId`` values stay ``api_v1_*``.
"""

from __future__ import annotations

from typing import Any

# operationId -> name an MCP client sees. Must match the ai-service gateway snapshot.
MCP_TOOL_NAMES: dict[str, str] = {
    'api_v1_actions_deliveries_list': 'email_list_action_deliveries',
    'api_v1_actions_deliveries_requeue': 'email_requeue_action_deliveries',
    'api_v1_actions_deliveries_retrieve': 'email_get_action_deliveries',
    'api_v1_actions_event_log_list': 'email_list_action_event_log',
    'api_v1_actions_event_log_retention_retrieve': 'email_get_action_event_log_retention',
    'api_v1_actions_event_log_retrieve': 'email_get_action_event_log',
    'api_v1_actions_event_log_types_list': 'email_list_action_event_log_types',
    'api_v1_actions_events_list': 'email_list_action_events',
    'api_v1_actions_rules_create': 'email_create_action_rules',
    'api_v1_actions_rules_destroy': 'email_delete_action_rules',
    'api_v1_actions_rules_list': 'email_list_action_rules',
    'api_v1_actions_rules_partial_update': 'email_update_action_rules',
    'api_v1_actions_rules_retrieve': 'email_get_action_rules',
    'api_v1_actions_rules_rotate_secret': 'email_rotate_rule_secret',
    'api_v1_actions_rules_send_test': 'email_send_test_rule',
    'api_v1_auth_link_hosts_retrieve': 'email_get_auth_link_hosts',
    'api_v1_auth_link_hosts_update': 'email_replace_auth_link_hosts',
    'api_v1_broadcasts_create': 'email_create_broadcasts',
    'api_v1_broadcasts_destroy': 'email_delete_broadcasts',
    'api_v1_broadcasts_list': 'email_list_broadcasts',
    'api_v1_broadcasts_partial_update': 'email_update_broadcasts',
    'api_v1_broadcasts_preview': 'email_broadcasts_preview',
    'api_v1_broadcasts_retrieve': 'email_get_broadcasts',
    'api_v1_broadcasts_send': 'email_broadcasts_send',
    'api_v1_catalog_retrieve': 'email_get_catalog',
    'api_v1_library_create': 'email_create_library',
    'api_v1_library_destroy': 'email_delete_library',
    'api_v1_library_list': 'email_list_library',
    'api_v1_library_retrieve': 'email_get_library',
    'api_v1_library_update': 'email_replace_library',
    'api_v1_messages_cancel': 'email_cancel_messages',
    'api_v1_messages_list': 'email_list_messages',
    'api_v1_messages_retrieve': 'email_get_messages',
    'api_v1_newsletters_create': 'email_create_newsletters',
    'api_v1_newsletters_destroy': 'email_delete_newsletters',
    'api_v1_newsletters_list': 'email_list_newsletters',
    'api_v1_newsletters_partial_update': 'email_update_newsletters',
    'api_v1_newsletters_retrieve': 'email_get_newsletters',
    'api_v1_newsletters_rotate_key': 'email_rotate_newsletter_key',
    'api_v1_newsletters_subscribers_create': 'email_create_newsletters_subscribers',
    'api_v1_newsletters_subscribers_destroy': 'email_delete_newsletters_subscribers',
    'api_v1_newsletters_subscribers_export': 'email_export_newsletter_subscribers',
    'api_v1_newsletters_subscribers_import': 'email_import_newsletter_subscribers',
    'api_v1_newsletters_subscribers_list': 'email_list_newsletters_subscribers',
    'api_v1_provider_retrieve': 'email_get_provider',
    'api_v1_provider_test_send': 'email_test_send_provider',
    'api_v1_provider_update': 'email_replace_provider',
    'api_v1_rules_create': 'email_create_rules',
    'api_v1_rules_destroy': 'email_delete_rules',
    'api_v1_rules_list': 'email_list_rules',
    'api_v1_rules_partial_update': 'email_update_rules',
    'api_v1_rules_retrieve': 'email_get_rules',
    'api_v1_stats_retrieve': 'email_get_stats',
    'api_v1_suppressions_create': 'email_create_suppressions',
    'api_v1_suppressions_destroy': 'email_delete_suppressions',
    'api_v1_suppressions_list': 'email_list_suppressions',
    'api_v1_template_versions_create': 'email_create_template_versions',
    'api_v1_template_versions_list': 'email_list_template_versions',
    'api_v1_template_versions_publish': 'email_publish_template_versions',
    'api_v1_template_versions_retrieve': 'email_get_template_versions',
    'api_v1_templates_destroy': 'email_delete_templates',
    'api_v1_templates_list': 'email_list_templates',
    'api_v1_templates_render_preview': 'email_render_preview_templates',
    'api_v1_templates_retrieve': 'email_get_templates',
    'api_v1_templates_send_test': 'email_send_test_templates',
}


def publish_mcp_tool_names(schema: dict[str, Any]) -> dict[str, Any]:
    """Point MCP ``operationId`` values at ``x-mcp.tool_name`` (copy; caller owns ``schema``)."""
    paths = schema.get('paths') or {}
    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict) or 'operationId' not in operation:
                continue
            ext = operation.get('x-mcp')
            if not isinstance(ext, dict):
                nested = (operation.get('extensions') or {}).get('x-mcp')
                ext = nested if isinstance(nested, dict) else None
            if not ext:
                continue
            published = str(ext.get('tool_name') or '').strip()
            if not published:
                raise ValueError(f"MCP operation {operation.get('operationId')} is missing x-mcp.tool_name")
            operation['operationId'] = published
    return schema
