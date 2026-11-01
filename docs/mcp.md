---
title: Shellui MCP
sidebar_label: Shellui MCP
description: Company-owner APIs in email-service that Shellui MCP agents may call, and how they are marked in OpenAPI.
---

# Shellui MCP

Shellui MCP exposes selected company-owner APIs to AI agents through a shared server built in identity-service. email-service does not run that server. It publishes an OpenAPI schema with `x-mcp` extensions so the MCP layer knows which routes are safe to offer agents.

Agents authenticate with the same identity-service JWT as the admin panel. They act as the company owner for the JWT `company_id`. Service keys (`esk_`) and staff-only routes are never MCP tools.

## Where the schema lives

- `GET /api/schema/` on the email-service deployment (also linked from the [service homepage](https://email.shellui.com/)).
- Each operation may include:

```yaml
x-mcp:
  read_only: true
  destructive: false
  sensitive: false
```

- `read_only`: HTTP GET-style tools that do not change stored state (preview endpoints count as read-only).
- `destructive`: deletes or other irreversible changes.
- `sensitive`: mail, credentials, signing secrets, or personal data.

Response fields can carry `x-mcp-secret` (never return to agents) or `x-mcp-sensitive` (mask in read-safe MCP modes).

## Endpoints available to agents

The tables list the HTTP API. Paths are under `/api/v1/`. Company owners pass `Authorization: Bearer <jwt>`. Staff may add `?company_id=` when the token allows it.

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
