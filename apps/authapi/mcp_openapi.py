"""OpenAPI ``x-mcp`` markers for Shellui MCP tools on ``POST /mcp``."""

from __future__ import annotations

from typing import Any

from apps.authapi.mcp_tool_names import MCP_TOOL_NAMES

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
        'api_v1_metrics_retrieve',
        'api_v1_provider_webhooks_resend_create',
        'api_v1_service_clients_list',
        'api_v1_service_clients_create',
        'api_v1_scheduled_jobs_list',
        'api_v1_scheduled_jobs_runs_list',
        'api_v1_scheduled_jobs_runs_retrieve',
        'api_v1_public_newsletters_subscribe',
    }
)

# Company-owner admin APIs that must be explicitly decorated with ``mcp_tool`` to become MCP tools.
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

# OpenAPI component properties that must carry ``x-mcp-secret`` (plaintext secrets or key material).
MCP_REQUIRED_SECRET_SCHEMA_PROPERTIES: frozenset[tuple[str, str]] = frozenset(
    {
        ('ProviderCredentials', 'api_key'),
        ('ProviderCredentials', 'password'),
        ('ProviderCredentials', 'secret_access_key'),
        ('ProviderWrite', 'webhook_secret'),
        ('ActionRule', 'secret'),
        ('ActionRuleCreate', 'secret'),
        ('NewsletterCreate', 'turnstile_secret'),
        ('PatchedNewsletterWrite', 'turnstile_secret'),
    }
)

# Populated only by ``mcp_tool(...)`` at import time (strict allowlist).
MCP_EXPLICIT_REGISTRY: dict[str, dict[str, Any]] = {}


def _mcp_payload(*, read_only: bool, destructive: bool = False, sensitive: bool = False) -> dict[str, Any]:
    return {
        'read_only': read_only,
        'destructive': destructive,
        'sensitive': sensitive,
    }


def mcp_tool(
    *,
    operation_id: str,
    read_only: bool,
    destructive: bool = False,
    sensitive: bool = False,
) -> dict[str, Any]:
    """Register an MCP tool and return ``extend_schema`` kwargs (only explicit decoration exposes a tool)."""
    if operation_id in MCP_FORBIDDEN_OPERATION_IDS:
        raise ValueError(f'mcp_tool cannot tag forbidden operation: {operation_id}')
    published = MCP_TOOL_NAMES.get(operation_id)
    if not published:
        raise ValueError(f'mcp_tool missing published name for {operation_id}')
    payload = _mcp_payload(read_only=read_only, destructive=destructive, sensitive=sensitive)
    payload['tool_name'] = published
    existing = MCP_EXPLICIT_REGISTRY.get(operation_id)
    if existing is not None and existing != payload:
        raise ValueError(f'mcp_tool called twice with different flags for {operation_id}')
    MCP_EXPLICIT_REGISTRY[operation_id] = payload
    return {'extensions': {'x-mcp': payload}}


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

    tagged_in_schema: dict[str, dict[str, Any]] = {}
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for prefix in MCP_EXCLUDED_PATH_PREFIXES:
            if path.startswith(prefix):
                if any(mcp_extension(op) for op in path_item.values() if isinstance(op, dict)):
                    raise AssertionError(f'x-mcp must not appear under excluded path: {path}')
        for operation in path_item.values():
            if not isinstance(operation, dict) or 'operationId' not in operation:
                continue
            op_id = operation['operationId']
            ext = mcp_extension(operation)
            if op_id in MCP_FORBIDDEN_OPERATION_IDS and ext is not None:
                raise AssertionError(f'forbidden operation must not be tagged x-mcp: {op_id}')
            if ext is not None:
                tagged_in_schema[op_id] = ext

    if set(tagged_in_schema) != set(MCP_EXPLICIT_REGISTRY):
        raise AssertionError(
            f'x-mcp in schema must match mcp_tool registry only: '
            f'schema={sorted(tagged_in_schema)} registry={sorted(MCP_EXPLICIT_REGISTRY)}'
        )
    for op_id, payload in tagged_in_schema.items():
        if payload != MCP_EXPLICIT_REGISTRY[op_id]:
            raise AssertionError(f'x-mcp payload mismatch for {op_id}: {payload} != {MCP_EXPLICIT_REGISTRY[op_id]}')

    missing_decorators = MCP_REQUIRED_OPERATION_IDS - set(MCP_EXPLICIT_REGISTRY)
    if missing_decorators:
        raise AssertionError(f'required operations missing mcp_tool decoration: {sorted(missing_decorators)}')

    extra_decorators = set(MCP_EXPLICIT_REGISTRY) - MCP_REQUIRED_OPERATION_IDS
    if extra_decorators:
        raise AssertionError(f'mcp_tool on operations outside allowlist: {sorted(extra_decorators)}')


def validate_mcp_secret_fields(schema: dict[str, Any]) -> None:
    components = schema.get('components') or {}
    schemas = components.get('schemas') or {}
    missing = []
    for component, prop in MCP_REQUIRED_SECRET_SCHEMA_PROPERTIES:
        definition = schemas.get(component) or {}
        props = definition.get('properties') or {}
        field = props.get(prop) or {}
        if field.get('x-mcp-secret') is not True:
            missing.append(f'{component}.{prop}')
    if missing:
        raise AssertionError(f'schema properties missing x-mcp-secret: {sorted(missing)}')
