# email-service documentation

`email-service` sends Shellui mail. The HTTP API lives at `https://email.shellui.com`. This docs site is published separately so the API host stays an API.

## Contract

Other Shellui services and the admin app implement against [integration.md](integration.md). That page lists environment variables, every endpoint, request and response shapes, error codes, retries, and idempotency.

## Guides

- [Authentication](authentication.md): service keys and identity JWTs
- [Providers](providers.md): Resend, SMTP, and how to add Mailjet
- [Library](library.md): built-in designs and company templates
- [Templates](templates.md): event copies, editor documents, versions, and variables
- [Rules](rules.md): company email rules and built-in auth mail
- [Broadcasts](broadcasts.md): one email to many users, each in their language
- [Newsletters](newsletters.md): public sign-up with double opt-in, lists, and sending issues
- [Lanes](lanes.md): auth, transactional, and bulk
- [Events](events.md): catalog events for identity, storage, and hosting
- [Metrics](metrics.md): admin stats and Prometheus
- [Shellui Actions](actions.md): outbound webhooks
- [n8n](n8n.md): receiving those webhooks
- [Configuration](configuration.md): environment variables
- [Security](security.md): credentials, suppressions, and link hosts

Project setup is in `README.md`. Image publishing is in `PUBLISH.md`.
