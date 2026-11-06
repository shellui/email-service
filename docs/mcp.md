---
title: Shellui MCP
sidebar_label: Shellui MCP
description: Shellui MCP Streamable HTTP endpoint and company-owner tools in email-service.
---

# Shellui MCP

Shellui MCP lets AI agents call selected company-owner APIs through a Streamable HTTP endpoint on email-service. Identity-service is the OAuth authorization server. Agents authenticate with a company-scoped agent JWT from Identity.

email-service publishes `POST /mcp` (JSON-RPC: `initialize`, `tools/list`, `tools/call`) and `GET /.well-known/oauth-protected-resource` (resource `/mcp`, authorization server = Identity `IDENTITY_ISSUER`). Only OpenAPI operations registered with `mcp_tool(operation_id=..., read_only=..., ...)` become tools. The schema adds the `mcp-tools` tag at publish time from that registry.

Service keys (`esk_`), staff-only routes, and internal callbacks are never MCP tools.

## Agent JWT

Agents use the same Identity JWKS settings as the admin panel (`IDENTITY_JWKS`, `IDENTITY_ISSUER`). Access tokens must include `email` in the `scope` claim and use audience `{EMAIL_PUBLIC_URL}/mcp` exactly (RFC 8707 resource indicator). Tokens with any other `aud` are rejected (including SaaS-wide or global gateway issuances that are not minted for this resource).

Required claims:

| Claim | Purpose |
| --- | --- |
| `agent_id` | Agent uuid |
| `company_id` | Regional company scope as a JSON number (integer, non-negative). Reject strings, floats, and booleans (`true` and `false` are not integers). |
| `scope` | Must include `email` |
| `mcp_access` | `read_only`, `ask`, or `full` |
| `mcp_read` | `safe` or `sensitive` |
| `mcp_destructive` | Must be JSON boolean (`true` or `false`). Marks the agent as eligible for destructive tools. **Every** destructive call still needs a fresh human approval in Identity. |
| `mcp_authority` | `personal` or `delegated`. When the global triple below is present, this claim must be `delegated`. Values such as `global_delegated` are rejected. Omit or set `personal` when there is no global triple. |

Optional claims for globally delegated agents (Identity documentation). Either omit all three or include all three together:

| Claim | Purpose |
| --- | --- |
| `global_company_id` | Canonical UUID string for the Shell-wide company (not the regional numeric `company_id`); nil UUID is rejected |
| `company_binding_version` | Non-negative JSON integer (including `0`) |
| `grant_version` | Non-negative JSON integer; optional for personal agents, required with the other two for delegation |

Personal regional agents omit all three. Tokens that set only one or two of the three are rejected. Optional `grant_version` alone (including `0`) is allowed for personal agents.

Regional resource tokens use audience `{EMAIL_PUBLIC_URL}/mcp` for this host. Do not exchange a global-only token for email MCP without Identity re-minting the regional profile above.

## Access modes

| `mcp_access` | Behavior |
| --- | --- |
| `read_only` | HTTP GET-style tools only |
| `ask` | Writes create an approval request in Identity |
| `full` | Non-destructive writes run without a pending approval. Destructive tools always need a fresh approval per call. |

Read-safe mode (`mcp_read=safe`) masks emails and other sensitive strings in tool results. Fields marked `x-mcp-secret` in OpenAPI are never returned (for example webhook signing secrets and provider passwords).

## Identity integration

email-service reuses existing Identity URL settings (no new required env vars):

