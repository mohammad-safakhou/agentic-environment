#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(realpath "$0")")/.."
if [[ "$(id -u)" != 0 ]]; then echo 'Run via ./ae enable-monitoring' >&2; exit 1; fi
echo 'Paste the Beszel public key shown when adding a system:'
IFS= read -r key
echo 'Paste the Beszel agent token:'
IFS= read -rs token
echo
if [[ -z "$key" || -z "$token" ]]; then echo 'Key and token are required' >&2; exit 1; fi
AE_BESZEL_KEY="$key" AE_BESZEL_TOKEN="$token" python3 - <<'PY'
import json, os, pathlib
path=pathlib.Path('.env')
lines=[line for line in path.read_text().splitlines() if not line.startswith(('BESZEL_KEY=','BESZEL_TOKEN=','COMPOSE_PROFILES='))]
lines.append('BESZEL_KEY='+json.dumps(os.environ['AE_BESZEL_KEY']))
lines.append('BESZEL_TOKEN='+json.dumps(os.environ['AE_BESZEL_TOKEN']))
lines.append('COMPOSE_PROFILES=monitoring')
path.write_text('\n'.join(lines)+'\n')
path.chmod(0o600)
PY
set -a
source versions.env
set +a
docker compose --env-file .env --profile monitoring up -d beszel_agent
echo 'In Beszel, set the system Host / IP to /beszel_socket/beszel.sock.'
