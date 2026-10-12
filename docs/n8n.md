---
title: n8n
sidebar_label: n8n
description: Receive email-service Shellui Actions webhooks in an n8n Webhook node.
---

# n8n

email-service can POST signed JSON to an n8n Webhook node when a message changes state. The signing and retry rules match identity-service, storage-service, and hosting-service, so one n8n pattern works for every Shellui webhook.

The event list and envelope are in [actions.md](actions.md) and [integration.md](integration.md).

## Before you start

1. Create a Shellui Actions webhook rule (`POST /api/v1/actions/rules` on email-service, or the admin app).
2. Copy the production Webhook URL from n8n when the workflow is active.
3. Set the rule signing secret to the value n8n shows, or to a `whsec_` secret you generate. Plain text and `whsec_<base64>` both work. The API returns the secret only on create and on rotate.
4. Optional: if the Webhook node uses Header Auth, store that header only in a system you control. This version of the rule API signs the body. It does not add a separate Authorization header.

Default HTTP timeout from Shellui is 5 seconds (`ACTIONS_WEBHOOK_TIMEOUT_SECONDS`). In the Webhook node, set Respond to Immediately so n8n answers before the workflow finishes.

## n8n Webhook node

| Setting | Value |
| --- | --- |
| HTTP Method | POST |
| Path | Your choice (the production URL includes the path) |
| Authentication | None (the signature is enough) |
| Respond | Immediately |

Shellui always POSTs to the URL saved on the rule. Use the production URL. n8n returns 404 when the workflow is inactive. Shellui retries 404.

## Delivery

- At-least-once. Dedupe on header `webhook-id`.
- No ordering guarantee between lanes or companies.
- `webhook-id` stays stable across retries of the same delivery. `X-Shellui-Delivery-Attempt` increments.
- `email.message.sent` can arrive twice for one message if the provider webhook also reports acceptance. Dedupe on `data.message_id` plus `type` if you need one row.

## HTTP headers

| Header | Meaning |
| --- | --- |
| `Content-Type` | `application/json` |
| `webhook-id` | Stable across retries of this delivery |
| `webhook-timestamp` | Unix seconds when signed |
| `webhook-signature` | `v1,<base64>` HMAC-SHA256 |
| `X-Shellui-Event` | Event type, for example `email.message.delivered` |
| `X-Shellui-Delivery-Attempt` | Attempt number (1 on the first try) |
| `User-Agent` | `shellui-email-webhooks/1.0` |

Verify the raw request body bytes, not a re-encoded pretty print.

## Retry behavior

| HTTP result | Shellui behavior |
| --- | --- |
| 2xx | Delivered |
| 404, 408, 409, 425, 429, other retryable 4xx, 5xx, timeouts, connection errors | Retry. Backoff 30s * 2^(attempt-1), max 1 hour, up to 8 attempts. |
| 400, 401, 403, 405, 410, 413, 422 | Dead (no retry) |
| 429 / 503 with `Retry-After` | Next attempt respects `Retry-After`, capped at 1 hour |

A verifier that fails the signature should return 401. Shellui will not retry a 401. Fix the secret, then requeue with `POST /api/v1/actions/deliveries/{id}/requeue`.

## Reference verifier

```bash
node docs/examples/verify-shellui-webhook.mjs "$SECRET" /path/to/raw-body.bin
```

Set `WEBHOOK_ID`, `WEBHOOK_TIMESTAMP`, and `WEBHOOK_SIGNATURE` to the request headers.
