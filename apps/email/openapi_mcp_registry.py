"""Agent-facing copy for MCP operations. Consumed by ``openapi_admin.mcp_openapi_fields``.

Each value is the OpenAPI summary and description, and the MCP tool description.
One or two sentences: what the tool does, the arguments that matter, and what it returns.
"""

from __future__ import annotations

# Keys must match ``MCP_REQUIRED_OPERATION_IDS`` in ``apps.authapi.mcp_openapi``.
MCP_DESCRIPTIONS: dict[str, str] = {
    'api_v1_catalog_retrieve': (
        'Returns the Shellui catalog events a company can edit, with variables, suggested subjects, and default designs. '
        'Built-in auth events that companies cannot edit are omitted.'
    ),
    'api_v1_library_list': (
        'Returns built-in Shellui designs and this company\'s library templates, grouped by set. '
        'Each row includes the name, subject, and rendered HTML.'
    ),
    'api_v1_library_create': (
        'Creates a company library design from `name`, or copies one when `source_id` is set. '
        'Returns the new design, including its id and rendered HTML.'
    ),
    'api_v1_library_retrieve': (
        'Returns one library design for `library_id`, including the editor document, theme, variables, and rendered HTML.'
    ),
    'api_v1_library_update': (
        'Updates name, subject, preheader, document, or theme on the company library design `library_id`, and returns the saved design. '
        'Built-in Shellui designs reject the write.'
    ),
    'api_v1_library_destroy': (
        'Deletes the company library design `library_id` when no rule or copy uses it, and returns an empty result. '
        'A design still in use returns 409 template_in_use.'
    ),
    'api_v1_templates_list': (
        'Returns this company\'s event and broadcast template copies. '
        'Optional `event_type` limits the list to one catalog event.'
    ),
    'api_v1_templates_retrieve': (
        'Returns metadata for template copy `template_id`, including template_key, kind, language, and active_version. '
        'Editor JSON is on the version tools.'
    ),
    'api_v1_templates_destroy': (
        'Deletes template copy `template_id` when no rule or broadcast uses it, and returns an empty result. '
        'A copy still in use returns 409 template_in_use.'
    ),
    'api_v1_template_versions_list': (
        'Returns every draft and published version of template `template_id`, including document, translations, theme, and state.'
    ),
    'api_v1_template_versions_create': (
        'Creates a draft version of template `template_id` from the latest version, or from `library_id` when that is sent. '
        'Returns the new version number and state draft.'
    ),
    'api_v1_template_versions_retrieve': (
        'Returns version `version_number` of template `template_id`, including document, translations, theme, and publish state.'
    ),
    'api_v1_template_versions_publish': (
        'Publishes draft `version_number` of template `template_id` after link and placeholder checks, and sets it as active_version. '
        'Returns the version number, state, and checksum.'
    ),
    'api_v1_templates_render_preview': (
        'Renders HTML and plain text for template `template_id` with placeholders filled, and does not send mail. '
        'Omit `document` to use the newest draft, or send `document` and `variables` to preview unsaved JSON.'
    ),
    'api_v1_templates_send_test': (
        'Queues one test message for template `template_id` to the caller address, or to `to` when the caller is Shellui staff, and returns the provider send status. '
        'Use render preview when you only need HTML.'
    ),
    'api_v1_rules_list': (
        'Returns this company\'s email rules, each binding an event_type to a template and recipients, with optional `service` to limit by catalog service. '
        'Built-in auth rules are included and cannot be deleted or disabled.'
    ),
    'api_v1_rules_create': (
        'Creates an email rule for `event_type`, a template copy from `content.library_id`, and the first published version. '
        'Returns the rule, including its id, recipients, and template_id.'
    ),
    'api_v1_rules_retrieve': (
        'Returns email rule `rule_id`, including recipients, enabled state, language, and template_id.'
    ),
    'api_v1_rules_partial_update': (
        'Updates `enabled`, `recipient_mode`, `static_recipients`, `language`, or `newsletter_id` on email rule `rule_id`, and returns the saved rule. '
        'Built-in auth rules reject changes to enabled or recipients.'
    ),
    'api_v1_rules_destroy': (
        'Deletes a company email rule `rule_id` and its template copy, and returns an empty result. '
        'Built-in auth rules cannot be deleted.'
    ),
    'api_v1_provider_retrieve': (
        'Returns this company\'s mail provider, from addresses, and whether credentials are set. '
        'Secrets and passwords are not included.'
    ),
    'api_v1_provider_update': (
        'Replaces this company\'s mail provider (`resend`, `ses`, or `smtp`), from addresses, and credentials, and returns the saved settings without secrets. '
        'Omitted optional fields keep their stored values.'
    ),
    'api_v1_provider_test_send': (
        'Queues one test message through the saved provider to the caller address, or to `to` when the caller is Shellui staff, and returns the provider status. '
        'Missing credentials return 400 provider_not_configured.'
    ),
    'api_v1_auth_link_hosts_retrieve': (
        'Returns the company auth-link host allowlist plus platform and application hosts used to validate magic-link URLs in templates.'
    ),
    'api_v1_auth_link_hosts_update': (
        'Replaces the company auth-link host allowlist with `hosts`, and returns the saved hosts plus platform and application hosts. '
        'Removing a host makes template links on that host fail checks.'
    ),
    'api_v1_stats_retrieve': (
        'Returns delivery counts for this company by day, lane, and event type. '
        'Optional `from` and `to` are ISO dates, and `lane` and `event_type` filter the window.'
    ),
    'api_v1_suppressions_list': (
        'Returns this company\'s suppressed addresses, masked, with reason and lanes.'
    ),
    'api_v1_suppressions_create': (
        'Blocks future mail to `email` for the lanes in `lanes` (every lane when omitted) and returns the new suppression id and masked address. '
        '`reason` defaults to manual.'
    ),
    'api_v1_suppressions_destroy': (
        'Deletes suppression `suppression_id` so the address can receive mail again, and returns an empty result.'
    ),
    'api_v1_messages_list': (
        'Returns recent outbound messages for this company, with masked recipients and status. '
        'Optional filters are `status`, `lane`, `template_key`, and `limit` (default 50, max 200).'
    ),
    'api_v1_messages_retrieve': (
        'Returns message `message_id`, including lane, provider, status timeline, and template key. '
        'The recipient address is not included.'
    ),
    'api_v1_messages_cancel': (
        'Cancels queued or retrying message `message_id`, clears stored variables, and returns the id and status cancelled. '
        'A message that already left the queue returns 409 message_not_cancellable.'
    ),
    'api_v1_broadcasts_list': (
        'Returns this company\'s broadcast drafts and sent campaigns, with audience summary and delivery counts.'
    ),
    'api_v1_broadcasts_create': (
        'Creates a broadcast draft named `name` from library `source_key`, with optional `language` and `audience`, and returns the draft including its id and template_id.'
    ),
    'api_v1_broadcasts_retrieve': (
        'Returns broadcast `broadcast_id`, including audience, template, counts, state, and sender configuration.'
    ),
    'api_v1_broadcasts_partial_update': (
        'Updates `name` or `audience` on broadcast draft `broadcast_id`, and returns the saved broadcast. '
        'A broadcast that has started sending returns 409 broadcast_not_editable.'
    ),
    'api_v1_broadcasts_destroy': (
        'Deletes broadcast draft `broadcast_id` and its template copy, and returns an empty result. '
        'A broadcast that is sending or already sent returns 409 broadcast_not_deletable.'
    ),
    'api_v1_broadcasts_preview': (
        'Counts sendable, unsubscribed, and suppressed recipients for broadcast `broadcast_id` without sending mail. '
        'Optional `audience` overrides the saved audience for this count.'
    ),
    'api_v1_broadcasts_send': (
        'Queues delivery of broadcast `broadcast_id` to its audience through the company bulk sender, and returns the broadcast state. '
        'A broadcast that cannot be sent returns 409.'
    ),
    'api_v1_newsletters_list': (
        'Returns this company\'s newsletter lists, with public keys, subscriber counts, and sign-up URLs.'
    ),
    'api_v1_newsletters_create': (
        'Creates a newsletter list named `name`, with its confirmation template and sign-up URL, and returns the list including its id and public_key.'
    ),
    'api_v1_newsletters_retrieve': (
        'Returns newsletter list `newsletter_id`, including settings, Turnstile flags, subscriber counts, and sign-up URL.'
    ),
    'api_v1_newsletters_partial_update': (
        'Updates name, description, language, origins, redirect URL, or Turnstile keys on newsletter `newsletter_id`, and returns the saved list. '
        '`turnstile_secret` is write-only.'
    ),
    'api_v1_newsletters_destroy': (
        'Deletes newsletter list `newsletter_id`, its subscribers, and its confirmation template, and returns an empty result.'
    ),
    'api_v1_newsletters_rotate_key': (
        'Issues a new public_key for newsletter `newsletter_id` and returns the list. '
        'Sign-up URLs that still use the old key stop working.'
    ),
    'api_v1_newsletters_subscribers_list': (
        'Returns a page of subscribers for newsletter `newsletter_id`. '
        'Optional `status` filters by pending, confirmed, or unsubscribed, and `email` looks up one address.'
    ),
    'api_v1_newsletters_subscribers_create': (
        'Adds `email` to newsletter `newsletter_id` and returns the outcome and subscriber. '
        '`mode` confirm sends a confirmation email, and consented records an existing opt-in without mail.'
    ),
    'api_v1_newsletters_subscribers_destroy': (
        'Deletes subscriber `subscriber_id` from newsletter `newsletter_id` so the address can sign up again, and returns an empty result.'
    ),
    'api_v1_newsletters_subscribers_export': (
        'Returns subscribers of newsletter `newsletter_id` as CSV text in `csv`, with content_type text/csv. '
        'Optional `status` limits the export.'
    ),
    'api_v1_newsletters_subscribers_import': (
        'Imports subscribers into newsletter `newsletter_id` from CSV text in `csv`, and returns counts of added, existing, invalid, and skipped rows. '
        '`mode` confirm emails each new address, and consented marks them confirmed without mail.'
    ),
    'api_v1_actions_events_list': (
        'Returns Shellui Actions event ids a webhook rule can subscribe to, with a label and description for each.'
    ),
    'api_v1_actions_rules_list': (
        'Returns this company\'s Shellui Actions webhook rules. Signing secrets are masked.'
    ),
    'api_v1_actions_rules_create': (
        'Creates a Shellui Actions webhook rule for `event_type`, `name`, and HTTPS `url`, and returns the rule with `secret` masked. '
        'Read the plaintext secret from the HTTP API, not from this tool.'
    ),
    'api_v1_actions_rules_retrieve': (
        'Returns Shellui Actions webhook rule `rule_id`, including URL and enabled state. The signing secret is masked.'
    ),
    'api_v1_actions_rules_partial_update': (
        'Updates name, description, enabled, URL, or newsletter filter on Shellui Actions webhook rule `rule_id`, and returns the saved rule.'
    ),
    'api_v1_actions_rules_destroy': (
        'Deletes Shellui Actions webhook rule `rule_id` and returns an empty result. '
        'A delivery already in flight may still finish.'
    ),
    'api_v1_actions_rules_rotate_secret': (
        'Replaces the signing secret on Shellui Actions webhook rule `rule_id` and returns the rule with `secret` masked. '
        'The previous secret stops verifying immediately.'
    ),
    'api_v1_actions_rules_send_test': (
        'Queues a sample Shellui Actions delivery to the URL on rule `rule_id`, and returns status queued. '
        'This sends an HTTPS webhook, not email.'
    ),
    'api_v1_actions_deliveries_list': (
        'Returns recent Shellui Actions webhook deliveries for this company, with status, attempt counts, and last error.'
    ),
    'api_v1_actions_deliveries_retrieve': (
        'Returns Shellui Actions delivery `delivery_id`, including attempts, HTTP statuses, and the signed envelope.'
    ),
    'api_v1_actions_deliveries_requeue': (
        'Queues another attempt for failed or dead Shellui Actions delivery `delivery_id`, and returns the delivery with status pending.'
    ),
    'api_v1_actions_event_log_list': (
        'Returns recent email domain events stored for this company, including event_type, data, and created_at.'
    ),
    'api_v1_actions_event_log_retrieve': (
        'Returns one domain event log row `event_id`, including the full event payload.'
    ),
    'api_v1_actions_event_log_types_list': (
        'Returns the Shellui Actions event type ids that are valid for log filters and webhook rules.'
    ),
    'api_v1_actions_event_log_retention_retrieve': (
        'Returns how many days this company keeps domain event log rows.'
    ),
}
