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
| `company_id` | Company scope |
| `scope` | Must include `email` |
| `mcp_access` | `read_only`, `ask`, or `full` |
| `mcp_read` | `safe` or `sensitive` |
| `mcp_destructive` | Skip extra approval for destructive tools when true |

## Access modes

| `mcp_access` | Behavior |
| --- | --- |
| `read_only` | HTTP GET-style tools only |
| `ask` | Writes create an approval request in Identity |
| `full` | Writes run immediately (destructive tools may still need approval) |

Read-safe mode (`mcp_read=safe`) masks emails and other sensitive strings in tool results. Fields marked `x-mcp-secret` in OpenAPI are never returned (for example webhook signing secrets and provider passwords).

## Identity integration

email-service reuses existing Identity URL settings (no new required env vars):

- Poll `GET {IDENTITY_SERVICE_URL}/api/v1/mcp/internal/mcp-revocations` (30 second cache) with a signed service assertion (`aud` `shellui-mcp-revocation`, scope `mcp.revocation.read`). If the revocation list or approval internal APIs fail, MCP calls are refused (fail closed).
- Forward redacted call rows to `POST {IDENTITY_SERVICE_URL}/api/v1/mcp/internal/mcp-call-logs`
- Create approval rows with `POST {IDENTITY_SERVICE_URL}/api/v1/mcp/internal/mcp-approval-requests`

Internal calls use short-lived JWT assertions from the shared `shellui_mcp` helpers (`aud` `shellui-mcp-call-log` or `shellui-mcp-approval`). Signing uses the same optional `JWT_PRIVATE_KEY` or local `JWT_HS256_FALLBACK_SECRET` pattern as other Shellui services.

Per-agent rate limits use Redis when `REDIS_URL` is set (same as API rate limits).

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
