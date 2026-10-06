# Integration contract

This page is the contract for identity-service, storage-service, hosting-service, and the Shellui admin app. Implement against these paths, fields, and error codes. Do not infer a second shape from another service.

Base URL: `https://email.shellui.com`

OpenAPI (generated from the running service): `GET /api/schema/`, Swagger at `/api/docs/`, ReDoc at `/api/docs/redoc/`. When this document and the schema disagree, this document is the one sibling repos should follow until both are updated together.

## Environment variables (callers)

| Variable | Default | Use |
| --- | --- | --- |
| `EMAIL_SERVICE_URL` | `https://email.shellui.com` | Origin only. Callers append paths such as `/api/v1/send`. No trailing slash required. Local Compose is `http://localhost:8003`. |
| `EMAIL_SERVICE_API_KEY` | none | Service key issued by email-service. Prefix `esk_`. Send as `Authorization: Bearer <key>`. |

Store the key in the caller's secret store. email-service stores only a SHA-256 hash and a 12-character prefix. The plaintext is returned once, from `POST /api/v1/service-clients` or `manage.py create_service_key`.

## Two ways to send

Sibling services do not share an email client today. This service therefore exposes two entry points.

**Direct send** (`POST /api/v1/send`) is for mail the caller has already decided to send. Identity uses it for magic links and invitations. Those templates are on the `auth` lane, with a TTL and idempotency. A company email rule does not suppress a direct send.

**Event ingest** (`POST /api/v1/events`) is for catalog events. The caller posts `service`, `event_type`, `company_id`, a payload, and recipient hints. email-service queues one message for every enabled email rule on that company, service, and event. Auth-lane events have a built-in rule that cannot be turned off, so sign-in mail still sends when the event is posted. If no enabled rule matches, the response is `202` with `skipped_reason: no_rule`. `default_enabled` on the catalog is metadata only. It does not send mail.

Recommended key scopes:

| Service | `allowed_lanes` | `allowed_template_prefixes` |
| --- | --- | --- |
| identity | `auth`, `transactional` | `identity.` |
| storage | `transactional` | `storage.` |
| hosting | `transactional` | `hosting.` |

A key that is not allowed to use a lane receives `403 lane_not_allowed`. A key whose prefixes do not match the template key receives `403 forbidden`.

## Authentication

`Authorization: Bearer <credential>`

| Credential | Who | What they can call |
| --- | --- | --- |
| `esk_` service key | A Shellui service | `/send`, `/send/batch`, `/events`, `/messages/{id}`, `/catalog`, and identity-only `/privacy/erase` |
| Identity JWT (RS256, JWKS) | Staff, or a company owner | Admin routes below. `company_id` on the token must match the requested company unless the caller is staff. |

Health is public. Provider webhooks use Svix signatures, not a Bearer token. Metrics require a JWT (same pattern as storage-service). Service keys cannot read metrics.

See [authentication.md](authentication.md).

## Errors

JSON errors never contain translated sentences. Shape:

```json
{
  "error_code": "validation_failed",
  "field_errors": {
    "to": ["required"]
  },
  "request_id": "optional"
}
```

`field_errors` and `request_id` are omitted when empty.

| `error_code` | HTTP | Meaning |
| --- | --- | --- |
| `unauthorized` | 401 | Missing or invalid credential, or a bad provider webhook signature |
| `forbidden` | 403 | Authenticated, but not allowed for this company, template, or recipient |
| `company_mismatch` | 403 | Token or key `company_id` does not match the request |
| `lane_not_allowed` | 403 | Service key cannot use this lane |
| `validation_failed` | 400 | See `field_errors` |
| `template_not_found` | 404 | Unknown `template_key`, template id, or version number |
| `language_not_available` | 400 | Language is not `en` or `fr` for that template |
| `unknown_event` | 400 | `event_type` on `POST /api/v1/events` is not in the catalog |
| `event_unknown` | 400 | `event_type` on the rules API or `GET /api/v1/templates` is not in the catalog, or `service` does not own that event |
| `library_not_found` | 404 | Library id is not a built-in or one of this company's templates |
| `library_built_in` | 409 | `PUT` or `DELETE` on a built-in library template |
| `library_not_synced` | 503 | An event's default design is missing because migrations have not run |
| `renderer_unavailable` | 503 | Node or `renderer/compose.mjs` could not run, or timed out |
| `rule_built_in` | 409 | A built-in auth rule cannot be deleted, disabled, or sent to other recipients |
| `auth_event_rule_forbidden` | 400 | A company rule on an auth-lane event |
| `auth_single_recipient` | 400 | An auth-lane `/send` with more than one `to`, or an auth-lane event with more than one recipient |
| `auth_link_misplaced` | 400 | An auth-lane copy puts a link variable outside a link target or visible text, for example in an image `src`, a `style`, or an `alt` |
| `template_in_use` | 409 | `DELETE /api/v1/templates/{id}` while an email rule still points at that copy |
| `template_lane_mismatch` | 400 | Requested lane does not match the template's lane class |
| `lane_requires_campaign` | 400 | `bulk` is not accepted on `/send`. Campaigns are not in this version. |
| `lane_paused` | 409 | Staff paused the lane for every company, or this company's provider returned 401/403. Retry later. |
| `idempotency_conflict` | 409 | Same `idempotency_key`, different body |
| `recipient_invalid` | 400 | Address failed the format check |
| `recipient_suppressed` | 422 | Auth lane, hard bounce. Do not retry. |
| `recipient_rate_limited` | 429 | Auth lane, 5 messages per recipient per company per 10 minutes |
| `company_rate_limited` | 429 | Transactional lane, 1000 messages per company per hour. Auth lane, 30 messages per company per 60 seconds (`EMAIL_COMPANY_AUTH_LIMIT`). |
| `variable_url_not_allowed` | 400 | URL variable is not `https`, `mailto`, or `tel`, or the host is not on `EMAIL_AUTH_LINK_HOSTS` |
| `unsubscribe_link_missing` | 400 | A bulk template publish omitted `{{ system.unsubscribe_url }}` |
| `provider_not_configured` | 409 | No usable provider credentials for this send |
| `platform_sender_not_allowed` | 409 | Non-auth mail for a company with no provider, and the company is not listed in `EMAIL_PLATFORM_COMPANY_IDS`. Auth mail still uses the platform fallback. The same code is HTTP 403 when a company sets `from_email` to the platform From address. |
| `company_smtp_disabled` | 400 or 409 | Company SMTP is off (`EMAIL_ALLOW_COMPANY_SMTP` defaults to false). 400 when saving the provider, 409 when a send is refused. |
| `provider_host_not_public` | 400 | Company SMTP host is missing, private, or not a public address |
| `auth_link_missing` | 400 | An auth-lane copy dropped a required link variable such as `magic_link_url` |
| `auth_link_host_not_allowed` | 400 | An auth-lane link (link mark, button, or linked image) is not an allowlisted `https` host, a declared URL variable, or `system.message_id` |
| `auth_literal_link` | 400 | An auth-lane copy has a literal URL in the subject, preheader, or document text |
| `provider_not_available` | 400 | Provider name is not `resend` or `smtp` |
| `provider_test_failed` | 502 | The test send was refused by the provider |
| `message_not_found` | 404 | Unknown message id |
| `message_not_cancellable` | 409 | Status is not `queued` or `retrying` |
| `not_found` | 404 | Other missing rows |
| `method_not_allowed` | 405 | Wrong HTTP method |
| `conflict` | 409 | Generic DRF conflict mapped by the exception handler |
| `rate_limited` | 429 | Generic DRF throttle mapped by the exception handler |
| `request_failed` | other | Unmapped handler error |