- Poll `GET {IDENTITY_SERVICE_URL}/api/v1/mcp/internal/mcp-revocations` (30 second cache) with a signed service assertion (`aud` `shellui-mcp-revocation`, scope `mcp.revocation.read`). The response includes **revoked** agent ids (`revoked_agent_ids`) plus a signed `agents[]` snapshot. Presence in `revoked_agent_ids` means the agent is blocked; absence does **not** prove an active grant. If the list or snapshot is missing or untrusted on a poll, MCP fails closed. Before each MCP request, the token must match **exactly one** row for `agent_id`: regional `company_id` must match the JWT (JSON integers only in the snapshot; no string coercion). Delegated rows with `mcp_authority_snapshot_rejected` true are refused. A delegated row must include `global_company_id`, `company_binding_version`, and `grant_version`, and those must match the JWT triple (including `0` values). Duplicate rows for one agent id or no row for a live token are refused.
- **Delegated agents (global triple in the JWT):** After the snapshot check, email-service calls `POST {IDENTITY_SERVICE_URL}/api/v1/mcp/internal/mcp-agent-delegated-authority` with a **strict** JSON body (service-signed internal assertion only; no user JWT): `agent_id` (regional agent UUID from the verified token), JSON integer `company_id` (non-negative), canonical `global_company_id` UUID string, and non-negative JSON integers `company_binding_version` and `grant_version` (`0` is valid). The request uses a **separate** RS256 service JWT: `aud` `shellui-mcp-agent-delegated-authority`, scope `mcp.agent.delegated.authority.read`, `target` = Identity canonical issuer URL (same `IDENTITY_ISSUER` / `identity_origin()` pattern as other internal MCP assertions). The pinned `shellui-django` package does not yet export `build_mcp_agent_authority_assertion`; this service uses `build_mcp_delegated_authority_assertion` in `config/service_signing.py` with those claim values.
- Identity returns a **flat** JSON object (no `agent` wrapper). Required JSON booleans: `authorized` true, `revoked` false, `paused` false. Optional `active`: when present it must be JSON boolean true or the row is denied. At least one of `regional_id`, `agent_id`, or `id` must be present and equal to the regional agent UUID from the token. When `global_agent_id` or `global_agentid` is present it must match that same regional agent UUID. Identity may include extra keys (for example `capabilities`); they are ignored. Integers `company_id`, `global_company_id`, `company_binding_version`, and `grant_version` must match the request body. HTTP `404` for a valid signed request (no binding) returns `403` with `error_code` `authority_binding_missing`. HTTP `403` from Identity denies as a normal forbidden row. HTTP `401` on the internal call means Identity rejected this service’s assertion (issuer, audience, scope, or JWKS trust) and surfaces as `503` with `identity_unavailable`. Network failures, `5xx`, non-JSON bodies, or other transport errors also return `503`.
- MCP matches the live row to the regional token: same agent UUID triple, same regional `company_id`, same canonical `global_company_id`, and same `company_binding_version` and `grant_version`. Snapshot agreement alone is not enough. email-service repeats this live check before each approval state, approval create, approval consume, and delegated call log forward. Personal regional agents (no global triple) skip this POST and rely on the signed revocation snapshot row anchored to Identity JWKS / PEM plus `max_active_grant_version` behavior.
- Forward **flat** audit rows to `POST {IDENTITY_SERVICE_URL}/api/v1/mcp/internal/mcp-call-logs` (no tool `arguments`, no response bodies). Rows send `method` and `http_status` only (no duplicate `http_method` or `status` aliases). Identity #135 is the source of truth for the final allowlist.
- Create approval rows with `POST {IDENTITY_SERVICE_URL}/api/v1/mcp/internal/mcp-approval-requests` using an `Authorization: Bearer` service assertion only (JSON body is `{}`; the action fingerprint is in the assertion claims, not in the POST body). Do not send `arguments`, `arguments_redacted`, titles, or paths in the request body. For delegated agents, the assertion must carry the **full** triple from the verified regional JWT (`global_company_id`, `company_binding_version`, `grant_version`); do not default `grant_version` to `0` or omit fields when the token carries a global triple.

Approval state lookup (`mcp-approval-state`) returns JSON with `status` among `not_found` (no row yet), `pending`, `approved`, `denied`, or `expired` (treated as already consumed). email-service maps those to call-log **`outcome`** `pending_approval` where appropriate (not the literal string `pending`). Any other status, a missing `status`, invalid JSON, an `approved` row without an approval id, or transport failure makes the write path stop without creating a duplicate pending row (treated as denied when the contract is broken; transport errors return `503 identity_unavailable`). Identity approval APIs validate the assertion and live authority; email-service maps unknown HTTP success shapes to failure, not success.

Activity and call logs use the categorical **`outcome`** values above. Older activity labels (`pending`, `success`, `session`, and similar) may still appear read-only in UIs; this service writes only the five outcomes listed here. **`error_code`** on forwarded rows is taken from this service's own enums (for example validation, approval, rate limit), not from arbitrary provider response bodies or external URLs.

Internal calls use short-lived RS256 assertions from this service's key. Configure them with env vars `EMAIL_SERVICE_SIGNING_KEY` or `EMAIL_SERVICE_SIGNING_KEY_FILE` and `EMAIL_SERVICE_ISSUER` (loaded in Django as `MCP_SIGNING_KEY_PEM` and `MCP_SIGNING_ISSUER`; public keys at `GET /.well-known/jwks.json`). Revocation and approval reuse `shellui_django.mcp.internal` builders (`shellui-mcp-revocation`, `shellui-mcp-call-log`, `shellui-mcp-approval`). The live delegated row uses `build_mcp_delegated_authority_assertion` in `config/service_signing.py` (`shellui-mcp-agent-delegated-authority`). Revocation poll responses are verified with Identity JWKS via `parse_trusted_revocation_poll_response`.

