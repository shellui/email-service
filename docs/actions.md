# Shellui Actions (outbound webhooks)

Company owners and staff can subscribe to email-service domain events. Delivery uses the same outbox, signing, and retry rules as storage-service and hosting-service. There is no separate bus.

For n8n, see [n8n.md](n8n.md). The caller contract, including the envelope, is in [integration.md](integration.md).

## Event catalog

| Event type | When it fires |
| --- | --- |
| `email.message.sent` | The provider accepted the message. May also fire again if a provider webhook reports sent. Dedupe on `webhook-id` per delivery, and on your side if you need one row per message. |
| `email.message.delivered` | The recipient server accepted the message. |
| `email.message.delivery_delayed` | The provider reported a delay. |
| `email.message.bounced` | The message bounced. A hard bounce also writes a suppression. |
| `email.message.complained` | The recipient reported the message. |
| `email.message.failed` | Handoff failed with no retries left, or the provider rejected the key. |
| `email.message.expired` | The auth TTL elapsed before handoff. |
| `email.message.suppressed` | The address was suppressed and the message was not handed to the provider. |
| `email.unsubscribe.created` | `POST /u/{token}` recorded an unsubscribe. |

`data` includes `message_id`, `template_key`, `lane`, `service`, `to_email`, and `to_user_id`. `to_email` is personal data.

Opens and clicks from Resend are ignored and do not emit events.

## Signing

- Body: UTF-8 compact JSON.
- Verifiers HMAC the raw request bytes.
- Secret: plain string or Standard Webhooks `whsec_<base64>`. Create and rotate return the plaintext once.
- Headers: `webhook-id`, `webhook-timestamp`, `webhook-signature` (`v1,<base64>`), `X-Shellui-Event`, `X-Shellui-Delivery-Attempt`, `User-Agent: shellui-email-webhooks/1.0`.

Default timeout: 5 seconds (`ACTIONS_WEBHOOK_TIMEOUT_SECONDS`).

## Retries

Backoff is 30s * 2^(attempt-1), capped at 1 hour, up to 8 attempts, then `dead`.

| HTTP result | Behavior |
| --- | --- |
| 2xx | Delivered |
| 404, 408, 409, 425, 429, other retryable 4xx, 5xx, timeouts, connection errors | Retry |
| 400, 401, 403, 405, 410, 413, 422 | Dead (no retry) |
| 429 / 503 with `Retry-After` | Next attempt uses `Retry-After` (capped at 1 hour) |

SSRF checks reject private and link-local targets unless `ACTIONS_WEBHOOK_ALLOW_PRIVATE=true` (local only).

```cron
* * * * * python manage.py retry_webhooks
```

Finished deliveries and event-log rows are deleted after `EVENT_LOG_RETENTION_DAYS` (7) by hourly `purge_expired_data`.

## Admin API

Paths and bodies: [integration.md](integration.md#shellui-actions-outbound).
