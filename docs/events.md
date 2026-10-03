# Suggested events

`GET /api/v1/catalog` is the live list. This page records the v1 catalog so the admin app can show which events exist and which ones used to be on by default.

`default_enabled` is catalog metadata. It does not send mail. A company receives event mail only when an enabled email rule exists. See [rules.md](rules.md).

Auth-lane events get a built-in rule for every company, created on the first rules list or the first event. Those two events send on `POST /api/v1/events` without anyone creating a rule, and the rule cannot be deleted or turned off. Direct `/send` still works and does not consult rules. Identity can keep calling `/send` for magic links and invitations.

Every other event, including ones marked enabled below, returns `202` with `skipped_reason: no_rule` until the company creates a rule. That includes `hosting.deployment.failed`, `identity.user.invitation_revoked`, and `identity.scim.provisioning_conflict`, which used to send with no rule row.

An enabled rule with no recipient (`recipients: []`, or a static rule with no addresses) is accepted and skipped (`no_recipients`). See [integration.md](integration.md). Callers do not send `company_name`. email-service fills it from a name it already stored, or the mail uses the template's language default.

Events marked disabled below are noisy (every upload, every user edit). A company opts in by creating a rule.

Suggested documents are one heading, one short paragraph, and a button only when the event has an action URL. English and French. Variables are on the catalog response.

Login events `identity.auth.login.succeeded` and `identity.auth.login.failed` are not listed. Identity marks them `webhook: false`.

| Event | Lane | Catalog `default_enabled` | Why it is in the catalog |
| --- | --- | --- | --- |
| `identity.auth.magic_link.requested` | auth | enabled | Sign-in. TTL 120s. Built-in rule. `/send` still works. |
| `identity.user.invited` | auth | enabled | Invitation. TTL 300s. Built-in rule. `/send` still works. |
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

Source files: `defaults/<service>/<event>/` (`definition.json`, `subjects.json`, `en.json`, `fr.json`), generated from the catalog.
