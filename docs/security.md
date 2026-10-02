# Production security

Controls for running email-service next to identity-service 0.5.0 or newer.

## Credentials

Company provider API keys and webhook secrets are Fernet-encrypted (`EMAIL_CREDENTIALS_KEY`). API responses return `credentials_hint` and `configured`. They do not return the secret.

Template variables are encrypted with `EMAIL_VARIABLES_KEY` until the provider accepts the message, then deleted. Message status payloads do not include the rendered HTML or the variables.

Recipient addresses on admin lists are masked. Suppressions and unsubscribes store `HMAC-SHA256` of the address (`EMAIL_HASH_PEPPER`), plus a masked hint.

Service API keys are stored as SHA-256. The `esk_` plaintext is shown once.

Shellui Actions signing secrets are encrypted at rest and revealed only on create and rotate.

## CORS and cookies

Browser calls use Bearer JWTs, not cookies. `CORS_ALLOW_CREDENTIALS` defaults to false. `CORS_ALLOW_ALL_ORIGINS` defaults to true only when `DEBUG=true`. Production defaults to false unless the variable is set. Startup fails if both allow-all and credentials are true.

## JWT issuer and audience

When `DEBUG=false`, `IDENTITY_ISSUER`, `IDENTITY_AUDIENCE`, and a pinned JWKS document (`IDENTITY_JWKS` or `IDENTITY_JWKS_FILE`) are required. Issuer and audience must match identity-service `JWT_ISSUER` and `JWT_AUDIENCE` (typically `https://id.shellui.com` and `shellui`). The process refuses to start if the document is missing, including when only `IDENTITY_JWKS_URL` is set.

## Transport

When `DEBUG=false`, `SECURE_SSL_REDIRECT`, HSTS (one year), and secure session and CSRF cookies default to on. Postgres TLS is required unless `POSTGRES_SSL_REQUIRE=false`.

## Links in auth mail

`magic_link_url` must use `https` and a host in `EMAIL_AUTH_LINK_HOSTS`. When `DEBUG=false` that list cannot include `localhost`, `127.0.0.1`, or `::1`. Other URL variables must be `https`, `mailto`, or `tel`. An auth-lane company override must keep the required link variable, and every button `href` must be that variable or an allowlisted `https` host.

## Webhook targets

Outbound Shellui Actions URLs are resolved and checked for private addresses (`apps/actions/ssrf.py`). Set `ACTIONS_WEBHOOK_ALLOW_PRIVATE=true` only on a local network.

## Privacy erase

`POST /api/v1/privacy/erase` is limited to the identity service key and to staff. It deletes message rows for one company and one address.
