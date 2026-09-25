#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(realpath "$0")")/.."
if [[ ! -x bin/wmill ]]; then echo 'Run ./ae setup first' >&2; exit 1; fi
if [[ "${1:-}" == "" ]]; then
  echo 'Usage: ./ae connect-windmill WORKSPACE_ID' >&2
  exit 1
fi
host="$(sed -n 's/^TAILSCALE_HOST=//p' .env | head -1)"
windmill_url="${AE_WINDMILL_URL:-https://$host:8444}"
bin/wmill workspace add agent-environment "$1" "$windmill_url"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
chmod 700 "$scratch"
python3 - "$scratch" <<'PY'
import json
import pathlib
import sys

values = {}
for line in pathlib.Path('.env').read_text().splitlines():
    if '=' in line and not line.startswith('#'):
        key, value = line.split('=', 1)
        values[key] = value
token = values['AE_API_TOKEN']
settings = {
    'api_url': (os.environ.get('AE_WINDMILL_API_URL', 'http://host.docker.internal:8765'), False),
    'api_token': (token, True),
    'windmill_url': (os.environ.get('AE_WINDMILL_INTERNAL_URL', 'http://windmill_server:8000'), False),
}
for name, (value, secret) in settings.items():
    path = pathlib.Path(sys.argv[1]) / f'{name}.variable.yaml'
    path.write_text(json.dumps({'value': value, 'is_secret': secret,
                                'description': 'Agent environment integration setting'}) + '\n')
    path.chmod(0o600)
PY
cd windmill
for name in api_url api_token windmill_url; do
  ../bin/wmill variable push "$scratch/$name.variable.yaml" "f/ae/$name" --plain-secrets >/dev/null
done
../bin/wmill generate-metadata f/ae --yes --parallel 1
for script in f/ae/submit.py f/ae/tick.py f/ae/cancel.py f/ae/waive_review.py f/ae/fallback.py f/ae/retry_publish.py f/ae/weekly_summary.py f/ae/accept.py f/ae/health_check.py f/ae/example.py; do
  ../bin/wmill script push "$script"
done
for name in tick weekly_summary health_check; do
  ../bin/wmill schedule push "f/ae/$name.schedule.yaml" "f/ae/$name"
done
echo 'Scripts and schedules uploaded. The task form is f/ae/submit.'
