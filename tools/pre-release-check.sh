#!/usr/bin/env bash
# Pre-release checklist for shellui/email-service. See PUBLISH.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

IMAGE="${1:-}"
if [[ "${1:-}" == "--image" ]]; then
  IMAGE="${2:-}"
fi

VERSION="$(python3 - <<'PY'
import tomllib
from pathlib import Path
data = tomllib.loads(Path("pyproject.toml").read_text())
print(data["project"]["version"])
PY
)"

echo "version=${VERSION}"
test -n "${VERSION}"

if [[ ! -f uv.lock ]]; then
  echo "uv.lock is missing"
  exit 1
fi

if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo ".env is tracked"
  exit 1
fi

export SECRET_KEY="${SECRET_KEY:-pre-release-check-not-a-secret}"
export DEBUG=true
export IDENTITY_JWKS_URL="${IDENTITY_JWKS_URL:-http://localhost:8000/.well-known/jwks.json}"

if command -v uv >/dev/null 2>&1; then
  uv run python manage.py check
else
  python manage.py check
fi

if [[ -n "${IMAGE}" ]]; then
  docker build -t "${IMAGE}" .
  echo "built ${IMAGE}"
fi

echo "pre-release check ok"
