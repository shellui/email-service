#!/usr/bin/env bash
# Fail when a production environment is missing required email-service settings.
set -euo pipefail

fail() {
  echo "prod-config: $1" >&2
  exit 1
}

[[ "${DEBUG:-false}" == "false" || "${DEBUG}" == "0" ]] || fail "DEBUG must be false"
[[ -n "${SECRET_KEY:-}" ]] || fail "SECRET_KEY is required"
[[ -n "${POSTGRES_DATABASE_URL:-}" ]] || fail "POSTGRES_DATABASE_URL is required (SQLite is not allowed)"
[[ -n "${REDIS_URL:-}" ]] || fail "REDIS_URL is required"
[[ -n "${IDENTITY_ISSUER:-}" ]] || fail "IDENTITY_ISSUER is required"
[[ -n "${IDENTITY_AUDIENCE:-}" ]] || fail "IDENTITY_AUDIENCE is required"
[[ -n "${EMAIL_CREDENTIALS_KEY:-}" ]] || fail "EMAIL_CREDENTIALS_KEY is required"
[[ -n "${EMAIL_VARIABLES_KEY:-}" ]] || fail "EMAIL_VARIABLES_KEY is required"
[[ -n "${EMAIL_HASH_PEPPER:-}" ]] || fail "EMAIL_HASH_PEPPER is required"
echo "prod-config check ok"