email-service pins **`shellui-django`** to django-commons **`develop`** at git commit **`1f0754b421688ed26b7eb2e76b25b6fa3eea2a37`** (`pyproject.toml` / `uv.lock`). Install from that git ref, not PyPI, when you need a matching build.

Approval assertions always use `shellui_django.mcp.internal.build_mcp_approval_assertion` from that pin:

- **Personal regional:** optional `grant_version` only when the claim is present (including explicit `0`); omit `global_company_id` and `company_binding_version`.
- **Globally delegated:** `global_company_id` (UUID string), `company_binding_version`, and `grant_version` are **all required** on every create, state, and consume path (never truthiness: `0` is valid). Incomplete triples are refused.

Revocation poll responses must carry signed `max_active_grant_version` (and matching HTTP body); the trusted parser from `shellui-django` enforces per-agent snapshots and fingerprints.

After a successful HTTP write in `ask` mode, Identity `mcp-approval-consume` must return JSON with `consumed` set to the boolean `true`. A `2xx` response with any other value is treated as failure (no silent success).

## Regional MCP URL vs delegated SaaS flow

- **Regional, personal, or delegated profile for this product:** the resource indicator is `{EMAIL_PUBLIC_URL}/mcp`, for example `http://<regional-email-host>/mcp` in local or regional deployments. Agents send `Authorization: Bearer` with that exact `aud` after Identity mints the token for email-service. The catalog of tools is whatever `tools/list` returns from this deployment; derive tool names and JSON-RPC shapes from `GET /api/schema/` (or `tools/list`), not from hand-maintained prefix lists in other repos.
- **SaaS delegated workflow:** a global Shell profile is exchanged for a **regional** email MCP token through the main Shellui flow (for example ai-service after OAuth exchange). Do not present a shortcut where a global-only token calls `{origin}/mcp` on email-service without Identity re-minting the regional resource token. Link-level documentation for the exchange path lives in the main Shellui repository on `develop` (for example `docs/` paths referenced by your integration handbook), not in this service’s release website branch.
- **Identity token endpoint:** MCP clients obtain agent tokens from Identity’s OAuth surface (for example `POST` `{IDENTITY_ISSUER}/oauth/mcp/token` with the correct `resource` for email); the exact host depends on whether the client targets the regional IdP or the global issuer your environment documents. Pair that with this service’s `GET /.well-known/oauth-protected-resource` for `resource` and `authorization_servers`.

## Workflows agents drive (audit-oriented)

These map MCP tools to flat fields on each forwarded call log row (OpenAPI path **template**, not a materialized URL with filled path parameters or query strings). Path templates must not include query strings or raw URL labels; registry **`tool`** names come from OpenAPI `operationId` (for example `api_v1_templates_render_preview`).

| Agent goal | Tool (`operation_id`) | HTTP | `resource_path` (template) | `access_kind` |
| --- | --- | --- | --- | --- |
| List template copies | `api_v1_templates_list` | GET | `/api/v1/templates` | read |
| List email rules | `api_v1_rules_list` | GET | `/api/v1/rules` | read |
| List Shellui Actions webhook rules | `api_v1_actions_rules_list` | GET | `/api/v1/actions/rules` | read |
| Read default event catalog | `api_v1_catalog_retrieve` | GET | `/api/v1/catalog` | read |
| Create an email rule (and template copy) | `api_v1_rules_create` | POST | `/api/v1/rules` | write |
| Send a design test | `api_v1_templates_send_test` | POST | `/api/v1/templates/{template_id}/send-test` | write |

Rows include **`tool`** (registry `operationId`), **`resource_path`** (template only), **`method`**, **`access_kind`**, categorical **`outcome`**, **`duration_ms`**, `arguments_hash` (SHA-256 of canonical JSON for MCP tool arguments, same input as Identity approval assertions), optional `approval_request_id` or `request_id` when UUID-shaped, optional `error_code`, optional `http_status` after the DRF view runs, optional delegation fields `grant_version`, `global_company_id`, and `company_binding_version` when the token carries them, and optional trusted `company_name` from this service’s `CompanyProfile`. They do **not** include `operation_title`, OpenAPI summaries as titles, raw arguments, redacted argument blobs, user email content, or SMTP provider secrets. Event-triggered email **rules** are normal MCP-tagged API calls; **provider** credentials (Resend, SES, SMTP passwords) stay in company settings and are never MCP tools. Built-in **themes** and template **variables** are the typed fields documented in [Templates and translations](templates.md); pass **`variables`** as a JSON object for `render-preview` and related tools.

