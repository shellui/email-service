---
title: Security
sidebar_label: Security
description: Sign-in links, stored secrets, and the production controls for a public email-service.
---

# Security

These controls cover sign-in mail and a public install. Run them next to identity-service 0.5.0 or newer.

## Who receives a sign-in link

An auth-lane send accepts one recipient. A second address returns `400 auth_single_recipient`, on `/send`, on `/send/batch`, and on `/events`. The address is the one in that request. A company rule cannot add another.

Auth-lane events are `identity.auth.magic_link.requested` and `identity.user.invited`. Each company gets a built-in rule. It cannot be deleted, disabled, or switched to a static recipient list (`409 rule_built_in`). A company cannot add its own rule on those events (`400 auth_event_rule_forbidden`).

`/send` does not consult rules. identity-service uses `/send` for those two events and does not also post them to `/events`.

## Staff accounts do not get a magic link

identity-service sends `identity.auth.magic_link.staff_blocked` when a staff account asks for a magic link. email-service does not decide that. It only sends the copy in `apps/email/builtin_auth.py`.

That copy has no link and no token. Variables are `company_name` and `sign_in_url` (the app origin, omitted for a loopback callback). Companies cannot edit it, add a rule, or receive it through `POST /api/v1/events`. It is not in `GET /api/v1/catalog`. `/send` only.

## Links and tokens are not kept

`magic_link_url` and the other send variables are Fernet-encrypted with `EMAIL_VARIABLES_KEY` until the provider accepts the message. They are deleted on accept, on expiry, and when the send fails for good. Message status payloads do not include the rendered HTML or the variables.

`email.message.*` webhook data is `message_id`, `template_key`, `lane`, `service`, `to_email`, and `to_user_id`. It does not include the sign-in URL or the token. The service's own log lines do not write those values either. An SMTP library exception can still contain whatever the relay returned.

Auth copy cannot put the link variable in an image `src`, a `style`, or an `alt` (`auth_link_misplaced`). Subject, preheader, and document text cannot contain a literal URL (`auth_literal_link`).

## What a company can change

A company can:

- Store its own Resend or SMTP credentials, From address, and Bulk From
- Create, edit, and delete email rules on transactional events
- Edit the copy those rules send, including English and French text and a color theme

A company cannot:

- Disable, delete, or add recipients on a built-in auth rule
- Add a rule on an auth-lane event
- Edit `identity.auth.magic_link.staff_blocked`
- Set `from_email` to the platform From address unless the company is in `EMAIL_PLATFORM_COMPANY_IDS`
- Store an SMTP host that is not a public address, or store SMTP at all while `EMAIL_ALLOW_COMPANY_SMTP` is false
- Turn off the private-address check on a Shellui Actions webhook URL

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

`magic_link_url` must use `https` and a host in `EMAIL_AUTH_LINK_HOSTS`. When `DEBUG=false` that list cannot include `localhost`, `127.0.0.1`, or `::1`. Other URL variables must be `https`, `mailto`, or `tel`. An auth-lane copy must keep the required link variable, and every link in the document (link marks, button `href`, linked images) must be a declared URL variable, `{{ system.message_id }}`, or an allowlisted `https` host. Subject, preheader, and every text node in the document cannot contain a literal URL (`auth_literal_link`). The required link variable is the only link allowed in that prose. A copy made from the library for an auth event drops the links these checks would refuse, so it publishes as made.

## Documents

Documents are editor JSON, composed to HTML by email-service, never HTML from the caller. Every save checks the node and mark allowlist, rejects `javascript:`, `vbscript:`, `expression(`, and `data:text/html` in any attribute, accepts only `https`, `mailto`, `tel`, or `{{ token }}` links and `https` or `{{ system.assets_url }}` images, and caps the JSON at `EMAIL_MAX_DOCUMENT_BYTES`. Composing runs `renderer/compose.mjs` in a Node subprocess with a timeout. Details are in [templates.md](templates.md#document).

## Webhook targets

Outbound Shellui Actions URLs are resolved and checked for private addresses (`apps/actions/ssrf.py`). Set `ACTIONS_WEBHOOK_ALLOW_PRIVATE=true` only on a local network. A rule's `config` cannot turn that check off.

## SMTP duplicate window

Resend retries use `Idempotency-Key` set to the message id, so a second handoff of the same message does not send another copy. SMTP has no equivalent. `Message-ID` is stable (`<{message_id}@email.shellui.com>`), and a row that already has `provider_message_id` is marked sent instead of resent. If the worker stops after the relay has accepted the message and before that id is saved, the next attempt can deliver a second copy. That window is a property of SMTP.

## First superuser

Leave `SETUP_TOKEN` empty in production. Create the first administrator with `uv run python manage.py createsuperuser`. The home-page form is open only when `DEBUG=true`, or when `DEBUG=false` and the request carries that token.

## Privacy erase

`POST /api/v1/privacy/erase` is limited to the identity service key and to staff. It deletes message rows for one company and one address.
