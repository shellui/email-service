---
title: Service keys
sidebar_label: Service keys
description: esk_ service keys for identity, storage, and hosting, and the identity JWT used by the admin API.
---

# Service keys

Two credentials share `Authorization: Bearer`. Sibling services use an `esk_` key. The admin app uses an identity-service JWT.

Issue the key in email-service, then set `EMAIL_SERVICE_API_KEY` on the caller. storage-service and hosting-service also need `EMAIL_SERVICE_ALLOW_PRIVATE=true` when `EMAIL_SERVICE_URL` is a private or loopback address. See [Configuration](configuration.md).

## Key format

Prefix `esk_`, then `token_urlsafe(32)`. Stored as a 12-character prefix and a SHA-256 hash.

A key carries:

- `service` (for example `identity`)
- `allowed_lanes`
- `allowed_template_prefixes` (a template key must start with one of them)
- optional `allowed_company_ids` (empty means every company)

Issue a key as staff:

`POST /api/v1/service-clients`

or:

```bash
uv run python manage.py create_service_key --service identity --lanes auth,transactional --prefixes identity.
```

The command and the POST response print the key once. Callers set `EMAIL_SERVICE_API_KEY`.

Recommended scopes: identity `auth` and `transactional` with prefix `identity.`; storage and hosting `transactional` with prefixes `storage.` and `hosting.`.

## Identity JWTs

Admin routes verify RS256 tokens with the same JWKS client as storage-service and hosting-service.

| Setting | Role |
| --- | --- |
| `IDENTITY_JWKS` or `IDENTITY_JWKS_FILE` | Pinned public keys. Preferred in production. |
| `IDENTITY_SERVICE_URL` | Dev base URL. JWKS is `{url}/.well-known/jwks.json`. |
| `IDENTITY_JWKS_URL` | Explicit JWKS URL. |
| `IDENTITY_ISSUER` | Required when `DEBUG=false`. Match identity `JWT_ISSUER`. |
| `IDENTITY_AUDIENCE` | Required when `DEBUG=false`. Match identity `JWT_AUDIENCE`. |
| `JWT_HS256_FALLBACK_SECRET` | Local only, when identity is in `DEBUG` with HS256. |

The principal needs `is_staff` or `is_company_owner`. If the token has `company_id`, it must equal the requested company unless the caller is staff. A mismatch is `403 company_mismatch`.

`GET /api/v1/metrics` follows storage-service: staff, or the `pat_agm` claim, sees every company. A company owner sees their own company. The endpoint is not anonymous.

## Health

`GET /api/v1/health` does not require a credential.
