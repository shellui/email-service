"""OpenAPI copy for company-owner admin APIs (written for agents and humans)."""

from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse

from apps.authapi.mcp_openapi import MCP_REQUIRED_OPERATION_IDS
from apps.email.openapi_mcp_registry import MCP_SPECS
from apps.email.schema import ErrorSerializer

PERMISSIONS_BLOCK = (
    'Permissions: Shellui company owner for the target `company_id`, or Shellui staff. '
    'Send `Authorization: Bearer <jwt>` from identity-service. Service API keys (`esk_`) cannot call this route.'
)

# Backward-compatible alias used in older description strings.
AUTH_ADMIN = PERMISSIONS_BLOCK

COMPANY_QUERY_HELP = (
    'Target company. Owners omit this when their JWT already carries `company_id`. '
    'Staff may set it to administer another company.'
)

MESSAGE_ID_PATH = OpenApiParameter(
    name='message_id',
    type=OpenApiTypes.STR,
    location=OpenApiParameter.PATH,
    description='Public message id (for example `msg_01h2x3y4z5`).',
)

TEMPLATE_ID_PATH = OpenApiParameter(
    name='template_id',
    type=OpenApiTypes.INT,
    location=OpenApiParameter.PATH,
    description='Internal template copy id from list or rule responses.',
)

NEWSLETTER_ID_PATH = OpenApiParameter(
    name='newsletter_id',
    type=OpenApiTypes.INT,
    location=OpenApiParameter.PATH,
    description='Newsletter list id.',
)

RULE_ID_PATH = OpenApiParameter(
    name='id',
    type=OpenApiTypes.INT,
    location=OpenApiParameter.PATH,
    description='Email rule id.',
)

ACTION_RULE_ID_PATH = OpenApiParameter(
    name='id',
    type=OpenApiTypes.INT,
    location=OpenApiParameter.PATH,
    description='Shellui Actions webhook rule id.',
)

DELIVERY_ID_PATH = OpenApiParameter(
    name='delivery_id',
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.PATH,
    description='Webhook delivery id (UUID string).',
)


def admin_description(
    *,
    lead: str,
    side_effects: str,
    sends_email: bool = False,
    destructive: bool = False,
    notes: str = '',
) -> str:
    """Standard MCP-facing operation description."""
    parts = [lead.strip(), PERMISSIONS_BLOCK]
    parts.append(f'Side effects: {side_effects.strip()}')
    parts.append(f'Sends email: {"Yes" if sends_email else "No"}.')
    parts.append(f'Destructive: {"Yes" if destructive else "No"}.')
    if notes.strip():
        parts.append(notes.strip())
    return '\n\n'.join(parts)


def _error_response(description: str) -> OpenApiResponse:
    return OpenApiResponse(
        response=ErrorSerializer,
        description=description,
    )


ADMIN_API_ERRORS = {
    400: _error_response(
        'Invalid query or body. Body uses `error_code` (often `validation_failed`) and optional '
        '`field_errors` mapping field names to stable codes (for example `required`, `invalid`).'
    ),
    401: _error_response('Missing or invalid Bearer token (`error_code`: `unauthorized`).'),
    403: _error_response(
        'Caller is authenticated but not allowed (`error_code`: `forbidden`). '
        'Common causes: wrong company, staff-only field, or built-in auth rule guard.'
    ),
    404: _error_response('Resource not found (`error_code`: `not_found` or a domain-specific code).'),
    409: _error_response(
        'Conflict with current state (`error_code`: for example `template_in_use`, `message_not_cancellable`).'
    ),
    429: _error_response(
        'Rate limited (`error_code`: `rate_limited`, `company_rate_limited`, or `recipient_rate_limited`). '
        'Retry after backing off.'
    ),
}


def mcp_openapi_fields(operation_id: str) -> dict[str, str]:
    """Summary and description for an MCP allowlisted operation."""
    spec = MCP_SPECS.get(operation_id)
    if spec is None:
        raise KeyError(f'missing MCP OpenAPI registry entry for {operation_id}')
    return {
        'summary': spec.summary,
        'description': admin_description(
            lead=spec.lead,
            side_effects=spec.side_effects,
            sends_email=spec.sends_email,
            destructive=spec.destructive,
            notes=spec.notes,
        ),
    }


if set(MCP_SPECS) != MCP_REQUIRED_OPERATION_IDS:
    missing = MCP_REQUIRED_OPERATION_IDS - set(MCP_SPECS)
    extra = set(MCP_SPECS) - MCP_REQUIRED_OPERATION_IDS
    raise AssertionError(f'MCP_SPECS drift: missing={sorted(missing)} extra={sorted(extra)}')


def _fields(op_id: str) -> dict[str, str]:
    return mcp_openapi_fields(op_id)


TEMPLATE_LIST_SUMMARY = MCP_SPECS['api_v1_templates_list'].summary
TEMPLATE_LIST_DESCRIPTION = mcp_openapi_fields('api_v1_templates_list')['description']

TEMPLATE_RENDER_PREVIEW_SUMMARY = MCP_SPECS['api_v1_templates_render_preview'].summary
TEMPLATE_RENDER_PREVIEW_DESCRIPTION = mcp_openapi_fields('api_v1_templates_render_preview')['description']

RULE_LIST_SUMMARY = MCP_SPECS['api_v1_rules_list'].summary
RULE_LIST_DESCRIPTION = mcp_openapi_fields('api_v1_rules_list')['description']

ACTIONS_RULES_LIST_SUMMARY = MCP_SPECS['api_v1_actions_rules_list'].summary
ACTIONS_RULES_LIST_DESCRIPTION = mcp_openapi_fields('api_v1_actions_rules_list')['description']

EXAMPLE_TEMPLATE_LIST = OpenApiExample(
    'Two event copies',
    value={
        'templates': [
            {
                'id': 12,
                'template_key': 'company.a1b2c3d4e5f6',
                'name': 'Magic link requested',
                'event_type': 'identity.auth.magic_link.requested',
                'kind': 'event',
                'language': 'en',
                'company_id': 1,
                'active_version': 3,
                'source_key': 'protocol.magic-link',
                'set': 'protocol',
                'head': '',
            }
        ]
    },
    response_only=True,
)

EXAMPLE_RENDER_PREVIEW_REQUEST = OpenApiExample(
    'Preview draft with sample variables',
    value={
        'variables': {
            'company_name': 'Acme',
            'action_url': 'https://app.example.com/auth/callback?token=sample',
        }
    },
    request_only=True,
)

EXAMPLE_RENDER_PREVIEW_RESPONSE = OpenApiExample(
    'Rendered bodies',
    value={
        'subject': 'Sign in to Acme',
        'html': '<html>...</html>',
        'text': 'Sign in to Acme\n\n...',
        'missing_variables': [],
    },
    response_only=True,
)

EXAMPLE_EMAIL_RULE = OpenApiExample(
    'Create rule',
    value={
        'event_type': 'storage.object.created',
        'enabled': True,
        'recipient_mode': 'event_recipients',
        'static_recipients': [],
        'language': 'en',
        'content': {'library_id': 4},
    },
    request_only=True,
)

EXAMPLE_ACTION_RULE = OpenApiExample(
    'Webhook on delivery',
    value={
        'name': 'Notify CRM',
        'event_type': 'email.message.delivered',
        'url': 'https://hooks.example.com/shellui/email',
        'enabled': True,
    },
    request_only=True,
)
