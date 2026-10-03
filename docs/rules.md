# Email rules

A company email rule is one send instruction, the same idea as a Shellui Actions webhook rule. The admin "Email and webhooks" page lists email rules next to webhook rules, ordered by event.

There is no on/off row per catalog event. A company creates the rules it wants. Several rules may share one event (the person on the event, plus a static ops address).

Auth-lane events are the exception. Login depends on them, and magic link is the default sign-in method, so a built-in rule exists for every company without anyone creating it.

The HTTP contract is also in [integration.md](integration.md). Catalog events are in [events.md](events.md). Themes are in [themes.md](themes.md).

## Fields

| Field | Meaning |
| --- | --- |
| `id` | Rule id. Path parameter on get, patch, and delete. |
| `service` | Owner service of the event (`identity`, `storage`, `hosting`). |
| `event_type` | Catalog event id. |
| `enabled` | `false` skips this rule. Built-in rules cannot be set to `false`. |
| `recipient_mode` | `hints` uses the event's recipients. `static` uses `static_recipients`. |
| `static_recipients` | Email strings. On write, a string or `{"email": "..."}` is accepted. |
| `language` | `en`, `fr`, or blank. Blank follows the recipient language, then the event language, then `en`. |
| `template_id` | Company template this rule sends. |
| `built_in` | Read-only. `true` for auth-lane rules created by the service. |
| `created_at`, `updated_at` | ISO-8601 UTC timestamps with a `Z` suffix. |

## Endpoints

Auth: staff or company owner. `company_id` query, or the company on the JWT.

`GET /api/v1/rules?company_id=42&service=identity`

Creates any missing built-in rules, then returns `{company_id, rules}`. Order is `event_type`, then `created_at`. `service` is optional.

`POST /api/v1/rules?company_id=42` returns the rule at `201`.

```json
{
  "event_type": "hosting.deployment.failed",
  "content": {"mode": "suggested"}
}
```

`content.mode` `suggested` creates a published company template from that event's suggested document, in the company's current theme, named with the event label, and links it. The response `template_id` is the id the editor should open.

`content.mode` `existing` links a template the company already has:

```json
{"event_type": "hosting.deployment.failed", "content": {"mode": "existing", "template_id": 15}}
```

Optional fields: `service` (must own the event), `enabled` (default `true`), `recipient_mode` (default `hints`), `static_recipients`, `language`.

`GET /api/v1/rules/{id}?company_id=42` returns one rule.

`PATCH /api/v1/rules/{id}?company_id=42` accepts `enabled`, `recipient_mode`, `static_recipients`, `language`, and `template_id`.

`DELETE /api/v1/rules/{id}?company_id=42` returns `204`.

## Errors

JSON uses `error_code` only. No translated sentence.

| `error_code` | HTTP | When |
| --- | --- | --- |
| `event_unknown` | 400 | `event_type` is not in the catalog, or `service` is not the owner |
| `template_not_found` | 404 | Template id is missing or belongs to another company |
| `template_variables_mismatch` | 400 | Every `{{ token }}` in the template must be declared on the event. The body adds `missing_variables`. `system.*` is always allowed. `recipient_email` is always available. |
| `rule_built_in` | 409 | Delete, or `{"enabled": false}`, on a built-in rule |
| `language_not_available` | 400 | Language is not `en` or `fr` |
| `not_found` | 404 | Rule id is not in this company |
| `validation_failed` | 400 | `content` is missing or not `suggested` / `existing`, `recipient_mode` is not `hints` or `static`, or `static_recipients` is not a list of addresses |

`GET /api/v1/templates?event_type=` uses the same variable check and returns only fitting templates. An unknown `event_type` there is also `event_unknown`. Ingest (`POST /api/v1/events`) still uses `unknown_event` for a bad event id.

## Built-in auth rules

Every catalog event with `lane_class: auth` gets one built-in rule per company. Today that is `identity.auth.magic_link.requested` and `identity.user.invited`. Password reset and email verification are not in the catalog, so they have no built-in rule. A future auth-lane catalog event is covered the same way, with no extra setting.

Creation is lazy and idempotent: the first `GET /api/v1/rules` or the first `POST /api/v1/events` for that company. A second call does not add another built-in row. The template is published English copy of the suggested document, in the company theme, and `language` on the rule is blank so the message follows the event language until someone edits the template.

A built-in rule cannot be deleted or disabled. Edit its template through the version API, or `PATCH` `template_id` to another company template whose variables fit. `language`, `recipient_mode`, and `static_recipients` can still be patched. Leaving `recipient_mode` as `hints` keeps the sign-in message on the address identity sent.

## Sending

`POST /api/v1/events` queues one message for each enabled rule on that company, service, and event. The idempotency key is stored once for the whole response, so a retried event does not send a second copy of any of those messages.

No enabled rule: `202`, `rule_enabled: false`, `skipped_reason: no_rule`.

Enabled rules and no address: `202`, `rule_enabled: true`, `skipped_reason: no_recipients`. This is returned before the provider check.

A matching non-auth rule with a recipient still needs a company provider (`409 platform_sender_not_allowed`), unless the company is in `EMAIL_PLATFORM_COMPANY_IDS`. Auth-lane rules keep the platform fallback From `no-reply@shellui.com`.

Direct `POST /api/v1/send` does not read rules.

## Migration

Company rules that were enabled become one rule plus a company template copied from the published content, or from the suggested document when nothing was published. Disabled overrides and platform-default rows are dropped. Auth mail is covered by the built-in rules instead. Versions stored as theme `shellui` move to `barebone`.
