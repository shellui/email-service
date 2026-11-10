"""OpenAPI copy for company-owner admin APIs (written for agents and humans)."""

from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse

from apps.authapi.mcp_openapi import MCP_REQUIRED_OPERATION_IDS
from apps.email.openapi_mcp_registry import MCP_DESCRIPTIONS
from apps.email.schema import ErrorSerializer

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
    """Summary and description for an MCP allowlisted operation.

    Both fields carry the same one or two sentences. shellui-django 0.5.1 copies
    ``summary`` into the MCP tool description, and the ai-service gateway may read
    either field.
    """
    text = MCP_DESCRIPTIONS.get(operation_id)
    if not text:
        raise KeyError(f'missing MCP OpenAPI registry entry for {operation_id}')
    return {'summary': text, 'description': text}


if set(MCP_DESCRIPTIONS) != MCP_REQUIRED_OPERATION_IDS:
    missing = MCP_REQUIRED_OPERATION_IDS - set(MCP_DESCRIPTIONS)
    extra = set(MCP_DESCRIPTIONS) - MCP_REQUIRED_OPERATION_IDS
    raise AssertionError(f'MCP_DESCRIPTIONS drift: missing={sorted(missing)} extra={sorted(extra)}')


TEMPLATE_LIST_SUMMARY = MCP_DESCRIPTIONS['api_v1_templates_list']
TEMPLATE_LIST_DESCRIPTION = MCP_DESCRIPTIONS['api_v1_templates_list']

TEMPLATE_RENDER_PREVIEW_SUMMARY = MCP_DESCRIPTIONS['api_v1_templates_render_preview']
TEMPLATE_RENDER_PREVIEW_DESCRIPTION = MCP_DESCRIPTIONS['api_v1_templates_render_preview']

RULE_LIST_SUMMARY = MCP_DESCRIPTIONS['api_v1_rules_list']
RULE_LIST_DESCRIPTION = MCP_DESCRIPTIONS['api_v1_rules_list']

ACTIONS_RULES_LIST_SUMMARY = MCP_DESCRIPTIONS['api_v1_actions_rules_list']
ACTIONS_RULES_LIST_DESCRIPTION = MCP_DESCRIPTIONS['api_v1_actions_rules_list']

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