The design names `template_not_published` and `campaign_state_invalid` are not emitted. A direct send with no published copy uses the event's default library design. Campaigns are not implemented, so bulk sends return `lane_requires_campaign`.

## Idempotency

Optional string field `idempotency_key` on `/send`, `/send/batch`, and `/events`.

- Unique per service key's service name, `company_id`, and key.
- The service stores a hash of the JSON body. The same key and the same body within 24 hours (`EMAIL_IDEMPOTENCY_HOURS`) returns the stored response with `idempotent_replay: true`.
- The same key and a different body returns `409 idempotency_conflict`.
- Keys are not shared across services.

Callers that retry network failures must send the same key and the same body.

## Retries (callers)

Retry with the same `idempotency_key` and the same JSON body. Backoff matches Shellui Actions webhook delivery in identity, storage, and hosting: **30 seconds times 2^(attempt-1)**, capped at **1 hour**, up to **8** attempts. For `429` and `503`, wait `Retry-After` when that header is present, still capped at 1 hour.

| Result | What the caller does |
| --- | --- |
| 2xx | Done. `202` with `skipped_reason` (`no_rule` or `no_recipients`) is finished. Do not retry it. |
| 404, 408, 409, 425, 429, any other 4xx not in the next row, 5xx, timeouts, connection errors | Retry |
| 400, 401, 403, 405, 410, 413, 422 | Permanent. Do not retry that body. |

`404` is retryable. Sibling webhook delivery retries `404` so an inactive n8n workflow can start later. On this API, `404` means `template_not_found`, `message_not_found`, or `not_found`.

`409` is retryable. `lane_paused` clears when the lane resumes. `idempotency_conflict` means this key was already stored with a different body. Send the original body, or use a new key.

`422` is permanent. For auth mail it is `recipient_suppressed`.

`5xx` (including `502 provider_test_failed`) and connection errors are retryable. The event was not accepted.

Workers inside email-service retry provider failures on their own. Callers do not poll `/send` to force a provider retry.

## Direct send

`POST /api/v1/send`

Auth: service key.

```json
{
  "company_id": 42,
  "template_key": "identity.auth.magic_link.requested",
  "language": "en",
  "idempotency_key": "magic-link-42-user-7-token-id",
  "ttl_seconds": 120,
  "to": [
    {"email": "ada@acme.com", "user_id": 7}
  ],
  "variables": {
    "company_name": "Acme",
    "magic_link_url": "https://id.shellui.com/api/v1/magic-link/verify?token=example",
    "recipient_name": "Ada"
  }
}
```

| Field | Required | Notes |
| --- | --- | --- |
| `company_id` | yes | Integer. Must be allowed by the key. |
| `template_key` | yes | Catalog key. See [events.md](events.md). |
| `language` | no | `en` (default) or `fr`. Unknown language: `language_not_available`. |
| `lane` | no | Must match the template lane class. Omit it. |
| `ttl_seconds` | no | Auth lane only in practice. Default 120 for magic links, 300 for invitations. Capped at `EMAIL_AUTH_MAX_TTL_SECONDS` (300). |
| `idempotency_key` | recommended | See above. |
| `to` | yes | 1 to 50 recipients. `user_id` is optional. |
| `variables` | yes for required tokens | Max 8192 bytes of JSON. Unknown tokens are dropped. `system.*` is reserved. |

`202` response:

```json
{
  "idempotent_replay": false,
  "messages": [
    {
      "id": "msg_<32 hex chars>",
      "to": "ada@acme.com",
      "status": "queued",
      "lane": "auth",
      "template_version": null,
      "language": "en",
      "expires_at": "2026-10-02T12:02:00Z"
    }
  ]
}
```

