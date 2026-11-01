"""MCP admin operation summaries and bodies. Consumed by ``openapi_admin.mcp_openapi_fields``."""

from __future__ import annotations

from typing import NamedTuple


class _McpSpec(NamedTuple):
    summary: str
    lead: str
    side_effects: str
    sends_email: bool = False
    destructive: bool = False
    notes: str = ''


# Keys must match ``MCP_REQUIRED_OPERATION_IDS`` in ``apps.authapi.mcp_openapi``.
MCP_SPECS: dict[str, _McpSpec] = {
    'api_v1_catalog_retrieve': _McpSpec(
        'List catalog events for template editors',
        'Returns editable catalog events with variables, suggested subjects, and default designs. '
        'Built-in auth-only events that companies cannot edit are omitted.',
        'Read-only. No database writes.',
    ),
    'api_v1_library_list': _McpSpec(
        'List library email designs',
        'Returns built-in Shellui designs and company-owned library templates grouped by set.',
        'Read-only.',
    ),
    'api_v1_library_create': _McpSpec(
        'Create a company library design',
        'Creates a new library template, optionally duplicated from `source_id`.',
        'Inserts a library row and composed HTML snapshot.',
    ),
    'api_v1_library_retrieve': _McpSpec(
        'Read one library design',
        'Returns editor `document`, theme, variables, and rendered HTML for one library template.',
        'Read-only.',
    ),
    'api_v1_library_update': _McpSpec(
        'Update a company library design',
        'Updates name, subject, preheader, document, or theme on a company-owned library template.',
        'Persists editor JSON and refreshes composed HTML. Built-in library rows reject writes.',
    ),
    'api_v1_library_destroy': _McpSpec(
        'Delete a company library design',
        'Deletes a company-owned library template that is not referenced by active rules or copies.',
        'Removes the library row.', destructive=True,
        notes='Returns `409 template_in_use` when a rule or copy still references the design.',
    ),
    'api_v1_templates_list': _McpSpec(
        'List event and broadcast email copies',
        'Returns one row per language copy tied to catalog events or broadcasts. '
        'Filter with query `event_type` to copies that fit that catalog event.',
        'Read-only.',
    ),
    'api_v1_templates_retrieve': _McpSpec(
        'Read one event or broadcast copy',
        'Returns metadata for the copy (`template_key`, `active_version`, `kind`). '
        'Use template version endpoints for editor JSON.',
        'Read-only.',
    ),
    'api_v1_templates_destroy': _McpSpec(
        'Delete an unused template copy',
        'Deletes a template copy when no rule or broadcast references it.',
        'Removes the template and its versions.', destructive=True,
        notes='Returns `409 template_in_use` when still referenced.',
    ),
    'api_v1_template_versions_list': _McpSpec(
        'List versions of a template copy',
        'Returns every draft and published version with editor `document`, `translations`, and `theme`.',
        'Read-only.',
    ),
    'api_v1_template_versions_create': _McpSpec(
        'Create a draft template version',
        'Creates a new draft from the latest version, or starts over from `library_id` when sent.',
        'Inserts a draft version row. Publishing is a separate call.',
    ),
    'api_v1_template_versions_retrieve': _McpSpec(
        'Read one template version',
        'Returns one version including editor JSON and publish state.',
        'Read-only.',
    ),
    'api_v1_template_versions_publish': _McpSpec(
        'Publish a draft template version',
        'Validates links and placeholders in every language, then sets `active_version`.',
        'Updates template active version and version state.',
    ),
    'api_v1_templates_render_preview': _McpSpec(
        'Render HTML preview for a template version',
        'Composes production HTML and plain text with placeholders substituted. '
        'Send `document` to preview unsaved editor JSON, or omit it to use a stored version. '
        'When `variables` is omitted, catalog example values fill known tokens.',
        'Read-only composition. Does not enqueue mail.',
    ),
    'api_v1_templates_send_test': _McpSpec(
        'Send a test message for a draft',
        'Queues one message to the caller email (or `to` when staff). '
        'Use `render-preview` when you only need HTML without sending.',
        'Enqueues one outbound message through the company provider.', sends_email=True,
        notes='May return `429 company_rate_limited` or provider rate limits.',
    ),
    'api_v1_rules_list': _McpSpec(
        'List email rules for a company',
        'Each rule binds a catalog `event_type` to a template copy and recipients. '
        'Built-in auth rules appear but cannot be deleted or disabled. '
        'Filter with query `service` for one emitter.',
        'Read-only.',
    ),
    'api_v1_rules_create': _McpSpec(
        'Create an email rule and template copy',
        'Creates the rule, the template copy from `content.library_id`, and publishes the first version.',
        'Inserts rule, template, and version rows.',
    ),
    'api_v1_rules_retrieve': _McpSpec(
        'Read one email rule',
        'Returns recipients, enabled state, and linked template id for one rule.',
        'Read-only.',
    ),
    'api_v1_rules_partial_update': _McpSpec(
        'Update email rule recipients or enabled state',
        'Patches `enabled`, `recipient_mode`, `static_recipients`, `language`, or `newsletter_id`.',
        'Updates the rule row.',
        notes='Built-in auth rules reject changes to `enabled` or recipients (`403 forbidden`).',
    ),
    'api_v1_rules_destroy': _McpSpec(
        'Delete a custom email rule',
        'Deletes a company-defined rule and its dedicated template copy when allowed.',
        'Removes rule and template rows.', destructive=True,
        notes='Built-in auth rules cannot be deleted (`403 forbidden`).',
    ),
    'api_v1_provider_retrieve': _McpSpec(
        'Read company mail provider settings',
        'Returns provider name, from addresses, webhook hints, and auth link hosts. '
        'Secrets and passwords are never returned.',
        'Read-only.',
    ),
    'api_v1_provider_update': _McpSpec(
        'Update company mail provider settings',
        'Sets provider (`resend`, `ses`, or `smtp`), from addresses, credentials, and optional webhook secret.',
        'Persists encrypted credentials and provider configuration.',
        notes='Credential fields are write-only (`x-mcp-secret`).',
    ),
    'api_v1_provider_test_send': _McpSpec(
        'Send a provider configuration test message',
        'Queues one test message using the saved provider settings to the caller or optional `to`.',
        'Enqueues one outbound message.', sends_email=True,
        notes='Returns `400 provider_not_configured` when credentials are missing.',
    ),
    'api_v1_auth_link_hosts_retrieve': _McpSpec(
        'Read allowed auth link hosts',
        'Returns company extra hosts plus platform and application hosts used to validate magic link URLs.',
        'Read-only.',
    ),
    'api_v1_auth_link_hosts_update': _McpSpec(
        'Replace company auth link hosts',
        'Replaces the company-specific host allowlist used when validating URLs in templates.',
        'Updates stored host list and refreshes application host cache.',
    ),
    'api_v1_stats_retrieve': _McpSpec(
        'Read delivery statistics for a company',
        'Returns counts by day, lane, and event type for a date window (`from`, `to` query, ISO dates).',
        'Read-only aggregated metrics.',
    ),
    'api_v1_suppressions_list': _McpSpec(
        'List suppressed email addresses',
        'Returns masked addresses with reason and lane scope for the company.',
        'Read-only.',
    ),
    'api_v1_suppressions_create': _McpSpec(
        'Add a suppression',
        'Blocks future sends to an address for selected lanes (default: all lanes).',
        'Inserts or extends a suppression row.',
    ),
    'api_v1_suppressions_destroy': _McpSpec(
        'Remove a suppression',
        'Deletes one suppression row by id.',
        'Removes the suppression.', destructive=True,
    ),
    'api_v1_messages_list': _McpSpec(
        'List recent outbound messages',
        'Returns masked recipient addresses and delivery status. '
        'Filter with `status`, `lane`, `template_key`, and `limit` (max 200, default 50).',
        'Read-only.',
    ),
    'api_v1_messages_retrieve': _McpSpec(
        'Read one outbound message',
        'Returns lane, provider, status timeline, and template key. Recipient address is not included.',
        'Read-only.',
    ),
    'api_v1_messages_cancel': _McpSpec(
        'Cancel a queued message',
        'Marks a queued or retrying message as cancelled and clears stored variables.',
        'Updates message status.',
        notes='Returns `409 message_not_cancellable` when the message already left the queue.',
    ),
    'api_v1_broadcasts_list': _McpSpec(
        'List broadcast campaigns',
        'Returns draft and sent broadcasts with audience summary and delivery counts.',
        'Read-only.',
    ),
    'api_v1_broadcasts_create': _McpSpec(
        'Create a broadcast draft',
        'Creates a broadcast from a library `source_key` and optional audience filters.',
        'Inserts broadcast and template copy rows.',
    ),
    'api_v1_broadcasts_retrieve': _McpSpec(
        'Read one broadcast',
        'Returns audience, template linkage, counts, and sender configuration.',
        'Read-only.',
    ),
    'api_v1_broadcasts_partial_update': _McpSpec(
        'Update a broadcast draft',
        'Patches `name` or `audience` while the broadcast is still a draft.',
        'Updates broadcast row.',
        notes='Returns `409 broadcast_not_editable` after send has started.',
    ),
    'api_v1_broadcasts_destroy': _McpSpec(
        'Delete a broadcast draft',
        'Deletes a draft broadcast and its template copy when not sending.',
        'Removes broadcast rows.', destructive=True,
        notes='Returns `409 broadcast_not_deletable` when sending or completed.',
    ),
    'api_v1_broadcasts_preview': _McpSpec(
        'Preview broadcast audience size',
        'Counts sendable, unsubscribed, and suppressed recipients for the saved or requested audience.',
        'Read-only evaluation. Does not enqueue mail.',
    ),
    'api_v1_broadcasts_send': _McpSpec(
        'Send a broadcast',
        'Queues delivery to the broadcast audience through the company bulk sender.',
        'Enqueues many outbound messages or provider broadcast jobs.', sends_email=True,
        notes='Returns `409` when the broadcast is not in a sendable state. May return `429` when rate limited.',
    ),
    'api_v1_newsletters_list': _McpSpec(
        'List newsletter lists',
        'Returns public keys, subscriber counts, and sign-up URLs for each list.',
        'Read-only.',
    ),
    'api_v1_newsletters_create': _McpSpec(
        'Create a newsletter list',
        'Creates the list, confirmation template, and sign-up URL.',
        'Inserts newsletter and template rows.',
    ),
    'api_v1_newsletters_retrieve': _McpSpec(
        'Read one newsletter list',
        'Returns settings, Turnstile configuration flags, and subscriber counts.',
        'Read-only.',
    ),
    'api_v1_newsletters_partial_update': _McpSpec(
        'Update newsletter list settings',
        'Patches name, origins, redirect URL, or Turnstile keys.',
        'Updates newsletter row. `turnstile_secret` is write-only.',
    ),
    'api_v1_newsletters_destroy': _McpSpec(
        'Delete a newsletter list',
        'Deletes the list, subscribers, and confirmation template.',
        'Removes newsletter data.', destructive=True,
    ),
    'api_v1_newsletters_rotate_key': _McpSpec(
        'Rotate newsletter public sign-up key',
        'Issues a new `public_key` embedded in sign-up URLs.',
        'Updates the list key.',
        notes='Existing embedded forms with the old key stop working.',
    ),
    'api_v1_newsletters_subscribers_list': _McpSpec(
        'List newsletter subscribers',
        'Paginated subscribers with optional `status` filter and exact `email` lookup.',
        'Read-only.',
    ),
    'api_v1_newsletters_subscribers_create': _McpSpec(
        'Add a newsletter subscriber',
        'Adds an address with `confirm` (sends confirmation email) or `consented` (already opted in).',
        'Inserts or updates subscriber row.', sends_email=True,
        notes='`confirm` sends mail. `consented` does not.',
    ),
    'api_v1_newsletters_subscribers_destroy': _McpSpec(
        'Remove a newsletter subscriber',
        'Deletes the subscriber record so the address can sign up again.',
        'Removes subscriber row.', destructive=True,
    ),
    'api_v1_newsletters_subscribers_export': _McpSpec(
        'Export newsletter subscribers as CSV',
        'Returns `text/csv` with email and metadata columns, optionally filtered by `status`.',
        'Read-only export.',
    ),
    'api_v1_newsletters_subscribers_import': _McpSpec(
        'Import newsletter subscribers from CSV',
        '`confirm` (default) sends each new address a confirmation email. '
        '`consented` marks addresses confirmed without mail. Unsubscribed rows are skipped.',
        'Bulk insert or update subscriber rows.', sends_email=True,
        notes='Only `consented` when recipients already agreed to receive mail.',
    ),
    'api_v1_actions_events_list': _McpSpec(
        'List Shellui Actions event types',
        'Returns domain event ids, labels, and descriptions you can subscribe to with webhook rules.',
        'Read-only.',
    ),
    'api_v1_actions_rules_list': _McpSpec(
        'List Shellui Actions webhook rules',
        'Each rule posts signed HTTPS webhooks when an email domain event fires. '
        'Secrets are write-only except immediately after create or rotate.',
        'Read-only.',
    ),
    'api_v1_actions_rules_create': _McpSpec(
        'Create a Shellui Actions webhook rule',
        'Creates an HTTPS webhook rule for a registered `event_type`. '
        'Returns the signing `secret` once in the response body.',
        'Inserts rule row and stores encrypted secret.',
        notes='Store `secret` before closing the response. URL must be publicly reachable unless staff SSRF override is enabled.',
    ),
    'api_v1_actions_rules_retrieve': _McpSpec(
        'Read one Shellui Actions webhook rule',
        'Returns rule metadata and masked secret hints. Plaintext `secret` is omitted.',
        'Read-only.',
    ),
    'api_v1_actions_rules_partial_update': _McpSpec(
        'Update a Shellui Actions webhook rule',
        'Patches name, description, enabled flag, URL, or newsletter filter.',
        'Updates rule configuration.',
    ),
    'api_v1_actions_rules_destroy': _McpSpec(
        'Delete a Shellui Actions webhook rule',
        'Removes the rule. In-flight deliveries may still complete.',
        'Deletes the rule row.', destructive=True,
    ),
    'api_v1_actions_rules_rotate_secret': _McpSpec(
        'Rotate a webhook signing secret',
        'Generates a new HMAC secret and returns it once in the response body.',
        'Updates encrypted secret on the rule.',
        notes='Update your receiver before relying on the new secret.',
    ),
    'api_v1_actions_rules_send_test': _McpSpec(
        'Queue a sample webhook delivery',
        'Enqueues a synthetic domain event so the rule fires against its configured URL.',
        'Inserts an outbox row for delivery workers.', sends_email=False,
        notes='Does not send email. Delivers an HTTPS webhook only.',
    ),
    'api_v1_actions_deliveries_list': _McpSpec(
        'List recent webhook deliveries',
        'Returns delivery status, attempt counts, and errors for the company.',
        'Read-only.',
        notes='Staff may filter by `scheduled_job_run_id` (not exposed on MCP).',
    ),
    'api_v1_actions_deliveries_retrieve': _McpSpec(
        'Read one webhook delivery',
        'Returns attempts, HTTP statuses, and the signed envelope payload.',
        'Read-only.',
    ),
    'api_v1_actions_deliveries_requeue': _McpSpec(
        'Requeue a failed webhook delivery',
        'Resets a failed or dead delivery to pending for another attempt.',
        'Updates outbox status and schedule.',
    ),
    'api_v1_actions_event_log_list': _McpSpec(
        'List recent domain events for the company',
        'Returns the latest email domain events stored for auditing and Actions rules.',
        'Read-only.',
        notes='Query `scope=platform` is staff-only and not an MCP tool.',
    ),
    'api_v1_actions_event_log_retrieve': _McpSpec(
        'Read one domain event log entry',
        'Returns full event payload for a company or platform (staff) row.',
        'Read-only.',
    ),
    'api_v1_actions_event_log_types_list': _McpSpec(
        'List domain event type ids',
        'Returns event type strings valid for log filters and Actions rules.',
        'Read-only.',
    ),
    'api_v1_actions_event_log_retention_retrieve': _McpSpec(
        'Read event log retention settings',
        'Returns configured retention days for company event log rows.',
        'Read-only.',
    ),
}
