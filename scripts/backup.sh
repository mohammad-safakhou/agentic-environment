#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(realpath "$0")")/.."
set -a
source versions.env
set +a
if [[ "$(id -u)" != 0 ]]; then echo 'Run via ./ae backup' >&2; exit 1; fi
readarray -t backup_config < <(python3 - <<'PY'
import json
c=json.load(open('/etc/agentic-environment/config.json'))
print(c.get('backup_repository',''))
print(c.get('backup_password_file',''))
PY
)
if [[ -z "${backup_config[0]}" || -z "${backup_config[1]}" ]]; then
  echo 'Configure backup_repository and backup_password_file before backing up' >&2; exit 1
fi
if [[ "${backup_config[0]}" != *:* ]]; then
  echo 'Backup repository must be an off-server restic destination' >&2; exit 1
fi
export RESTIC_REPOSITORY="${backup_config[0]}"
export RESTIC_PASSWORD_FILE="${backup_config[1]}"
if ! restic snapshots >/dev/null 2>&1; then restic init; fi
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p backups/"$stamp"
chmod 700 backups/"$stamp"
docker compose --env-file .env exec -T postgres pg_dump -U ae -Fc windmill > "backups/$stamp/windmill.dump"
cp -a /etc/agentic-environment "backups/$stamp/config"
cp -a /var/lib/agentic-environment/hapi "backups/$stamp/hapi"
if [[ -f /var/lib/agentic-environment/hapi/hapi.db ]]; then
  rm -f "backups/$stamp/hapi/hapi.db" "backups/$stamp/hapi/hapi.db-wal" "backups/$stamp/hapi/hapi.db-shm"
  sqlite3 /var/lib/agentic-environment/hapi/hapi.db ".backup 'backups/$stamp/hapi/hapi.db'"
fi
cp -a /home/ae-agent/workspaces "backups/$stamp/workspaces"
cp -a /home/ae-agent/repos "backups/$stamp/repos"
cp -a versions.env "backups/$stamp/versions.env"
restic backup "backups/$stamp" --tag agentic-environment
restic forget --keep-daily 7 --keep-weekly 4 --prune
rm -rf "backups/$stamp"
systemctl enable --now ae-backup.timer >/dev/null 2>&1 || true
echo "Backup completed: $stamp"
