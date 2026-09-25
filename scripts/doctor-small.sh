#!/usr/bin/env bash
set -euo pipefail
fail=0
check() {
  if "$@" >/dev/null 2>&1; then printf 'ok   %s\n' "$*"; else printf 'FAIL %s\n' "$*"; fail=1; fi
}
loopback_port() {
  ss -ltn | awk '{print $4}' | grep -Fxq "127.0.0.1:$1"
}
check docker inspect ae-small-postgres
check docker exec ae-small-postgres pg_isready -U ae -d windmill
for unit in ae-small-windmill-server ae-small-windmill-worker ae-small-hub ae-small-runner ae-small-api; do
  check systemctl is-active --quiet "$unit"
done
check curl -fsS http://127.0.0.1:38000/api/health/status
check curl -fsS http://127.0.0.1:39444/health
for port in 38000 39444 38765 55434; do
  check loopback_port "$port"
done
if [[ -r /etc/ae-small/api-token ]]; then
  if curl -fsS -H "Authorization: Bearer $(cat /etc/ae-small/api-token)" \
      http://127.0.0.1:38765/health >/dev/null 2>&1; then
    printf 'ok   integration API health\n'
  else
    printf 'FAIL integration API health\n'
    fail=1
  fi
else
  printf 'FAIL API token unreadable; run doctor with sudo\n'
  fail=1
fi
exit "$fail"
