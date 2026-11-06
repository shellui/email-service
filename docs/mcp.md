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

Agents use the same Identity JWKS settings as the admin panel (`IDENTITY_JWKS`, `IDENTITY_ISSUER`). Access tokens must include `email` in the `scope` claim and use audience `{EMAIL_PUBLIC_URL}/mcp` exactly (RFC 8707 resource indicator). Tokens with any other `aud` are rejected.

Required claims:

| Claim | Purpose |
| --- | --- |
| `agent_id` | Agent uuid |
| `company_id` | Regional company scope as a JSON number (integer, non-negative). String forms and booleans are rejected. |
| `scope` | Must include `email` |
| `mcp_access` | `read_only`, `ask`, or `full` |
| `mcp_read` | `safe` or `sensitive` |
| `mcp_destructive` | Must be JSON boolean (`true` or `false`). Marks the agent as eligible for destructive tools. **Every** destructive call still needs a fresh human approval in Identity. |

Optional claims for globally delegated agents (Identity admin documentation). Either omit all three or include all three together:

| Claim | Purpose |
| --- | --- |
| `global_company_id` | Canonical UUID string for the Shell-wide company (not the regional numeric `company_id`); nil UUID is rejected |
| `company_binding_version` | Non-negative JSON integer (including `0`) |
| `grant_version` | Non-negative JSON integer; optional for personal agents, required with the other two for delegation |

Personal regional agents omit all three. Tokens that set only one or two of the three are rejected. Optional `grant_version` alone (including `0`) is allowed for personal agents.

## Access modes

| `mcp_access` | Behavior |
| --- | --- |
| `read_only` | HTTP GET-style tools only |
| `ask` | Writes create an approval request in Identity |
| `full` | Non-destructive writes run without a pending approval. Destructive tools always need a fresh approval per call. |

Read-safe mode (`mcp_read=safe`) masks emails and other sensitive strings in tool results. Fields marked `x-mcp-secret` in OpenAPI are never returned (for example webhook signing secrets and provider passwords).

## Identity integration

email-service reuses existing Identity URL settings (no new required env vars):

- Poll `GET {IDENTITY_SERVICE_URL}/api/v1/mcp/internal/mcp-revocations` (30 second cache) with a signed service assertion (`aud` `shellui-mcp-revocation`, scope `mcp.revocation.read`). The response includes revoked agent ids plus a signed `agents[]` snapshot. If the list or snapshot is missing or untrusted on a poll, MCP fails closed. Before each MCP request, the token must match **exactly one** row for `agent_id`: regional `company_id` must match the JWT (JSON integers only in the snapshot; no string coercion). A delegated row must include `global_company_id`, `company_binding_version`, and `grant_version`, and those must match the JWT triple (including `0` values). Duplicate rows for one agent id or no row for a live token are refused.
- Forward **flat** audit rows to `POST {IDENTITY_SERVICE_URL}/api/v1/mcp/internal/mcp-call-logs` (no tool `arguments`, no response bodies).
- Create approval rows with `POST {IDENTITY_SERVICE_URL}/api/v1/mcp/internal/mcp-approval-requests` using an `Authorization: Bearer` service assertion only (JSON body is `{}`; the action fingerprint is in the assertion claims, not in tool arguments).

Approval state lookup (`mcp-approval-state`) returns `not_found` when Identity has no row yet for that assertion fingerprint, `pending`, `approved`, `denied`, or `expired` (treated as already consumed). Any other status, a missing `status`, invalid JSON, an `approved` row without an approval id, or transport failure makes the write path stop without creating a duplicate pending row (treated as denied when the contract is broken; transport errors return `503 identity_unavailable`).

Internal calls use short-lived JWT assertions from the shared `shellui_django.mcp` helpers (`aud` `shellui-mcp-call-log` or `shellui-mcp-approval`). Outbound signing uses `JWT_PRIVATE_KEY` (RS256, same key as identity-service in production). Revocation poll responses are verified with Identity JWKS via `parse_trusted_revocation_poll_response`.

email-service pins **`shellui-django`** to django-commons **`develop`** at git commit **`1f0754b421688ed26b7eb2e76b25b6fa3eea2a37`** (`pyproject.toml` / `uv.lock`). Install from that git ref, not PyPI, when you need a matching build.

Approval assertions always use `shellui_django.mcp.internal.build_mcp_approval_assertion` from that pin:

- **Personal regional:** optional `grant_version` only when the claim is present (including explicit `0`); omit `global_company_id` and `company_binding_version`.
- **Globally delegated:** `global_company_id` (UUID string), `company_binding_version`, and `grant_version` are **all required** on every create, state, and consume path (never truthiness: `0` is valid). Incomplete triples are refused.

Revocation poll responses must carry signed `max_active_grant_version` (and matching HTTP body); the trusted parser from `shellui-django` enforces per-agent snapshots and fingerprints.

After a successful HTTP write in `ask` mode, Identity `mcp-approval-consume` must return JSON with `consumed` set to the boolean `true`. A `2xx` response with any other value is treated as failure (no silent success).

## Workflows agents drive (audit-oriented)

These map MCP tools to flat fields on each forwarded call log row (OpenAPI path **template**, not a materialized URL with filled path parameters or query strings):

| Agent goal | Tool (`operation_id`) | HTTP | `resource_path` (template) | `access_kind` |
| --- | --- | --- | --- | --- |
| List template copies | `api_v1_templates_list` | GET | `/api/v1/templates` | read |
| List email rules | `api_v1_rules_list` | GET | `/api/v1/rules` | read |
| List Shellui Actions webhook rules | `api_v1_actions_rules_list` | GET | `/api/v1/actions/rules` | read |
| Read default event catalog | `api_v1_catalog_retrieve` | GET | `/api/v1/catalog` | read |
| Create an email rule (and template copy) | `api_v1_rules_create` | POST | `/api/v1/rules` | write |
| Send a design test | `api_v1_templates_send_test` | POST | `/api/v1/templates/{template_id}/send-test` | write |

Rows also include `operation_title` from the OpenAPI summary when present, `arguments_hash` (SHA-256 of canonical JSON for tool arguments, same input as Identity approval assertions), optional `approval_request_id` when a pending approval exists, optional bounded `request_id` when the HTTP request carried one, categorical `outcome`, optional `error_code`, optional `http_status` after the DRF view runs, and `duration_ms`.

**`outcome`** (Identity call-log vocabulary): `failed`, `denied`, `pending_approval`, `allowed`, `session_used`.

- **`allowed`**: HTTP tool completed without error and the call did not consume a one-shot Identity approval (read-only or non-destructive write under `full`, or `ask` write after live approval without a consume step in this path).
- **`session_used`**: Same as `allowed` but this execution consumed a one-shot Identity approval for this fingerprint (`ask` mode after successful `mcp-approval-consume`).
- **`pending_approval`**: Waiting on a human decision in Identity (new request or existing pending row).
- **`denied`**: Identity denied the approval, write gate denied the call, or approval already used (`session_used` is not used there).
- **`failed`**: Validation error, transport or Identity internal error, API error response, or rate limit.

Optional **`error_code`**: `forbidden`, `not_found`, `validation_failed`, `conflict`, `server_error`, `request_failed`, `identity_unavailable`, `tool_error`, `read_only_blocked`, `approval_pending`, `approval_denied`, `approval_expired`, `rate_limited`, `authority_ambiguous`, `authority_mismatch`, `authority_row_missing`.

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

`POST /api/v1/templates/{template_id}/render-preview` accepts optional `number`, `document`, `subject`, `preheader`, `theme`, and `variables`. When `document` is omitted, the newest draft is used, then the active published version.

Example body:

```json
{
  "variables": {
    "company_name": "Acme",
    "action_url": "https://app.example.com/auth/callback?token=sample"
  }
}
```

The response includes `subject`, `html`, `text`, and `missing_variables` for any `{{ token }}` that stayed empty after substitution.

## Example JSON-RPC

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

## Internal assertions to Identity

Revocation polling, approvals and call logs send Identity a short-lived RS256 assertion signed with this service's own key (`EMAIL_SERVICE_SIGNING_KEY`, issuer `EMAIL_SERVICE_ISSUER`, `shellui-django` from the git pin in `uv.lock`). The public key is served at `GET /.well-known/jwks.json`. Identity trusts it only when its own `EMAIL_SERVICE_ISSUER` matches, and reads the key from `EMAIL_SERVICE_JWKS_URL` or a pinned `EMAIL_SERVICE_JWKS`. Without a key, MCP fails closed: agents are refused and call logs are skipped.

Generate a key with `openssl genrsa 3072` and keep it in a secret store or a mounted file (`EMAIL_SERVICE_SIGNING_KEY_FILE`).
