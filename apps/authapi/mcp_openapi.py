"""OpenAPI ``x-mcp`` markers for Shellui MCP (schema only; server lives in identity-service)."""

from __future__ import annotations

from typing import Any

# Paths that must not appear in the published OpenAPI document.
MCP_EXCLUDED_PATH_PREFIXES: tuple[str, ...] = (
    '/api/v1/internal/',
    '/api/v1/scheduled-jobs',
    '/api/v1/service-clients',
)

# Operation ids that must never carry ``x-mcp`` (service, staff, or public callbacks).
MCP_FORBIDDEN_OPERATION_IDS: frozenset[str] = frozenset(
    {
        'api_v1_health_retrieve',
        'api_v1_send_create',
        'api_v1_send_batch_create',
        'api_v1_events_create',
        'api_v1_lanes_switch',
        'api_v1_privacy_erase',
        'api_v1_metrics_all_retrieve',
        'api_v1_provider_webhooks_resend_create',
        'api_v1_service_clients_list',
        'api_v1_service_clients_create',
        'api_v1_scheduled_jobs_list',
        'api_v1_scheduled_jobs_runs_list',
        'api_v1_scheduled_jobs_runs_retrieve',
        'api_v1_public_newsletters_subscribe',
    }
)

# Company-owner admin APIs agents may call through Shellui MCP.
MCP_REQUIRED_OPERATION_IDS: frozenset[str] = frozenset(
    {
        'api_v1_catalog_retrieve',
        'api_v1_library_list',
        'api_v1_library_create',
        'api_v1_library_retrieve',
        'api_v1_library_update',
        'api_v1_library_destroy',
        'api_v1_templates_list',
        'api_v1_templates_retrieve',
        'api_v1_templates_destroy',
        'api_v1_template_versions_list',
        'api_v1_template_versions_create',
        'api_v1_template_versions_retrieve',
        'api_v1_template_versions_publish',
        'api_v1_templates_render_preview',
        'api_v1_templates_send_test',
        'api_v1_rules_list',
        'api_v1_rules_create',
        'api_v1_rules_retrieve',
        'api_v1_rules_partial_update',
        'api_v1_rules_destroy',
        'api_v1_provider_retrieve',
        'api_v1_provider_update',
        'api_v1_auth_link_hosts_retrieve',
        'api_v1_auth_link_hosts_update',
        'api_v1_provider_test_send',
        'api_v1_stats_retrieve',
        'api_v1_suppressions_list',
        'api_v1_suppressions_create',
        'api_v1_suppressions_destroy',
        'api_v1_messages_list',
        'api_v1_messages_retrieve',
        'api_v1_messages_cancel',
        'api_v1_broadcasts_list',
        'api_v1_broadcasts_create',
        'api_v1_broadcasts_retrieve',
        'api_v1_broadcasts_partial_update',
        'api_v1_broadcasts_destroy',
        'api_v1_broadcasts_preview',
        'api_v1_broadcasts_send',
        'api_v1_newsletters_list',
        'api_v1_newsletters_create',
        'api_v1_newsletters_retrieve',
        'api_v1_newsletters_partial_update',
        'api_v1_newsletters_destroy',
        'api_v1_newsletters_rotate_key',
        'api_v1_newsletters_subscribers_list',
        'api_v1_newsletters_subscribers_create',
        'api_v1_newsletters_subscribers_destroy',
        'api_v1_newsletters_subscribers_export',
        'api_v1_newsletters_subscribers_import',
        'api_v1_actions_events_list',
        'api_v1_actions_rules_list',
        'api_v1_actions_rules_create',
        'api_v1_actions_rules_retrieve',
        'api_v1_actions_rules_partial_update',
        'api_v1_actions_rules_destroy',
        'api_v1_actions_rules_rotate_secret',
        'api_v1_actions_rules_send_test',
        'api_v1_actions_deliveries_list',
        'api_v1_actions_deliveries_retrieve',
        'api_v1_actions_deliveries_requeue',
        'api_v1_actions_event_log_list',
        'api_v1_actions_event_log_retrieve',
        'api_v1_actions_event_log_types_list',
        'api_v1_actions_event_log_retention_retrieve',
    }
)


def _mcp_payload(*, read_only: bool, destructive: bool = False, sensitive: bool = False) -> dict[str, Any]:
    return {
        'read_only': read_only,
        'destructive': destructive,
        'sensitive': sensitive,
    }