`to` on this response is the address you submitted. Admin list endpoints mask it. `template_version` is `null` when the event's default library design is used, or the published version number when a company copy sends.

With `EMAIL_DELIVER_SYNC=true` (tests and local only), `status` may already be `sent`, `failed`, or `expired`. Production workers deliver asynchronously. Poll `GET /api/v1/messages/{id}` or subscribe to Shellui Actions.

Identity must call this endpoint for:

- `identity.auth.magic_link.requested` (`ttl_seconds` 120 unless the product needs a shorter life)
- `identity.user.invited` (`ttl_seconds` up to 300)
- `identity.auth.magic_link.staff_blocked` (`ttl_seconds` up to 300), see [Built-in auth emails](#built-in-auth-emails)

Do not put magic-link tokens in logs. The service encrypts variables until the provider accepts the message, then deletes them. Rendered HTML is not stored on the message row.

### Built-in auth emails

Some auth emails only use copy that Shellui writes, in English and French (`apps/email/builtin_auth.py`). Their definition in `apps/email/catalog.py` has `company_editable: false`. A company cannot edit them or pick another design, they get no built-in rule, `POST /api/v1/rules` refuses them (`400 auth_event_rule_forbidden`), `POST /api/v1/events` never sends them (`skipped_reason: no_rule`), and `GET /api/v1/catalog` does not list them. Direct `/send` takes exactly one `to` address, under the same auth-lane rate limits and suppression rules as magic links.

`identity.auth.magic_link.staff_blocked` is the only one today. Identity sends it instead of a magic link when a staff account (`is_staff` or `is_superuser`) asks for one, so the person knows why no link arrived. It carries no sign-in link or token. Variables:

| Variable | Required | Content |
| --- | --- | --- |
| `company_name` | yes | Company the request was for. Filled from the stored name when omitted |
| `sign_in_url` | no | Plain link to the sign-in page the request came from (`https`, or `http://localhost` when `DEBUG=true`). Not a credential. Without it, the email has no button |

English copy: subject `[Shellui] Sign in to {{ company_name }} with your password or SSO`, heading "No sign-in link for staff accounts", one paragraph (`Someone asked for a sign-in link for this address on {{ company_name }}. For security, staff accounts can't sign in with an email link. Sign in with your password or SSO instead. If you didn't ask for this, you can ignore this email.`), and a "Go to sign-in" button to `sign_in_url` when it is set.

```json
{
  "company_id": 42,
  "template_key": "identity.auth.magic_link.staff_blocked",
  "language": "en",
  "ttl_seconds": 300,
  "to": [{"email": "ada@shellui.com"}],
  "variables": {"company_name": "Acme", "sign_in_url": "https://app.acme.com/"}
}
```

### Batch

`POST /api/v1/send/batch`

Same auth and lane rules. Up to 500 items. One bad item does not fail the batch.

```json
{
  "company_id": 42,
  "template_key": "identity.user.invitation_revoked",
  "language": "en",
  "idempotency_key": "batch-example",
  "items": [
    {
      "to": {"email": "ada@acme.com"},
      "variables": {"company_name": "Acme", "recipient_email": "ada@acme.com"}
    }
  ]
}
```

`202`:

```json
{
  "idempotent_replay": false,
  "accepted": [{"index": 0, "id": "msg_<hex>", "status": "queued"}],
  "rejected": [{"index": 1, "error_code": "recipient_invalid"}]
}
```

Auth-lane suppression on `/send` (not batch) fails the whole request with `422`. On batch, a suppressed recipient is a row in `rejected`.

## Event ingest

`POST /api/v1/events`

Auth: service key. `service` must equal the key's service name.

```json
{
  "service": "hosting",
  "event_type": "hosting.deployment.failed",
  "company_id": 42,
  "language": "en",
  "idempotency_key": "hosting-deployment-99-failed",
  "payload": {
    "display_name": "My App",
    "app_version": "1.2.0",
    "error": "artifact_extract_failed"
  },
  "recipients": [
    {"email": "ada@acme.com", "user_id": 7}
  ]
}
```

On accept, email-service creates any missing built-in auth rules for that company, then loads every enabled rule for the company, service, and `event_type`. Each rule queues one message per resolved recipient. An auth-lane event only uses its built-in rule, sends to the event's recipient, and takes exactly one (`400 auth_single_recipient`). A company can have several rules on one event (the event recipient and a static ops address, for example). There is no platform rule and no catalog on/off fallback.

When no enabled rule matches, `202`:

```json
{
  "idempotent_replay": false,
  "rule_enabled": false,
  "skipped_reason": "no_rule",
  "messages": []
}
```

`skipped.no_rule` counts that row. Catalog `default_enabled` is not consulted. Events that used to send with no company row (`hosting.deployment.failed`, `identity.user.invitation_revoked`, `identity.scim.provisioning_conflict`) now return `no_rule` until the company creates a rule. Auth-lane events send because their built-in rules exist. See [rules.md](rules.md) and [events.md](events.md).

When at least one enabled rule has a recipient, `202`:

```json
{
  "idempotent_replay": false,
  "rule_enabled": true,
  "messages": []
}
```

`messages` uses the same object as `/send`. One entry per queued message. An empty `messages` array with `rule_enabled: true` and no `skipped_reason` means every recipient was suppressed on a non-auth lane.

When enabled rules exist but no address resolves, `202` (not `400`). This check happens before the provider check, so a company with no provider still gets this response:

```json
{
  "idempotent_replay": false,
  "rule_enabled": true,
  "skipped_reason": "no_recipients",
  "messages": []
}
```

That covers `recipients: []`, a missing `recipients` field, and a `static` rule whose `static_recipients` list is empty, when those are the only enabled rules. The event is counted under `skipped.no_recipients`. Callers should treat this `202` as finished. Do not retry it. A non-list `recipients` value is still `400 validation_failed`.

One `idempotency_key` covers the whole event, including every rule's messages. A retry with the same key and body returns the stored response and does not send again, even if a rule was added later.

Recipient selection, per rule:

| `recipient_mode` | Behavior |
| --- | --- |
| `hints` (default) | Use the `recipients` array on the request. |
| `static` | Ignore request recipients. Use `static_recipients` on the rule. Stored values are email strings. Input may be a string or `{email}`. |

Language, per message: the rule `language` when it is set, otherwise the recipient `language`, then the request `language`, then `en`. An unedited catalog subject and preheader still follow that language. Once the company edits them, the stored text is what sends. The body is always the copy as stored.

`payload` keys match the template variables. Extra keys are ignored. For `hosting.deployment.failed` the failure text is `error` (for example `artifact_extract_failed`), the same field hosting stores on its webhook payload.

`company_name` may be omitted. email-service fills it before validation, in this order:

1. `company_name` on this request, when it is non-empty. It is stored for later mail only when the caller is the identity service key, or a service key whose `allowed_company_ids` is exactly this company. Any other caller, including a hosting or storage key that can address every company, uses the name for this message only.
2. A `company_name` stored from an earlier allowed caller for the same `company_id` (identity mail usually does this).
3. The company provider `from_name`, when the provider is configured and `from_name` is not the platform default (`Shellui`).
4. Otherwise the variable is left unset and renders empty, unless the copy gives it a default (`{{ company_name|default:"your company" }}`).

Hosting and storage do not need to send `company_name`. Identity still may. Auth templates that require `company_name` (`identity.auth.magic_link.requested`, `identity.user.invited`, `identity.user.invitation_revoked`) succeed without it once a name is stored. If none of the sources above has a name, those templates still return `validation_failed` with `variables.company_name: ["required"]`. Other required variables are unchanged.

email-service does not call identity's company API. That API is limited to members of the company, and a service key is not a member.

URL variables must be `https`, `mailto`, or `tel`. `http://localhost` and `http://127.0.0.1` are allowed only when `DEBUG=true`. Auth URL variables (`magic_link_url`) must use a host in `EMAIL_AUTH_LINK_HOSTS`. When `DEBUG=false`, `localhost`, `127.0.0.1`, and `::1` are removed from that list even if the environment includes them.

Non-auth mail (`POST /api/v1/send` on a transactional template, and `POST /api/v1/events` when an enabled rule matches and a recipient resolves) requires a company provider, unless `company_id` is in `EMAIL_PLATFORM_COMPANY_IDS`. Otherwise the response is `409 platform_sender_not_allowed`. Identity auth templates (`identity.auth.magic_link.requested`, `identity.user.invited`) still send through the platform fallback when the company has no provider, from `no-reply@shellui.com`. That includes event ingest of those auth events through the built-in rule.

A company that is not in `EMAIL_PLATFORM_COMPANY_IDS` cannot set `from_email` or `bulk_from_email` to `DEFAULT_FROM_EMAIL` or `BULK_FROM_EMAIL` (`403 platform_sender_not_allowed`).

Auth-lane copies must still contain every required URL variable (`magic_link_url` or `invitation_url`). Every link in the document (link marks, button `href`, linked images) must be a declared URL variable, `{{ system.message_id }}`, or a literal `https` URL whose host is on `EMAIL_AUTH_LINK_HOSTS`. Otherwise publish returns `auth_link_missing` or `auth_link_host_not_allowed`. Subject, preheader, and document text must not contain a literal URL. The required link variable may appear there. A literal URL returns `auth_literal_link`. A link variable anywhere else in the document (an image `src`, a `style`, an `alt`, any other attribute) returns `auth_link_misplaced`, because a mail client or image proxy would request that URL and hand the link to its host.

## Message status

`GET /api/v1/messages/{id}`

Auth: the service that created it, staff, or the company owner.

```json
{
  "id": "msg_<hex>",
  "company_id": 42,
  "service": "identity",
  "template_key": "identity.auth.magic_link.requested",
  "lane": "auth",
  "status": "sent",
  "provider": "resend",
  "accepted_at": "2026-10-02T12:00:00Z",
  "expires_at": "2026-10-02T12:02:00Z",
  "events": [{"type": "queued", "at": "2026-10-02T12:00:00Z"}]
}
```

Statuses: `queued`, `sending`, `retrying`, `sent`, `delivered`, `delivery_delayed`, `bounced`, `complained`, `failed`, `expired`, `suppressed`, `cancelled`.

`POST /api/v1/messages/{id}/cancel` moves `queued` or `retrying` to `cancelled` and deletes stored variables. Other statuses: `409 message_not_cancellable`.

`GET /api/v1/messages?company_id=42&status=&lane=&template_key=&limit=50`

Auth: staff or company owner. `limit` max 200. Addresses are masked (`a***@acme.com`).

## Catalog

`GET /api/v1/catalog`

Auth: service key, staff, or company owner.

```json
{
  "auth_link_hosts": ["id.shellui.com"],
  "events": [
    {
      "service": "identity",
      "event_type": "identity.auth.magic_link.requested",
      "template_key": "identity.auth.magic_link.requested",
      "label": "Magic link requested",
      "lane_class": "auth",
      "default_lane": "auth",
      "default_enabled": true,
      "category": "auth",
      "default_ttl_seconds": 120,
      "variables": [],
      "link_token": "magic_link_url",
      "default_template": "barebone.activation",
      "rules_allowed": false,
      "suggested": {
        "en": {"subject": "[Shellui] Sign in to {{ company_name }}", "preheader": "Your sign-in link."},
        "fr": {"subject": "[Shellui] Connexion à {{ company_name }}", "preheader": "Votre lien de connexion."}
      }
    }
  ]
}
```

`auth_link_hosts` is the read-only `EMAIL_AUTH_LINK_HOSTS` list. The editor uses it to check a button `href` before publish. `rules_allowed` is `false` for auth-lane events: the rule editor must not offer them, and `POST /api/v1/rules` refuses them. `variables[]` items: `token`, `type` (`string` or `url`), `required`, `description` (an i18n key `email.var.<token>`), `example`, `is_url`, and optionally `sensitive` and `allowed_hosts_setting`. A variable with `allowed_hosts_setting: "EMAIL_AUTH_LINK_HOSTS"` must use one of those hosts.

`link_token` is the URL variable a design's main link (`{{ action_url }}`) becomes on a copy of this event, empty when the event has no link. `default_template` is the library key used for built-in rules and for direct sends before the company has a copy. Designs are on `GET /api/v1/library` (admin JWT).

## Admin: provider

Auth: staff or company owner. `company_id` query parameter, or the token's company.

`GET /api/v1/provider?company_id=42`

```json
{
  "company_id": 42,
  "configured": true,
  "provider": "resend",
  "from_email": "no-reply@acme.com",
  "from_name": "Acme",
  "sending_domain": "acme.com",
  "bulk_from_email": "",
  "credentials_hint": "••••abcd",
  "webhook_configured": false,
  "webhook_hint": "",
  "fallback_provider": "resend",
  "fallback_configured": true,
  "smtp_allowed": false,
  "auth_link_hosts": ["id.shellui.com"]
}
```

`smtp_allowed` is `EMAIL_ALLOW_COMPANY_SMTP` (default false). `auth_link_hosts` is the same list as on `GET /api/v1/catalog`.

When the company has no provider row the same keys are present: `configured` is false, `provider` is null, `credentials_hint`, `from_name`, `sending_domain`, and `webhook_hint` are empty strings, and `webhook_configured` is false. `from_email` and `bulk_from_email` are the platform addresses. The API key is never returned. `configured: false` means auth mail can still use the platform fallback. Non-auth mail then returns `platform_sender_not_allowed` unless this company is in `EMAIL_PLATFORM_COMPANY_IDS`.

`PUT /api/v1/provider?company_id=42`

```json
{
  "provider": "resend",
  "from_email": "no-reply@acme.com",
  "from_name": "Acme",
  "sending_domain": "acme.com",
  "bulk_from_email": "",
  "credentials": {"api_key": "re_company_key"},
  "webhook_secret": "whsec_company_secret"
}
```

SMTP `credentials`: `host`, `port`, `username`, `password`, `use_tls`, `use_ssl`. Company SMTP is rejected with `company_smtp_disabled` unless `EMAIL_ALLOW_COMPANY_SMTP=true`. When it is on, `host` must resolve to a public address (`provider_host_not_public` otherwise). The platform `EMAIL_HOST` relay is separate and is not gated by that flag.

`from_email` is required on every PUT. Omitted `from_name`, `sending_domain`, and `bulk_from_email` keep the stored value. A present empty string clears that field.

Send `credentials` only when they change. If `provider` is unchanged and `credentials` is omitted, the stored secret is kept. A provider change requires `credentials`. Send `webhook_secret` only when it changes. An omitted `webhook_secret` keeps the stored webhook secret.

`POST /api/v1/provider/test-send?company_id=42`

```json
{"to": "ada@acme.com"}
```

Staff may choose `to`. A company owner may only send to the email on their JWT. Response: `{"status": "sent", "provider": "resend", "provider_message_id": "re_example"}`.

Platform fallback (no company row): `EMAIL_FALLBACK_PROVIDER` (`resend` or `smtp`) plus `RESEND_API_KEY` or `EMAIL_HOST` and related SMTP variables.

Who may use it:

| Caller | Lane | Result |
| --- | --- | --- |
| Any company | `auth` | Platform provider and `no-reply@shellui.com` |
| Company id in `EMAIL_PLATFORM_COMPANY_IDS` | any lane the key allows | Platform provider and the platform From for that lane |
| Any other company | `transactional` | `409 platform_sender_not_allowed` |

Set `EMAIL_FALLBACK_PROVIDER=none` to require a company provider for auth mail as well (sends then fail with `provider_not_configured`).

Auth rate limits: 5 messages per recipient per company per 10 minutes (`recipient_rate_limited`), and 30 messages per company per 60 seconds (`company_rate_limited`, `EMAIL_COMPANY_AUTH_LIMIT` and `EMAIL_COMPANY_AUTH_WINDOW_SECONDS`). A provider HTTP 401 or 403 pauses that company's lane only. `POST /api/v1/lanes/{lane}/pause` is still staff-only and pauses the lane for every company.

Default from address when the company has none: `no-reply@shellui.com`. Bulk from address: `news@news.shellui.com`. The HTTP host is `email.shellui.com` and is not a sending domain.

## Admin: library

Auth: staff or company owner. See [library.md](library.md) for the designs and their source.

| Method | Path | Result |
| --- | --- | --- |
| `GET` | `/api/v1/library?company_id=` | `{sets: [{key, name}], templates: […]}`, built-ins first, then this company's templates |
| `POST` | `/api/v1/library?company_id=` | Body `name`, `subject`, `preheader`, `document`, or `source_id` to duplicate another library template. `201` detail |
| `GET` | `/api/v1/library/{id}?company_id=` | Detail |
| `PUT` | `/api/v1/library/{id}?company_id=` | Any of `name`, `subject`, `preheader`, `document`. `409 library_built_in` on a built-in |
| `DELETE` | `/api/v1/library/{id}?company_id=` | `204`. `409 library_built_in` on a built-in. Existing event copies are not affected |

List items: `id`, `key`, `set`, `name`, `built_in`, `company_id` (null on built-ins), `subject`, `preheader`, `html`, `updated_at`. Detail adds `document`, `text`, `head` (the set CSS), and `variables` (`company_name`, `action_url`). Unknown id: `404 library_not_found`.

## Admin: rules

Email rules are a list, like Shellui Actions webhook rules. The old per-event toggle (`template_key`, `customized`, `default_enabled`, `DELETE /api/v1/rules/{event_type}`) is gone. Detail is in [rules.md](rules.md).

`GET /api/v1/rules?company_id=42&service=identity` creates missing built-in auth rules, then returns company rules ordered by `event_type`, then `created_at`. `service` is optional.

```json
{
  "company_id": 42,
  "rules": [
    {
      "id": 9,
      "service": "identity",
      "event_type": "identity.auth.magic_link.requested",
      "enabled": true,
      "recipient_mode": "hints",
      "static_recipients": [],
      "language": "",
      "template_id": 15,
      "built_in": true,
      "created_at": "2026-10-03T12:00:00Z",
      "updated_at": "2026-10-03T12:00:00Z"
    }
  ]
}
```

`language` blank means recipient language, then the event language, then `en`. `recipient_mode` is `hints` or `static`. `static_recipients` in responses is a list of email strings. `built_in` is read-only.

`POST /api/v1/rules?company_id=42` returns the rule at `201`, including `template_id`.

```json
{
  "event_type": "hosting.deployment.failed",
  "service": "hosting",
  "enabled": true,
  "language": "",
  "recipient_mode": "hints",
  "static_recipients": [],
  "content": {"library_id": 3}
}
```

`content.library_id` is a library template the company can see. email-service copies it onto the event and publishes the copy, named after the event label, with a key such as `company.a1b2c3d4e5f6`, the catalog subject and preheader in the rule language, and `{{ action_url }}` replaced by the event's `link_token`. The response `template_id` is that copy. Optional `service` must match the event's owner service. Several rules on one event are allowed, each with its own copy.

`GET /api/v1/rules/{id}?company_id=42` returns one rule. Unknown id: `404 not_found`.

`PATCH /api/v1/rules/{id}?company_id=42` accepts any of `enabled`, `recipient_mode`, `static_recipients`, `language`.

`DELETE /api/v1/rules/{id}?company_id=42` returns `204` and deletes the rule's copy.

| `error_code` | When |
| --- | --- |
| `event_unknown` | Unknown `event_type`, or `service` does not own it |
| `library_not_found` | `content.library_id` is not a built-in or one of this company's templates |
| `rule_built_in` | Delete, or `enabled: false`, on a built-in rule |
| `language_not_available` | `language` is not `en` or `fr` for that event |
| `validation_failed` | `content.library_id` missing (`field_errors.content: ["library_id_required"]`), `recipient_mode` not `hints` or `static`, or `static_recipients` is not a list of addresses |

Built-in rules exist for every catalog event with `lane_class: auth` (`identity.auth.magic_link.requested` and `identity.user.invited` today), except [built-in auth emails](#built-in-auth-emails) such as `identity.auth.magic_link.staff_blocked`, which have no rule and no company copy. They are created on the first rules list or the first event for that company, and a second create is a no-op. They cannot be deleted or disabled. Their copy starts from the event's `default_template` and can be edited or started over from another library template. `enabled: true` on a built-in rule is accepted. Other patch fields (`language`, `recipient_mode`, `static_recipients`) still apply.

## Admin: templates

Company templates here are event copies, one per rule. Detail is in [templates.md](templates.md).

| Method | Path | Result |
| --- | --- | --- |
| `GET` | `/api/v1/templates?company_id=` | Copies. Optional `event_type` keeps only copies whose `{{ token }}` values fit that event. Unknown event: `400 event_unknown`. |
| `GET` | `/api/v1/templates/{id}` | One copy |
| `DELETE` | `/api/v1/templates/{id}` | `204`. `409 template_in_use` when a rule points at it |
| `GET` | `/api/v1/templates/{id}/versions` | `{versions: [{number, state, subject, preheader, document, published_at}]}` |
| `GET` | `/api/v1/templates/{id}/versions/{number}` | One version, same fields |
| `POST` | `/api/v1/templates/{id}/versions` | Body `subject`, `preheader`, `document`, or `library_id` to start over from a library template (the subject and preheader stay). Composes and stores HTML and text. `201` `{number, state: "draft"}` |
| `POST` | `/api/v1/templates/{id}/versions/{number}/publish` | Runs the lane checks, sets `active_version`. `{number, state, checksum}` |
| `POST` | `/api/v1/templates/{id}/send-test` | Sends the document in the body, or the latest draft, with catalog examples |

List and detail items:

```json
{
  "id": 15,
  "template_key": "company.a1b2c3d4e5f6",
  "name": "Deployment failed",
  "event_type": "hosting.deployment.failed",
  "language": "",
  "company_id": 42,
  "active_version": 1,
  "source_key": "barebone.text-only",
  "set": "barebone",
  "head": "/* set CSS */"
}
```

`name` is plain text, max 120. `source_key` is the library template the copy came from, and `head` is the CSS of its `set`. Catalog keys still work for `POST /api/v1/send`.

`POST /api/v1/templates/{id}/send-test` body, all optional:

```json
{
  "document": {"type": "doc", "content": []},
  "subject": "Hello",
  "preheader": "",
  "to": "ada@acme.com"
}
```

When `document` is present it is the draft being edited, including one that has not been saved as a version, and `subject` is required. When it is omitted, the highest-numbered `draft` version is sent. No draft is `400` with `version: ["draft_required"]`. Catalog `example` values fill `{{ token }}` so the message is readable. A company owner can only send to the email on their JWT. Staff may set `to`. The response matches provider test-send: `status`, `provider`, `provider_message_id`.

`document` is React Email editor JSON (TipTap):

```json
{
  "type": "doc",
  "content": [
    {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "Title"}]},
    {
      "type": "paragraph",
      "content": [
        {"type": "text", "text": "Read "},
        {"type": "text", "text": "the docs", "marks": [{"type": "bold"}, {"type": "link", "attrs": {"href": "https://shellui.com/docs"}}]},
        {"type": "text", "text": " first."}
      ]
    },
    {"type": "button", "attrs": {"href": "{{ magic_link_url }}"}, "content": [{"type": "text", "text": "Open"}]}
  ]
}
```

Placeholders are `{{ token }}` or `{{ token|default:"fallback" }}`. `{%` is rejected (`template_tags_forbidden`). Allowed nodes and marks, link and image rules, and the size cap are in [templates.md](templates.md#document). A rejected document is `400 validation_failed` with `field_errors.document` set to `invalid`, `too_large`, `node_not_allowed`, `mark_not_allowed`, `unsafe_attribute`, `unsafe_link`, `unsafe_image`, or `render_failed`.

## Admin: broadcasts

`GET/POST /api/v1/broadcasts`, `GET/PATCH/DELETE /api/v1/broadcasts/{id}`, `POST /api/v1/broadcasts/{id}/preview`, and `POST /api/v1/broadcasts/{id}/send`. Auth: staff or company owner. Preview and send relay the caller's JWT to identity, whose company must be the broadcast's. Shapes, audience filters, languages, and errors are in [broadcasts.md](broadcasts.md).

Template summaries carry `kind` (`event`, `broadcast`, or `newsletter_confirmation`). `GET /api/v1/templates` lists event templates only.

## Admin: newsletters

`GET/POST /api/v1/newsletters`, `GET/PATCH/DELETE /api/v1/newsletters/{id}`, `POST /api/v1/newsletters/{id}/rotate-key`, `GET/POST /api/v1/newsletters/{id}/subscribers`, `DELETE /api/v1/newsletters/{id}/subscribers/{sid}`, `GET /api/v1/newsletters/{id}/subscribers.csv`, and `POST /api/v1/newsletters/{id}/subscribers/import`. Auth: staff or company owner. The public sign-up endpoint is `POST /api/v1/public/newsletters/{public_key}/subscribe`, with no auth. Broadcasts send to a list with the audience `{"mode": "newsletter", "list_id": 12}`. Shapes, abuse limits, unsubscribes, and errors are in [newsletters.md](newsletters.md).

## Admin: stats

`GET /api/v1/stats?company_id=42&from=2026-09-01T00:00:00Z&to=2026-10-02T00:00:00Z&lane=&event_type=`

Default window: the last 30 days.

```json
{
  "company_id": 42,
  "from": "2026-09-02T00:00:00Z",
  "to": "2026-10-02T00:00:00Z",
  "totals": {
    "sent": 0,
    "delivered": 0,
    "bounced": 0,
    "complained": 0,
    "expired": 0,
    "failed": 0,
    "queued": 0,
    "suppressed": 0,
    "cancelled": 0
  },
  "skipped": {"total": 0, "no_recipients": 0, "rule_disabled": 0, "no_rule": 0},
  "by_lane": {},
  "by_event": {},
  "by_day": [{"day": "2026-10-01", "sent": 1, "delivered": 0, "bounced": 0, "complained": 0, "expired": 0, "failed": 0, "queued": 0, "suppressed": 0, "cancelled": 0}]
}
```

`sent` counts provider-accepted messages. A `delivered` or `bounced` row is also included in `sent`, so `sent` is a superset of the later provider statuses. `by_event` keys are `event_type` (the catalog id).

`skipped` counts accepted `POST /api/v1/events` calls that queued no message. `no_recipients` is an enabled rule with no resolvable address. `no_rule` is an event with no enabled rule. `rule_disabled` stays in the object for older rows and is no longer written. `from`, `to`, `event_type`, and `lane` filter these rows the same way they filter messages. `lane` uses the catalog lane of the event.

## Admin: suppressions, lanes, privacy, clients

`GET /api/v1/suppressions?company_id=42`

`POST /api/v1/suppressions?company_id=42` body `email`, optional `reason` (`hard_bounce`, `complaint`, `manual`, `provider`), optional `lanes` (empty means all non-auth lanes). `201` returns `id`, `email_masked`, `reason`. The address is stored as an HMAC.

`DELETE /api/v1/suppressions/{id}` `204`.

Auth lane suppression honors `hard_bounce` only, for 30 days when the worker creates the row. Complaints and unsubscribes do not block magic links.

`POST /api/v1/lanes/{lane}/pause` and `POST /api/v1/lanes/{lane}/resume`

Auth: staff only. `lane` is `auth`, `transactional`, or `bulk`. Response: `{lane, paused}`.

`POST /api/v1/privacy/erase`

Auth: identity service key or staff.

```json
{"company_id": 42, "email": "ada@acme.com"}
```

Response: `{deleted_messages, email_masked}`. Deletes message rows and their events for that company and address. It does not delete suppressions.

`GET /api/v1/service-clients` staff only. Lists prefix, lanes, and prefixes. Never the key.

`POST /api/v1/service-clients` staff only.

```json
{
  "service": "identity",
  "name": "identity-production",
  "allowed_lanes": ["auth", "transactional"],
  "allowed_template_prefixes": ["identity."]
}
```

`201` includes `key` once.

## Health and metrics

`GET /api/v1/health` is public.

```json
{"status": "ok", "version": "0.1.0"}
```

`GET /api/v1/metrics` requires an identity JWT. Staff, or a token with the `pat_agm` claim (`access_global_metrics`), receives every company. Owners receive their company. `?company_id=` is allowed for staff and for the matching owner.

Prometheus text. See [metrics.md](metrics.md).

## Provider webhooks (Resend)

`POST /api/v1/provider-webhooks/resend/{stream}`

No Bearer token. Svix headers `svix-id`, `svix-timestamp`, `svix-signature` are verified with `RESEND_WEBHOOK_SECRET` when `company_id` is omitted. When `?company_id=` is set, only that company's stored webhook secret is accepted. A missing company secret does not fall back to the platform secret. The request is `401 unauthorized`.

Opens and clicks are ignored. Delivery, bounce, complaint, and delay update message status and emit Shellui Actions events. Point the Resend webhook at this URL. `stream` is recorded on the event and is not used to choose a lane.

Events with `data.broadcast_id` update that broadcast's recipient instead. `contact.updated` with `unsubscribed: true` records a bulk unsubscribe. See [broadcasts.md](broadcasts.md#unsubscribes-and-webhooks).

## Unsubscribe

`GET /u/{token}` shows a confirmation page and does not unsubscribe.

`POST /u/{token}` records the unsubscribe (one-click, including an empty body) only when the token signature matches. Token form: `{company_id}.{category}.{email_hmac}.{signature}`. `category` is the lane (`transactional` or `bulk`). `signature` is HMAC-SHA256 of `{company_id}|{email_hmac}|{category}` with `EMAIL_HASH_PEPPER`. A token that does not verify returns 404 and does not emit `email.unsubscribe.created`.

Non-auth messages set `system.unsubscribe_url` and `system.preferences_url` to that signed URL. Auth messages do not. Bulk messages also set `List-Unsubscribe` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click`. Transactional messages do not add those headers.

## Shellui Actions (outbound)

Companies can subscribe to email-service events. Paths match storage-service and hosting-service under `/api/v1/actions/`. Auth: staff or company owner.

| Method | Path |
| --- | --- |
| `GET` | `/api/v1/actions/events` |
| `GET` | `/api/v1/actions/event-log` |
| `GET` | `/api/v1/actions/event-log/types` |
| `GET` | `/api/v1/actions/event-log/retention` |
| `GET` | `/api/v1/actions/event-log/{id}` |
| `GET`, `POST` | `/api/v1/actions/rules?company_id=` |
| `GET`, `PATCH`, `DELETE` | `/api/v1/actions/rules/{id}` |
| `POST` | `/api/v1/actions/rules/{id}/send-test` |
| `POST` | `/api/v1/actions/rules/{id}/rotate-secret` |
| `GET` | `/api/v1/actions/deliveries?company_id=` |
| `GET` | `/api/v1/actions/deliveries/{uuid}` |
| `POST` | `/api/v1/actions/deliveries/{uuid}/requeue` |

Create body: `name`, `event_type`, `url`, optional `secret`, optional `description`, optional `enabled` (default true). The plaintext secret is returned only on create and rotate. Other responses expose `config.has_secret` and `config.secret_hint`.

Event types: `email.message.sent`, `email.message.delivered`, `email.message.delivery_delayed`, `email.message.bounced`, `email.message.complained`, `email.message.failed`, `email.message.expired`, `email.message.suppressed`, `email.unsubscribe.created`.

Envelope:

```json
{
  "id": "<uuid>",
  "type": "email.message.delivered",
  "time": "2026-10-02T12:00:01+00:00",
  "company": {"id": 42},
  "data": {
    "message_id": "msg_<hex>",
    "template_key": "hosting.deployment.failed",
    "lane": "transactional",
    "service": "hosting",
    "to_email": "ada@acme.com",
    "to_user_id": 7
  }
}
```

`data.to_email` is the recipient address. Treat the webhook body as personal data.

Signing matches the other Shellui services: Standard Webhooks HMAC, headers `webhook-id`, `webhook-timestamp`, `webhook-signature`, plus `X-Shellui-Event`, `X-Shellui-Delivery-Attempt`, and `User-Agent: shellui-email-webhooks/1.0`. The body is compact sorted JSON. Verify the raw bytes.

Retries: 30s times 2^(attempt-1), capped at 1 hour, 8 attempts, then `dead`. `404`, `408`, `409`, `425`, `429`, other retryable 4xx, 5xx, and connection errors retry. `400`, `401`, `403`, `405`, `410`, `413`, `422` are dead. `429` and `503` honor `Retry-After` up to 1 hour.

Delivery is at-least-once. Dedupe on `webhook-id`. A provider event and the local accept path can both emit `email.message.sent`.

If the service client has `callback_url`, the same envelope is also POSTed there, signed with that client's callback secret. The admin create endpoint does not set the callback. Set it in Django admin or when calling `issue_service_key` from a management command.

n8n notes: [n8n.md](n8n.md).

Run `python manage.py retry_webhooks` every minute and `python manage.py run_email_worker` as a resident process (or a frequent cron calling `sweep_email_queue`). `python manage.py purge_expired_data` hourly. Message rows are kept `EMAIL_MESSAGE_RETENTION_DAYS` (30). Event log rows and finished deliveries are kept `EVENT_LOG_RETENTION_DAYS` (7). Idempotency rows expire after 24 hours.
