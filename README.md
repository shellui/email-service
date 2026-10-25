# email-service

Templates and sends email for Shellui services.

[![CI](https://github.com/shellui/email-service/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/shellui/email-service/actions/workflows/ci.yml)
[![Docker Hub](https://img.shields.io/docker/v/shellui/email-service?sort=semver&label=docker)](https://hub.docker.com/r/shellui/email-service)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

identity-service, storage-service and hosting-service send their mail through email-service: sign-in links, invitations and event notifications. Each company brings its own Resend or SMTP account, picks a design set and edits its templates in the [admin](https://github.com/shellui/admin) app. A platform fallback covers sign-in mail when a company has no provider.

## Get started

You need Docker and Git.

```bash
git clone https://github.com/shellui/email-service.git
cd email-service
cp .env.example .env   # then set SECRET_KEY
docker compose up --build
```

The API runs on [http://localhost:8003](http://localhost:8003). `/api/docs/` is the OpenAPI reference and `/admin/` is Django admin. Compose starts Redis. Postgres is required when `DEBUG=false`. The full setup, with workers, is in [Run email-service](docs/getting-started.md).

Other services call it with an `esk_` service key. The contract they follow is in [docs/integration.md](docs/integration.md).

## Configuration

For production, `python manage.py generate_env --host email.example.com` prints the required settings with fresh secrets.

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Django secret, 50+ characters when `DEBUG=false` |
| `PUBLIC_BASE_URL` | Public origin, used in unsubscribe links |
| `IDENTITY_JWKS` | Pinned identity public keys, required when `DEBUG=false` |
| `EMAIL_CREDENTIALS_KEY` | Encrypts company provider credentials |
| `RESEND_API_KEY` | Platform fallback provider (Resend). For SES set `EMAIL_FALLBACK_PROVIDER=ses` and the `SES_*` keys |

Every setting is in [Configuration](docs/configuration.md). Before the first real send, finish the [Resend account steps](docs/providers.md#resend-account-before-mail-leaves).

## Documentation

The handbook lives in [`docs/`](docs/index.md) and is published at [docs.shellui.com/email](https://docs.shellui.com/email).

- [Integration contract](docs/integration.md), [events](docs/events.md) and [authentication](docs/authentication.md)
- [Providers](docs/providers.md), [email rules](docs/rules.md) and [lanes](docs/lanes.md)
- [Templates](docs/templates.md) and the [design library](docs/library.md)
- [Broadcasts](docs/broadcasts.md) and [newsletters](docs/newsletters.md)
- [Webhooks](docs/actions.md), [metrics](docs/metrics.md), [security](docs/security.md) and [troubleshooting](docs/troubleshooting.md)

## Development

```bash
uv sync
uv run python manage.py migrate
uv run python manage.py runserver 8003
uv run python manage.py test
```

To preview the docs site, clone [shellui/shellui](https://github.com/shellui/shellui) next to this repository and run `DOCS_SERVICES=email pnpm docs:start` there. See [Build the docs site](https://github.com/shellui/shellui/blob/main/docs/docs-site.md).

Releases go to Docker Hub as `shellui/email-service`. The checklist is in [PUBLISH.md](PUBLISH.md) and changes are in [CHANGELOG.md](CHANGELOG.md).

## Contributing

Open an issue or a pull request against `develop`. CI runs the tests, a dependency audit, a secret scan, a link check and the docs build. Coding agents start with [AGENTS.md](AGENTS.md).

## Shellui projects

| Project | What it does |
| --- | --- |
| [shellui](https://github.com/shellui/shellui) | The shell, CLI and SDK |
| [admin](https://github.com/shellui/admin) | Administration app inside the shell |
| [files](https://github.com/shellui/files) | File explorer for storage-service |
| [identity-service](https://github.com/shellui/identity-service) | Sign-in, companies and tokens |
| [storage-service](https://github.com/shellui/storage-service) | File storage |
| [hosting-service](https://github.com/shellui/hosting-service) | Deploys and serves Shellui apps |
| [email-service](https://github.com/shellui/email-service) | Templates and sends email |
| [skills](https://github.com/shellui/skills) | Agent Skills for coding agents |
| [playground](https://github.com/shellui/playground) | Live demo of the shell |

## License

[MIT](LICENSE)
