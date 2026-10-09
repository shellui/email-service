---
title: email-service
sidebar_label: Overview
description: email-service sends Shellui mail. Identity, storage, and hosting call it. This page is the map of the handbook.
---

# email-service

email-service sends Shellui mail. Identity, storage, and hosting call it when something happens. A company chooses Resend or SMTP, then decides which events send a message.

The HTTP API lives at `https://email.shellui.com`. These pages are published at [docs.shellui.com/email](https://docs.shellui.com/email).

## What email-service is

email-service is the Shellui mail API. It keeps a catalog of events, a queue per lane, and the company's provider credentials. The same process serves the API, Django admin, and OpenAPI.

It is a Django app, published as the `shellui/email-service` Docker image. It checks JSON Web Tokens (JWTs) issued by identity-service for admin calls. Service-to-service calls use an `esk_` key. It does not sign people in itself.

## Who it is for

Use it when a Shellui product needs mail: a sign-in link, an invitation, a notice after a storage or hosting event, a broadcast, or a newsletter. Company owners set the provider, the email rules, and the copy. Operators run the process, Postgres, and Redis.

Your product stays in the shell. email-service does not replace identity-service, storage-service, or hosting-service.

## Where it sits

On the full Shellui stack, three services call email-service when `EMAIL_SERVICE_API_KEY` is set:

- identity-service sends sign-in mail and invitations, and forwards other identity events
- storage-service forwards storage events
- hosting-service forwards hosting events

email-service then hands the message to Resend or SMTP. The company brings that account. A platform fallback covers Shellui's own mail, and auth mail, when a company has no provider.

Supabase Auth and Supabase Storage do not call email-service. hosting-service still can. The picture is on [shellui.com/architecture](https://shellui.com/architecture).

## How a call becomes mail

There are two entry points. The shapes are fixed in the [integration contract](integration.md).

`POST /api/v1/send` is for mail the caller has already decided to send. identity-service uses it for magic links, invitations, and the staff notice. A company email rule does not suppress that call.

`POST /api/v1/events` is for catalog events. email-service queues one message for every enabled email rule on that company, service, and event. No matching rule returns `202` with `skipped_reason: no_rule`. Catalog `default_enabled` does not send mail.

Auth-lane events (`identity.auth.magic_link.requested` and `identity.user.invited`) have a built-in rule. It cannot be deleted, disabled, or pointed at other recipients. A sign-in link goes to the one address in the request. identity-service sends those two with `/send` and does not also post them to `/events`, so the person does not get a second copy.

`identity.auth.magic_link.staff_blocked` is Shellui's own copy in `apps/email/builtin_auth.py`. No rule, no company copy, not listed by `GET /api/v1/catalog`. `/send` only.

## What a company configures

A company stores one provider: Resend or SMTP. Credentials are encrypted at rest. API responses return a masked hint and `configured`.

Each email rule owns a copy of a library design. The library has 40 React Email designs in five sets: Barebone, Matte, Protocol, Arcane, and Studio. Suggested subjects and preheaders exist in English and French. A Shellui appearance theme can repaint a design's colors.

Newsletters are public lists with double opt-in. Broadcasts send one email to many people, each in their language.

## Three lanes

Auth, transactional, and bulk each have a worker. Sign-in mail uses the auth lane, so a broadcast does not delay it. See [Lanes](lanes.md).

## Configure and run

Copy [`.env.example`](../.env.example) to `.env`, set `SECRET_KEY`, and run migrations. Docker Compose is the local path in [Run email-service](getting-started.md).

The container runs database migrations, then Gunicorn, one delivery worker per lane, and a Celery worker with beat. `retry_webhooks` and `sweep_email_queue` run every minute. `purge_expired_data` runs every hour at minute 17. Redis is required when `DEBUG=false`. With `DEBUG=true` and no Redis, the web app still starts and the scheduled jobs do not run.

## Where to go next

Pick the row that matches what you are doing:

| You want to | Start with |
| --- | --- |
| Run it locally | [Run email-service](getting-started.md) |
| Set environment variables | [Configuration](configuration.md) |
| Issue a service key and call the API | [Service keys](authentication.md) and the [integration contract](integration.md) |
| See which events exist | [Events catalog](events.md) |
| Connect Resend or SMTP | [Providers](providers.md) |
| Turn an event into mail | [Email rules](rules.md) |
| Edit copy, languages, and colors | [Templates and translations](templates.md) and the [design library](library.md) |
| Collect sign-ups from a website | [Newsletters](newsletters.md) |
| Mail many people at once | [Broadcasts](broadcasts.md) |
| See why sign-in mail has its own queue | [Lanes](lanes.md) |
| Call an HTTPS endpoint when mail changes state | [Webhooks](actions.md) and [n8n](n8n.md) |
| Read past mail events | [Event log](event-log.md) |
| Lock down a production install | [Security](security.md) |
| Scrape Prometheus or read admin counts | [Metrics](metrics.md) |
| See retries and retention | [Scheduled jobs](scheduled-jobs.md) |
| Fix a send that did not go out | [Troubleshooting](troubleshooting.md) |

Source and the changelog are on [GitHub](https://github.com/shellui/email-service). These pages are built from `docs/` by [shellui/shellui](https://github.com/shellui/shellui) and published on [docs.shellui.com](https://docs.shellui.com) at `docs.shellui.com/email`.
