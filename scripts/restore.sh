#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(realpath "$0")")/.."
set -a
source versions.env
set +a
if [[ "$(id -u)" != 0 ]]; then echo 'Run via ./ae restore' >&2; exit 1; fi
snapshot="${1:-}"
if [[ -z "$snapshot" ]]; then echo 'Usage: ./ae restore SNAPSHOT_ID [RESTIC_REPOSITORY PASSWORD_FILE]' >&2; exit 1; fi
if [[ -n "${2:-}" && -n "${3:-}" ]]; then
  backup_repository="$2"
  backup_password_file="$3"
elif [[ -f /etc/agentic-environment/config.json ]]; then
  readarray -t backup_config < <(python3 - <<'PY'
import json
c=json.load(open('/etc/agentic-environment/config.json'))
print(c.get('backup_repository',''))
print(c.get('backup_password_file',''))
PY
  )
  backup_repository="${backup_config[0]}"
  backup_password_file="${backup_config[1]}"
else
  echo 'On a fresh host, supply the restic repository and password-file path' >&2
  exit 1
fi
if [[ -z "$backup_repository" || ! -f "$backup_password_file" ]]; then
  echo 'Restic repository and existing password file are required' >&2; exit 1
fi
export RESTIC_REPOSITORY="$backup_repository"
export RESTIC_PASSWORD_FILE="$backup_password_file"
if ! command -v restic >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y restic
fi
temp_dir="$(mktemp -d)"
trap 'rm -rf "$temp_dir"' EXIT
restic restore "$snapshot" --target "$temp_dir"
backup_dir="$(find "$temp_dir" -name windmill.dump -print -quit)"
backup_dir="$(dirname "$backup_dir")"
if [[ ! -f "$backup_dir/windmill.dump" ]]; then echo 'Backup does not contain Windmill database' >&2; exit 1; fi
if [[ ! -d "$backup_dir/repos" || ! -d "$backup_dir/workspaces" ]]; then
  echo 'Backup lacks the Git repositories or task worktrees required for recovery' >&2; exit 1
fi
if [[ ! -f .env ]]; then
  bash scripts/setup.sh
  cd /opt/agentic-environment
fi
systemctl stop ae-api hapi-runner hapi-hub
docker compose --env-file .env stop windmill_worker windmill_server
cp -a "$backup_dir/config/." /etc/agentic-environment/
cp -a "$backup_dir/hapi/." /var/lib/agentic-environment/hapi/
cp -a "$backup_dir/workspaces/." /home/ae-agent/workspaces/
cp -a "$backup_dir/repos/." /home/ae-agent/repos/
cp -a "$backup_dir/versions.env" versions.env
chown -R ae-platform:ae-platform /var/lib/agentic-environment/hapi
chown -R ae-agent:ae-agent /home/ae-agent/workspaces /home/ae-agent/repos
restored_password_file="$(python3 - <<'PY'
import json
print(json.load(open('/etc/agentic-environment/config.json')).get('backup_password_file',''))
PY
)"
if [[ -n "$restored_password_file" && ! -e "$restored_password_file" ]]; then
  install -d -m 700 "$(dirname "$restored_password_file")"
  install -m 600 "$backup_password_file" "$restored_password_file"
fi
docker compose --env-file .env exec -T postgres pg_restore -U ae -d windmill --clean --if-exists --no-owner < "$backup_dir/windmill.dump"
docker compose --env-file .env up -d windmill_server windmill_worker
bash scripts/setup.sh
echo 'Restore completed. Reauthenticate providers on this host as needed.'
