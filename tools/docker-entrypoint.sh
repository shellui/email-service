#!/usr/bin/env sh
# Modes: `web` (default) runs migrations then gunicorn. Any other command runs as is,
# for example `python manage.py run_email_worker --lane auth`.
set -eu

SQLITE_FILE="${SQLITE_PATH:-/app/data/db.sqlite3}"
SQLITE_DIR="$(dirname "${SQLITE_FILE}")"

mkdir -p "${SQLITE_DIR}"

if [ "$#" -gt 0 ] && [ "$1" != "web" ]; then
  exec "$@"
fi

if [ "${SQLITE_DIR}" = "/app/data" ] && [ -z "${POSTGRES_DATABASE_URL:-}" ]; then
  echo "INFO: For persistent data when using --rm, run with a named volume: -v email-service-data:/app/data" >&2
fi

python manage.py migrate --noinput

exec gunicorn \
  --bind 0.0.0.0:8000 \
  --workers "${GUNICORN_WORKERS:-2}" \
  --threads "${GUNICORN_THREADS:-2}" \
  --timeout "${GUNICORN_TIMEOUT:-120}" \
  --access-logfile - \
  --error-logfile - \
  config.wsgi:application
