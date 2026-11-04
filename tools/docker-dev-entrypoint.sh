#!/usr/bin/env bash
# Dev container entrypoint (Shellui local stack and bind-mounted checkouts).
#
# Modes (first argument, default "web"):
#   web     migrate, then Django runserver on 0.0.0.0:8000 (autoreload on bind mounts)
#   other   exec the given command (for example worker processes from compose overrides)
set -euo pipefail

MODE="${1:-web}"
if [ "$#" -gt 0 ]; then
  shift
fi

log() {
  echo "dev-entrypoint: $*" >&2
}

ensure_node_modules() {
  if [ -d /app/node_modules/.bin ]; then
    return 0
  fi
  if [ -d /opt/node/node_modules/.bin ]; then
    log "populating /app/node_modules from the image"
    mkdir -p /app/node_modules
    cp -a /opt/node/node_modules/. /app/node_modules/
  fi
}

case "${MODE}" in
  web)
    ensure_node_modules
    cd /app
    python manage.py migrate --noinput
    exec python manage.py runserver 0.0.0.0:8000
    ;;
  *)
    ensure_node_modules
    cd /app
    exec "${MODE}" "$@"
    ;;
esac