# Default ``x-mcp`` flags per operation id (also applied when ``**mcp_tool(...)`` is omitted).
MCP_OPERATION_FLAGS: dict[str, dict[str, Any]] = {
    'api_v1_catalog_retrieve': _mcp_payload(read_only=True),
    'api_v1_library_list': _mcp_payload(read_only=True),
    'api_v1_library_create': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_library_retrieve': _mcp_payload(read_only=True),
    'api_v1_library_update': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_library_destroy': _mcp_payload(read_only=False, destructive=True),
    'api_v1_templates_list': _mcp_payload(read_only=True),
    'api_v1_templates_retrieve': _mcp_payload(read_only=True),
    'api_v1_templates_destroy': _mcp_payload(read_only=False, destructive=True),
    'api_v1_template_versions_list': _mcp_payload(read_only=True),
    'api_v1_template_versions_create': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_template_versions_retrieve': _mcp_payload(read_only=True),
    'api_v1_template_versions_publish': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_templates_render_preview': _mcp_payload(read_only=True, sensitive=True),
    'api_v1_templates_send_test': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_rules_list': _mcp_payload(read_only=True),
    'api_v1_rules_create': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_rules_retrieve': _mcp_payload(read_only=True),
    'api_v1_rules_partial_update': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_rules_destroy': _mcp_payload(read_only=False, destructive=True),
    'api_v1_provider_retrieve': _mcp_payload(read_only=True, sensitive=True),
    'api_v1_provider_update': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_auth_link_hosts_retrieve': _mcp_payload(read_only=True),
    'api_v1_auth_link_hosts_update': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_provider_test_send': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_stats_retrieve': _mcp_payload(read_only=True, sensitive=True),
    'api_v1_suppressions_list': _mcp_payload(read_only=True, sensitive=True),
    'api_v1_suppressions_create': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_suppressions_destroy': _mcp_payload(read_only=False, destructive=True),
    'api_v1_messages_list': _mcp_payload(read_only=True, sensitive=True),
    'api_v1_messages_retrieve': _mcp_payload(read_only=True, sensitive=True),
    'api_v1_messages_cancel': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_broadcasts_list': _mcp_payload(read_only=True),
    'api_v1_broadcasts_create': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_broadcasts_retrieve': _mcp_payload(read_only=True),
    'api_v1_broadcasts_partial_update': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_broadcasts_destroy': _mcp_payload(read_only=False, destructive=True),
    'api_v1_broadcasts_preview': _mcp_payload(read_only=True, sensitive=True),
    'api_v1_broadcasts_send': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_newsletters_list': _mcp_payload(read_only=True),
    'api_v1_newsletters_create': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_newsletters_retrieve': _mcp_payload(read_only=True),
    'api_v1_newsletters_partial_update': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_newsletters_destroy': _mcp_payload(read_only=False, destructive=True),
    'api_v1_newsletters_rotate_key': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_newsletters_subscribers_list': _mcp_payload(read_only=True, sensitive=True),
    'api_v1_newsletters_subscribers_create': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_newsletters_subscribers_destroy': _mcp_payload(read_only=False, destructive=True),
    'api_v1_newsletters_subscribers_export': _mcp_payload(read_only=True, sensitive=True),
    'api_v1_newsletters_subscribers_import': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_actions_events_list': _mcp_payload(read_only=True),
    'api_v1_actions_rules_list': _mcp_payload(read_only=True, sensitive=True),
    'api_v1_actions_rules_create': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_actions_rules_retrieve': _mcp_payload(read_only=True, sensitive=True),
    'api_v1_actions_rules_partial_update': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_actions_rules_destroy': _mcp_payload(read_only=False, destructive=True),
    'api_v1_actions_rules_rotate_secret': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_actions_rules_send_test': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_actions_deliveries_list': _mcp_payload(read_only=True, sensitive=True),
    'api_v1_actions_deliveries_retrieve': _mcp_payload(read_only=True, sensitive=True),
    'api_v1_actions_deliveries_requeue': _mcp_payload(read_only=False, sensitive=True),
    'api_v1_actions_event_log_list': _mcp_payload(read_only=True, sensitive=True),
    'api_v1_actions_event_log_retrieve': _mcp_payload(read_only=True, sensitive=True),
    'api_v1_actions_event_log_types_list': _mcp_payload(read_only=True),
    'api_v1_actions_event_log_retention_retrieve': _mcp_payload(read_only=True),
}


def mcp_tool(*, read_only: bool, destructive: bool = False, sensitive: bool = False) -> dict[str, Any]:
    """Return ``extend_schema`` kwargs that publish ``x-mcp`` on an operation."""
    return {'extensions': {'x-mcp': _mcp_payload(read_only=read_only, destructive=destructive, sensitive=sensitive)}}


def mcp_field(*, secret: bool = False, sensitive: bool = False) -> dict[str, Any]:
    """``json_schema_extra`` for serializer fields (secrets never returned to agents)."""
    extra: dict[str, Any] = {}
    if secret:
        extra['x-mcp-secret'] = True
    if sensitive:
        extra['x-mcp-sensitive'] = True
    return extra


def mcp_extension(operation: dict[str, Any]) -> dict[str, Any] | None:
    raw = operation.get('x-mcp')
    if isinstance(raw, dict):
        return raw
    extensions = operation.get('extensions') or {}
    nested = extensions.get('x-mcp')
    return nested if isinstance(nested, dict) else None


def validate_mcp_schema(schema: dict[str, Any]) -> None:
    paths = schema.get('paths') or {}
    for prefix in MCP_EXCLUDED_PATH_PREFIXES:
        for path in paths:
            if path.startswith(prefix):
                raise AssertionError(f'excluded path must not appear in OpenAPI: {path}')

    seen_required: set[str] = set()
    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict) or 'operationId' not in operation:
                continue
            op_id = operation['operationId']
            ext = mcp_extension(operation)
            if op_id in MCP_FORBIDDEN_OPERATION_IDS:
                if ext is not None:
                    raise AssertionError(f'forbidden operation must not be tagged x-mcp: {op_id}')
                continue
            if op_id in MCP_REQUIRED_OPERATION_IDS:
                seen_required.add(op_id)
                if ext is None:
                    raise AssertionError(f'company admin operation missing x-mcp: {op_id}')
            elif ext is not None:
                raise AssertionError(f'x-mcp on unexpected operation: {op_id}')

    missing = MCP_REQUIRED_OPERATION_IDS - seen_required
    if missing:
        raise AssertionError(f'OpenAPI schema missing required operations: {sorted(missing)}')
