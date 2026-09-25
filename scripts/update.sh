#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(realpath "$0")")/.."
set -a
source versions.env
set +a
if [[ "$(id -u)" != 0 ]]; then echo 'Run via ./ae update' >&2; exit 1; fi
active="$(docker compose --env-file .env exec -T postgres psql -U ae -d windmill -Atqc "SELECT count(*) FROM ae.tasks WHERE slot IS NOT NULL" 2>/dev/null || echo unknown)"
if [[ "$active" != 0 ]]; then echo "Wait for active tasks to drain (current: $active)" >&2; exit 1; fi
bash scripts/backup.sh
bash scripts/setup.sh
systemctl restart hapi-hub hapi-runner ae-api
bash scripts/doctor.sh
echo 'Pinned version set is healthy.'
