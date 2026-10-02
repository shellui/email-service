# Suggested events

`GET /api/v1/catalog` is the live list. This page records the v1 defaults so the admin app can show the same on and off states before a company customizes a rule.

Enabled means a new company receives the mail when a service calls `POST /api/v1/events`, with no `EmailRule` row yet. Direct `/send` does not consult this flag. Identity should still call `/send` for the two auth templates so a later rule change cannot stop sign-in mail.

Disabled events are noisy (every upload, every user edit). A company turns them on from the admin API.

Languages: `en` and `fr` for every row. Variables are on the catalog response.

Login events `identity.auth.login.succeeded` and `identity.auth.login.failed` are not listed. Identity marks them `webhook: false`.

| Event | Lane | Default enabled | Why |
| --- | --- | --- | --- |
| `identity.auth.magic_link.requested` | auth | enabled | Sign-in. TTL 120s. Send with `/send`. |
| `identity.user.invited` | auth | enabled | Invitation. TTL 300s. Send with `/send`. |
| `identity.user.invitation_revoked` | transactional | enabled | The previous invitation must be known to be dead. |
| `identity.scim.provisioning_conflict` | transactional | enabled | An admin has to resolve a directory conflict. |
| `hosting.deployment.failed` | transactional | enabled | A failed release is operational. |
| `identity.user.created` | transactional | disabled | Fires often during SCIM and invites. |
| `identity.user.updated` | transactional | disabled | Fires on ordinary profile edits. |
| `identity.user.deleted` | transactional | disabled | Sensitive, and not every company wants a mail. |
| `identity.group.created` | transactional | disabled | Directory noise. |
| `identity.group.updated` | transactional | disabled | Directory noise. |
| `identity.group.deleted` | transactional | disabled | Directory noise. |
| `identity.group.membership_changed` | transactional | disabled | Directory noise. |
| `identity.scim.user.provisioned` | transactional | disabled | High volume. |
| `identity.scim.user.deprovisioned` | transactional | disabled | High volume. Turn on if offboarding mail is required. |
| `identity.scim.token.created` | transactional | disabled | Security-sensitive. Off until a company opts in. |
| `identity.scim.token.revoked` | transactional | disabled | Same as token created. |
| `storage.bucket.created` | transactional | disabled | One-time provisioning. Low urgency. |
| `storage.object.uploaded` | transactional | disabled | One mail per file would flood the inbox. |
| `storage.object.deleted` | transactional | disabled | Same as uploads. |
| `hosting.app.created` | transactional | disabled | Informational. |
| `hosting.app.deleted` | transactional | disabled | Informational. |
| `hosting.deployment.created` | transactional | disabled | The artifact may not exist yet. |
| `hosting.deployment.succeeded` | transactional | disabled | Success is visible in the hosting UI. Opt in for release mail. |

Source files: `defaults/<service>/<event>/` (`definition.json`, `subjects.json`, `en.json`, `fr.json`), generated from the catalog.
