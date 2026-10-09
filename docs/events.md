---
title: Events catalog
sidebar_label: Events catalog
description: Catalog events for identity, storage, and hosting, and which ones send without a company rule.
---

# Events catalog

`GET /api/v1/catalog` is the live list. This page records the v1 catalog: lane, suggested English and French subjects, and whether a company must create a rule.

identity-service posts magic links, the staff notice, and invitations with `POST /api/v1/send`. It posts every other catalog event with `POST /api/v1/events`. storage-service and hosting-service post their events with `POST /api/v1/events`. Login events `identity.auth.login.succeeded` and `identity.auth.login.failed` are not in this catalog. Identity marks them `webhook: false`.

`default_enabled` is catalog metadata. It does not send mail. A company receives event mail only when an enabled email rule exists. See [rules.md](rules.md).

Auth-lane events get a built-in rule for every company, created on the first rules list or the first event. Those two events send on `POST /api/v1/events` without anyone creating a rule, and the rule cannot be deleted, turned off, or pointed at other recipients. A company cannot add its own rule on them (`rules_allowed: false` in the catalog). Direct `/send` still works and does not consult rules. Identity can keep calling `/send` for magic links and invitations.

Every other event, including ones marked enabled below, returns `202` with `skipped_reason: no_rule` until the company creates a rule. That includes `hosting.deployment.failed`, `identity.user.invitation_revoked`, and `identity.scim.provisioning_conflict`.

An enabled rule with no recipient (`recipients: []`, or a static rule with no addresses) is accepted and skipped (`no_recipients`). See [integration.md](integration.md). Callers may omit `company_name`. email-service then uses a name it already stored, or the provider From name.

Events marked disabled below are noisy (every upload, every user edit). A company opts in by creating a rule.

Each event has a suggested subject and preheader in English and French, its variables, `link_token` (the URL variable a design's main link becomes), and `default_template` (the [library](library.md) design for built-in rules and for direct sends before any copy exists: `barebone.activation` for magic links, `barebone.welcome` for invitations, `barebone.text-only` otherwise). Design text is English and edited per copy.

Login events `identity.auth.login.succeeded` and `identity.auth.login.failed` are not listed. Identity marks them `webhook: false`.

`identity.auth.magic_link.staff_blocked` is the exception to built-in rules: see [Built-in auth emails](integration.md#built-in-auth-emails).

| Event | Lane | Catalog `default_enabled` | Why it is in the catalog |
| --- | --- | --- | --- |
| `identity.auth.magic_link.requested` | auth | enabled | Sign-in. TTL 120s. Built-in rule. `/send` still works. |
| `identity.user.invited` | auth | enabled | Invitation. TTL 300s. Built-in rule. `/send` still works. |
| `identity.auth.magic_link.staff_blocked` | auth | enabled | Sent to a staff account instead of a magic link. TTL 300s. Built-in copy only: no rule, not editable, `/send` only. Not listed by `GET /api/v1/catalog`. |
| `identity.user.invitation_revoked` | transactional | enabled | The previous invitation must be known to be dead. Sends only after a company rule. |
| `identity.scim.provisioning_conflict` | transactional | enabled | An admin has to resolve a directory conflict. Sends only after a company rule. |
| `hosting.deployment.failed` | transactional | enabled | A failed release is operational. Sends only after a company rule. |
| `identity.user.created` | transactional | disabled | Fires often during SCIM and invites. |
| `identity.user.updated` | transactional | disabled | Fires on ordinary profile edits. |
| `identity.user.deleted` | transactional | disabled | Sensitive, and not every company wants a mail. |
| `identity.group.created` | transactional | disabled | Directory noise. |
| `identity.group.updated` | transactional | disabled | Directory noise. |
| `identity.group.deleted` | transactional | disabled | Directory noise. |
| `identity.group.membership_changed` | transactional | disabled | Directory noise. |
| `identity.scim.user.provisioned` | transactional | disabled | High volume. |
| `identity.scim.user.deprovisioned` | transactional | disabled | High volume. Create a rule if offboarding mail is required. |
| `identity.scim.token.created` | transactional | disabled | Security-sensitive. Off until a company creates a rule. |
| `identity.scim.token.revoked` | transactional | disabled | Same as token created. |
| `storage.bucket.created` | transactional | disabled | One-time provisioning. Low urgency. |
| `storage.object.uploaded` | transactional | disabled | One mail per file would flood the inbox. |
| `storage.object.deleted` | transactional | disabled | Same as uploads. |
| `hosting.app.created` | transactional | disabled | Informational. |
| `hosting.app.deleted` | transactional | disabled | Informational. |
| `hosting.deployment.created` | transactional | disabled | The artifact may not exist yet. |
| `hosting.deployment.succeeded` | transactional | disabled | Success is visible in the hosting UI. Create a rule for release mail. |

Source files: `defaults/<service>/<event>/` (`definition.json` with `link_token` and `default_template`, `subjects.json`, and `en.json` / `fr.json` with the subject and preheader), generated from the catalog by `export_defaults` in `apps/email/catalog.py`.
