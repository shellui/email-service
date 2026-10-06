#!/usr/bin/env bash
# Container entrypoint.
#
# Modes (first argument, default "web"):
#   web     migrate, then gunicorn, the scheduler (Celery worker with embedded beat) and
#           one delivery worker per lane (auth, transactional, bulk)
#   worker  the scheduler and the lane workers, no web server and no migrations; for a
#           dedicated worker container from the same image
#   other   run the given command, for example: python manage.py create_service_key …
#
# SCHEDULER_ENABLED=false keeps the scheduler out of this container, and
# EMAIL_WORKERS_ENABLED=false keeps the lane workers out. The scheduler needs REDIS_URL
# (or CELERY_BROKER_URL). Production settings already refuse to start without REDIS_URL.
# See docs/scheduled-jobs.md.
#
# Supervision: every process runs as a child of this script. A SIGTERM or SIGINT is
# forwarded to all of them, and if one exits the others are stopped and the container
# exits with that status, so Docker or Coolify restarts it.
set -euo pipefail

MODE="${1:-web}"
if [ "$#" -gt 0 ]; then
  shift
fi

LANES=(auth transactional bulk)

log() {
  echo "entrypoint: $*" >&2
}

# Same truthy values as config/settings.py (surrounding whitespace ignored).
is_true() {
  case "$(printf '%s' "${1:-}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')" in
    1 | true | yes | on) return 0 ;;
    *) return 1 ;;
  esac
}

# Set and not only whitespace (settings strips it).
is_set() {
  [ -n "$(printf '%s' "${1:-}" | tr -d '[:space:]')" ]
}

broker_configured() {
  is_set "${CELERY_BROKER_URL:-}" || is_set "${REDIS_URL:-}"
}

prepare_data_dir() {
  local sqlite_file sqlite_dir
  sqlite_file="${SQLITE_PATH:-/app/data/db.sqlite3}"
  sqlite_dir="$(dirname "${sqlite_file}")"
  mkdir -p "${sqlite_dir}"
  if [ "${sqlite_dir}" = "/app/data" ] && [ -z "${POSTGRES_DATABASE_URL:-}" ]; then
    log "INFO: For persistent data when using --rm, run with a named volume: -v email-service-data:/app/data"
  fi
}

GUNICORN_ARGS=(
  gunicorn
  --bind 0.0.0.0:8000
  --workers "${GUNICORN_WORKERS:-2}"
  --threads "${GUNICORN_THREADS:-2}"
  --timeout "${GUNICORN_TIMEOUT:-120}"
  --access-logfile -
  --error-logfile -
  config.wsgi:application
)

# One worker process with a small thread pool, plus beat embedded in it (--beat). The
# beat schedule file goes to /tmp, which is writable for appuser; losing it on restart
# only means beat starts a fresh schedule. --quiet drops the startup banner, which
# prints the broker URL.
SCHEDULER_ARGS=(
  celery -A config --quiet worker
  --beat
  --schedule "${CELERY_BEAT_SCHEDULE_FILE:-/tmp/celerybeat-schedule}"
  --pool threads
  --concurrency "${CELERY_WORKER_CONCURRENCY:-2}"
  --hostname "email-service@%h"
  --without-gossip
  --without-mingle
  --without-heartbeat
)

PIDS=()
STOP_SIGNAL=""

start() {
  "$@" &
  PIDS+=("$!")
}

start_scheduler() {
  if ! is_true "${SCHEDULER_ENABLED:-true}"; then
    log "SCHEDULER_ENABLED=false: scheduled jobs are not started in this container."
  elif ! broker_configured; then
    # Only reachable with DEBUG=true: production settings require REDIS_URL.
    log "WARNING: REDIS_URL is not set, so scheduled jobs (retry_webhooks, sweep_email_queue, purge_expired_data) are not running."
    log "WARNING: Set REDIS_URL to run them in this container. It is required when DEBUG is false. See docs/scheduled-jobs.md."
  else
    log "starting scheduler (Celery worker and beat)"
    start "${SCHEDULER_ARGS[@]}"
  fi
}

start_lane_workers() {
  if ! is_true "${EMAIL_WORKERS_ENABLED:-true}"; then
    log "EMAIL_WORKERS_ENABLED=false: lane workers are not started in this container."
    return
  fi
  local lane
  for lane in "${LANES[@]}"; do
    log "starting the ${lane} lane worker"
    start python manage.py run_email_worker --lane "${lane}"
  done
}

stop_children() {
  local pid
  for pid in "${PIDS[@]}"; do
    kill -TERM "${pid}" 2>/dev/null || true
  done
}

on_signal() {
  STOP_SIGNAL="$1"
  log "received ${STOP_SIGNAL}, stopping"
  stop_children
}

# Run with the commands already started in PIDS: forward SIGTERM and SIGINT, and exit as
# soon as one of them exits.
supervise() {
  trap 'on_signal SIGTERM' TERM
  trap 'on_signal SIGINT' INT

  local status=0
  set +e
  # Returns when the first child exits, or early when a trapped signal arrives.
  wait -n "${PIDS[@]}"
  status=$?
  stop_children
  wait "${PIDS[@]}"
  set -e
  if [ -n "${STOP_SIGNAL}" ]; then
    log "stopped"
    exit 0
  fi
  log "a process exited with status ${status}, stopping the container"
  if [ "${status}" -eq 0 ]; then
    status=1
  fi
  exit "${status}"
}

case "${MODE}" in
  web)
    prepare_data_dir
    python manage.py migrate --noinput
    start "${GUNICORN_ARGS[@]}"
    start_scheduler
    start_lane_workers
    supervise
    ;;
  worker)
    prepare_data_dir
    start_scheduler
    start_lane_workers
    if [ "${#PIDS[@]}" -eq 0 ]; then
      log "ERROR: worker mode has nothing to run. Set REDIS_URL for the scheduler, or EMAIL_WORKERS_ENABLED=true."
      exit 1
    fi
    supervise
    ;;
  *)
    prepare_data_dir
    exec "${MODE}" "$@"
    ;;
esac