**`outcome`** (Identity call-log vocabulary): `failed`, `denied`, `pending_approval`, `allowed`, `session_used`.

- **`allowed`**: HTTP tool completed without error, including after a successful one-shot `mcp-approval-consume` in `ask` mode.
- **`session_used`**: Reserved for a future **session grant** path when Identity issues a reusable session (not used for one-shot approval consume today).
- **`pending_approval`**: Waiting on a human decision in Identity (new request or existing pending row).
- **`denied`**: Identity denied the approval, write gate denied the call, or approval already used.
- **`failed`**: Validation error, transport or Identity internal error, API error response, rate limit, or any outcome that is not a known success path (do not treat an unspecified `2xx` as allowed when the contract does not define a result).

Optional **`error_code`**: `forbidden`, `not_found`, `validation_failed`, `conflict`, `server_error`, `request_failed`, `identity_unavailable`, `tool_error`, `read_only_blocked`, `approval_pending`, `approval_denied`, `approval_expired`, `rate_limited`, `authority_ambiguous`, `authority_mismatch`, `authority_row_missing`, `authority_binding_missing`.

Outcomes before execution (validation failure, write gate, approval-only branch) log `duration_ms` as `0` where applicable; Identity approval polling uses time after the write gate. Rows never include raw tool arguments, response bodies, provider error text, or URLs passed through from an agent.

Trusted `company_name` on forwarded call logs comes from this service's `CompanyProfile` row for the regional `company_id` when one exists.

## Where the schema lives

