"""OpenAPI copy for company-owner admin APIs (written for agents and humans)."""

from drf_spectacular.utils import OpenApiExample

AUTH_ADMIN = (
    'Bearer JWT from identity-service. Callers must be the company owner for the target '
    '`company_id`, or Shellui staff. Service keys (`esk_`) cannot use these routes.'
)

COMPANY_QUERY_HELP = (
    'Target company. Owners omit this when their JWT already carries `company_id`. '
    'Staff may set it to administer another company.'
)

TEMPLATE_LIST_SUMMARY = 'List event email copies for a company'
TEMPLATE_LIST_DESCRIPTION = (
    f'{AUTH_ADMIN} Returns one row per language copy tied to catalog events or broadcasts. '
    'Filter with `event_type` to only copies that fit that catalog event. '
    'Response rows include `template_key`, `active_version`, and `kind` (`event` or `broadcast`).'
)

TEMPLATE_RENDER_PREVIEW_SUMMARY = 'Render HTML preview for a template version'
TEMPLATE_RENDER_PREVIEW_DESCRIPTION = (
    f'{AUTH_ADMIN} Composes the same HTML and plain text as production, with placeholders substituted. '
    'Does not enqueue mail. Send `document` to preview unsaved editor JSON, or omit it to use a stored version. '
    'When `variables` is omitted, catalog example values fill known tokens.'
)

RULE_LIST_SUMMARY = 'List email rules for a company'
RULE_LIST_DESCRIPTION = (
    f'{AUTH_ADMIN} Each rule binds a catalog `event_type` to a template copy and recipients. '
    'Built-in auth rules appear but cannot be deleted or disabled. Filter with `service` when you only need one emitter.'
)

ACTIONS_RULES_LIST_SUMMARY = 'List Shellui Actions webhook rules'
ACTIONS_RULES_LIST_DESCRIPTION = (
    f'{AUTH_ADMIN} Each rule posts signed HTTPS webhooks when an email domain event fires. '
    'Secrets are write-only except immediately after create or rotate.'
)

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
