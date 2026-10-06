# Email rules

A company email rule is one send instruction, the same idea as a Shellui Actions webhook rule. The admin "Email and webhooks" page lists email rules next to webhook rules, ordered by event.

There is no on/off row per catalog event. A company creates the rules it wants. Several rules may share one event (the person on the event, plus a static ops address).

Auth-lane events are the exception. Login depends on them, and magic link is the default sign-in method, so a built-in rule exists for every company without anyone creating it. Their mail carries a sign-in or invitation link, so a company cannot add its own rule on them (`400 auth_event_rule_forbidden`), and the built-in rule always sends to the event's recipient.

The HTTP contract is also in [integration.md](integration.md). Catalog events are in [events.md](events.md). Designs are in [library.md](library.md) and copies in [templates.md](templates.md).

## Fields

| Field | Meaning |
| --- | --- |
| `id` | Rule id. Path parameter on get, patch, and delete. |
| `service` | Owner service of the event (`identity`, `storage`, `hosting`). |
| `event_type` | Catalog event id. |
| `enabled` | `false` skips this rule. Built-in rules cannot be set to `false`. |
| `recipient_mode` | `hints` uses the event's recipients. `static` uses `static_recipients`. Built-in rules are always `hints`. |
| `static_recipients` | Email strings. On write, a string or `{"email": "…"}` is accepted. |
| `language` | `en`, `fr`, or blank. Blank follows the recipient language, then the event language, then `en`. The copy sends that language's [translation](templates.md#languages) when it has one. |
| `template_id` | The rule's copy: the company template this rule sends. |
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
  "content": {"library_id": 3}
}
```

`content.library_id` is a library template the company can see: a built-in or one of its own (`GET /api/v1/library`). email-service copies that design onto the event, publishes it, and links it. The copy is named with the event label and uses the catalog subject and preheader in the rule's language. The response `template_id` is the copy the editor opens. Two rules never share a copy.

Optional fields: `service` (must own the event), `enabled` (default `true`), `recipient_mode` (default `hints`), `static_recipients`, `language`.

`GET /api/v1/rules/{id}?company_id=42` returns one rule.

`PATCH /api/v1/rules/{id}?company_id=42` accepts `enabled`, `recipient_mode`, `static_recipients`, and `language`. To change the design, start the copy over from another library template (`POST /api/v1/templates/{id}/versions` with `library_id`).

`DELETE /api/v1/rules/{id}?company_id=42` returns `204` and deletes the rule's copy.

## Errors

JSON uses `error_code` only. No translated sentence.

| `error_code` | HTTP | When |
| --- | --- | --- |
| `event_unknown` | 400 | `event_type` is not in the catalog, or `service` is not the owner |
| `library_not_found` | 404 | `content.library_id` is not a built-in or one of this company's templates |
| `rule_built_in` | 409 | Delete, `{"enabled": false}`, `recipient_mode: static`, or a non-empty `static_recipients` on a built-in rule |
| `auth_event_rule_forbidden` | 400 | `POST` on an auth-lane event (`field_errors.event_type: ["auth_event"]`), or `PATCH` on a company rule written on one before this check |
| `language_not_available` | 400 | Language is not `en` or `fr` |
| `not_found` | 404 | Rule id is not in this company |
| `validation_failed` | 400 | `content.library_id` is missing (`library_id_required`), `recipient_mode` is not `hints` or `static`, or `static_recipients` is not a list of addresses |
| `library_not_synced` | 503 | A built-in rule's default design is missing because migrations have not run |

Ingest (`POST /api/v1/events`) uses `unknown_event` for a bad event id.

## Built-in auth rules

Every catalog event with `lane_class: auth` gets one built-in rule per company. Today that is `identity.auth.magic_link.requested` and `identity.user.invited`. Password reset and email verification are not in the catalog, so they have no built-in rule. A future auth-lane catalog event is covered the same way, with no extra setting.

`identity.auth.magic_link.staff_blocked` gets no rule at all. Its copy is Shellui's own (`company_editable: false`), so no company can edit it, and only direct `/send` delivers it. See [Built-in auth emails](integration.md#built-in-auth-emails).

Creation is lazy and idempotent: the first `GET /api/v1/rules` or the first `POST /api/v1/events` for that company. A second call does not add another built-in row. The copy starts from the event's `default_template` (`barebone.activation` for magic links, `barebone.welcome` for invitations), with the English catalog subject and preheader. `language` on the rule is blank, so an unedited subject and preheader follow the event language.

A built-in rule cannot be deleted or disabled, and its recipients cannot change: `recipient_mode` stays `hints` and `static_recipients` stays empty (`409 rule_built_in`). The sign-in message only goes to the address identity sent. Edit its copy through the version API, or start it over from another library template. `language` can still be patched.

A company cannot create a rule on an auth-lane event. `POST /api/v1/rules` returns `400 auth_event_rule_forbidden`. The catalog marks these events with `rules_allowed: false`.

On `POST /api/v1/events`, an auth-lane event uses the built-in rule only, with the event's recipient, and accepts exactly one (`400 auth_single_recipient` otherwise). Direct `/send` on an auth-lane template takes exactly one `to` address too, and `/send/batch` exactly one item. Direct sends use the built-in rule's copy, or the default design. A copy that fails today's [auth checks](templates.md) at send time falls back to the default design.

## Sending

`POST /api/v1/events` queues one message for each enabled rule on that company, service, and event. The idempotency key is stored once for the whole response, so a retried event does not send a second copy of any of those messages.

No enabled rule: `202`, `rule_enabled: false`, `skipped_reason: no_rule`.

Enabled rules and no address: `202`, `rule_enabled: true`, `skipped_reason: no_recipients`. This is returned before the provider check.

A matching non-auth rule with a recipient still needs a company provider (`409 platform_sender_not_allowed`), unless the company is in `EMAIL_PLATFORM_COMPANY_IDS`. Auth-lane rules keep the platform fallback From `no-reply@shellui.com`.

Direct `POST /api/v1/send` does not read rules.