- `GET /api/schema/` on the email-service deployment (also linked from the [service homepage](https://email.shellui.com/)).
- Each exposed operation includes:

```yaml
x-mcp:
  read_only: true
  destructive: false
  sensitive: false
```

- `read_only`: HTTP GET-style tools that do not change stored state (preview endpoints count as read-only).
- `destructive`: deletes or other irreversible changes (also tagged `mcp-destructive` for the MCP server).
- `sensitive`: mail, credentials, signing secrets, or personal data.

Response fields can carry `x-mcp-secret` (never return to agents) or `x-mcp-sensitive` (mask in read-safe MCP modes).

## Endpoints available to agents

The tables list the HTTP API. Paths are under `/api/v1/`. Agents act as the company owner for the JWT `company_id`.

### Templates and preview

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/templates` | List event and broadcast copies. |
| GET | `/templates/{template_id}` | Read copy metadata. |
| DELETE | `/templates/{template_id}` | Delete an unused copy. |
| GET | `/templates/{template_id}/versions` | List versions (document JSON, translations, theme). |
| POST | `/templates/{template_id}/versions` | Create a draft version. |
| GET | `/templates/{template_id}/versions/{number}` | Read one version. |
| POST | `/templates/{template_id}/versions/{number}/publish` | Publish a draft. |
| POST | `/templates/{template_id}/render-preview` | Render HTML and plain text with placeholders filled. Does not send mail. |
| POST | `/templates/{template_id}/send-test` | Send one test message to the owner address. |

See [Templates and translations](templates.md) for documents, languages, and themes.

### Email rules

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/rules` | List email rules for the company. |
| POST | `/rules` | Create a rule and its template copy. |
| GET | `/rules/{rule_id}` | Read one rule. |
| PATCH | `/rules/{rule_id}` | Update recipients, language, or enabled state. |
| DELETE | `/rules/{rule_id}` | Delete a custom rule (not built-in auth rules). |

See [Email rules](rules.md).

### Shellui Actions webhooks

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/actions/events` | Event types you can subscribe to. |
| GET | `/actions/rules` | List webhook rules. |
| POST | `/actions/rules` | Create a rule (`secret` returned once). |
| GET | `/actions/rules/{id}` | Read one rule (masked secret). |
| PATCH | `/actions/rules/{id}` | Update URL, name, or enabled state. |
| DELETE | `/actions/rules/{id}` | Delete a rule. |
| POST | `/actions/rules/{id}/rotate-secret` | Rotate signing secret (plaintext once). |
| POST | `/actions/rules/{id}/send-test` | Queue a sample delivery. |
| GET | `/actions/deliveries` | Recent webhook deliveries. |
| GET | `/actions/deliveries/{delivery_id}` | One delivery with attempts. |
| POST | `/actions/deliveries/{delivery_id}/requeue` | Requeue a failed delivery. |
| GET | `/actions/event-log` | Recent domain events (company scope). |
| GET | `/actions/event-log/{id}` | One event log row. |

See [Webhooks](actions.md).

### Other company-owner tools

The same schema also tags catalog, library designs, provider settings, auth link hosts, message status, stats, suppressions, broadcasts, and newsletters. Those operations carry `x-mcp` in OpenAPI when they are intended for agents.

## Not available to agents

These routes are omitted from the published schema or never carry `x-mcp`:

- `POST /api/v1/send`, `/events`, and `/send/batch` (service keys).
- `POST /api/v1/internal/company-deletions`.
- `/api/v1/scheduled-jobs` (staff monitoring).
- `/api/v1/service-clients` (service API keys).
- Provider ingress webhooks (`/api/v1/provider-webhooks/...`).
- Public newsletter sign-up (`/api/v1/public/newsletters/...`).
- Lane pause switches, privacy erase, and global Prometheus metrics.

## Calling render-preview

`POST /api/v1/templates/{template_id}/render-preview` accepts optional `number`, `document`, `subject`, `preheader`, `theme`, and `variables`. When `document` is omitted, the newest draft is used, then the active published version. Use **`variables`** as a JSON object of strings (and other JSON types the template expects). Optional **`theme`** is a string such as `barebone` or `shellui` (see [Templates and translations](templates.md) for valid theme names in this repo).

Example MCP tool `api_v1_templates_render_preview` call (OpenAPI path template in `path`, ids in `path_params`, tool body under `body`):

```json
{
  "name": "api_v1_templates_render_preview",
  "arguments": {
    "path": "/api/v1/templates/{template_id}/render-preview",
    "path_params": {"template_id": "1"},
    "method": "POST",
    "body": {
      "theme": "barebone",
      "variables": {
        "company_name": "Acme",
        "action_url": "https://app.example.com/auth/callback?token=sample"
      }
    }
  }
}
```

This only renders HTML and plain text in the response. It does not send email. Tools that send or schedule real delivery (for example `api_v1_templates_send_test`) are destructive or external: in `ask` mode they require a **fresh** human approval in Identity per call even when `mcp_access` is `full`.

The response includes `subject`, `html`, `text`, and `missing_variables` for any `{{ token }}` that stayed empty after substitution.

Provider configuration (Resend, SES, SMTP, and similar) is per company or local dev; documentation examples use plain provider names only and never include live API keys.

## Example JSON-RPC

Use exact tool names from `tools/list` (for example `api_v1_rules_list`), not shortened aliases.

```json
{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}
```

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "api_v1_rules_list",
    "arguments": {"path": "/api/v1/rules", "method": "GET"}
  }
}
```

Paths with placeholders (for example `/api/v1/templates/{template_id}/render-preview`) must use the OpenAPI template in `path` and pass values in `path_params`:

```json
{
  "name": "api_v1_templates_render_preview",
  "arguments": {
    "path": "/api/v1/templates/{template_id}/render-preview",
    "path_params": {"template_id": "12"},
    "method": "POST",
    "body": {"variables": {"company_name": "Acme"}}
  }
}
```

Send `Authorization: Bearer <agent_access_token>` on every `POST /mcp` request.

Regional agent tokens are minted for this host and `{origin}/mcp`. Other products (storage, engines, and similar) use different tools and resources even when a company shares the same numeric `company_id`.

## Internal assertions to Identity

Revocation polling, approvals and call logs send Identity a short-lived RS256 assertion signed with this service's own key (`EMAIL_SERVICE_SIGNING_KEY` or `EMAIL_SERVICE_SIGNING_KEY_FILE`, issuer `EMAIL_SERVICE_ISSUER`, `shellui-django` from the git pin in `uv.lock`). The public key is served at `GET /.well-known/jwks.json`. Identity trusts it only when its own configured issuer matches this service, and reads the key from `EMAIL_SERVICE_JWKS_URL` or a pinned `EMAIL_SERVICE_JWKS`. Without a key, MCP fails closed: agents are refused and call logs are skipped.

Generate a key with `openssl genrsa 3072` and keep it in a secret store or a mounted file (`EMAIL_SERVICE_SIGNING_KEY_FILE`).
