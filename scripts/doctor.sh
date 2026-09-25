#!/usr/bin/env bash
set -euo pipefail
fail=0
check() {
  if "$@" >/dev/null 2>&1; then printf 'ok  %s\n' "$*"; else printf 'FAIL %s\n' "$*"; fail=1; fi
}
check command -v docker
check command -v tailscale
check command -v git
check command -v gh
check docker compose version
check systemctl is-active --quiet hapi-hub
check systemctl is-active --quiet hapi-runner
check systemctl is-active --quiet ae-api
check curl --fail --silent http://127.0.0.1:3006/health
check curl --fail --silent http://127.0.0.1:8000/api/health/status
check tailscale status
exit "$fail"
