#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(realpath "$0")")/.."
set -a
source versions.env
set +a

if [[ "$(id -u)" != 0 ]]; then echo 'Run via ./ae setup' >&2; exit 1; fi
if [[ "$(uname -m)" != x86_64 ]] || ! grep -q '^VERSION_ID="24.04"' /etc/os-release; then
  echo 'Ubuntu 24.04 x86-64 is required' >&2; exit 1
fi
if (( $(getconf _NPROCESSORS_ONLN) < 4 )); then echo 'At least 4 CPUs required' >&2; exit 1; fi
if (( $(awk '/MemTotal/ {print int($2/1024/1024)}' /proc/meminfo) < 14 )); then
  echo 'At least 16 GB RAM is required' >&2; exit 1
fi
if (( $(df -BG --output=avail . | tail -1 | tr -dc '0-9') < 80 )); then
  echo 'At least 80 GB free disk is required' >&2; exit 1
fi
for account in ae-platform ae-agent; do
  if ! id "$account" >/dev/null 2>&1; then
    echo "Create the $account account manually before running setup; see README.md" >&2
    exit 1
  fi
done
if [[ "$(getent passwd ae-platform | cut -d: -f6)" != /var/lib/agentic-environment ]] ||
   [[ "$(getent passwd ae-agent | cut -d: -f6)" != /home/ae-agent ]]; then
  echo 'Account home directories must match the paths in README.md' >&2; exit 1
fi
if id -nG ae-agent | tr ' ' '\n' | grep -Eq '^(sudo|docker)$'; then
  echo 'Remove ae-agent from sudo and docker groups before setup' >&2; exit 1
fi
for port in 3000 3006 5433 8000 8090 8765 9898; do
  if ss -ltn | awk '{print $4}' | grep -Eq ":${port}$"; then
    if [[ ! -f .env && ! -f /opt/agentic-environment/.env ]]; then echo "Port $port is already in use" >&2; exit 1; fi
  fi
done

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl git docker.io docker-compose-v2 python3 python3-venv python3-pip jq restic postgresql-client sqlite3 rsync
systemctl enable --now docker
if [[ "$(pwd)" != /opt/agentic-environment ]]; then
  install -d -m 755 /opt/agentic-environment
  rsync -a --exclude=.git --exclude=.env --exclude=.venv --exclude=runtime \
    --exclude=bin --exclude=backups --exclude=__pycache__ ./ /opt/agentic-environment/
  cd /opt/agentic-environment
fi
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg \
  -o /usr/share/keyrings/tailscale-archive-keyring.gpg
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list \
  -o /etc/apt/sources.list.d/tailscale.list
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y "tailscale=${TAILSCALE_VERSION}"
systemctl enable --now tailscaled
if ! tailscale status >/dev/null 2>&1; then
  echo 'Complete the Tailscale sign-in shown below.'
  tailscale up
fi

install -d -m 700 -o ae-platform -g ae-platform /var/lib/agentic-environment/hapi
install -d -m 700 /var/lib/agentic-environment/gh
install -d -m 700 -o ae-agent -g ae-agent /home/ae-agent/repos /home/ae-agent/workspaces
install -d -m 700 /etc/agentic-environment
install -d -m 700 backups

if [[ ! -x runtime/node/bin/node || "$(runtime/node/bin/node -v)" != "v${NODE_VERSION}" ]]; then
  mkdir -p runtime
  curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" -o /tmp/ae-node.tar.xz
  curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" -o /tmp/ae-node-shasums.txt
  grep " node-v${NODE_VERSION}-linux-x64.tar.xz$" /tmp/ae-node-shasums.txt | (cd /tmp && sha256sum -c -)
  tar -xJf /tmp/ae-node.tar.xz -C runtime
  ln -sfn "node-v${NODE_VERSION}-linux-x64" runtime/node
fi
export PATH="$(pwd)/runtime/node/bin:$PATH"
mkdir -p runtime/npm bin
npm install --prefix runtime/npm --no-audit --no-fund \
  "@twsxtd/hapi@${HAPI_VERSION}" "@openai/codex@${CODEX_VERSION}" \
  "@anthropic-ai/claude-code@${CLAUDE_VERSION}" "opencode-ai@${OPENCODE_VERSION}" \
  "windmill-cli@${WINDMILL_VERSION}"
ln -sfn ../runtime/npm/node_modules/@twsxtd/hapi/bin/hapi.cjs bin/hapi
ln -sfn ../runtime/npm/node_modules/@openai/codex/bin/codex.js bin/codex
ln -sfn ../runtime/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe bin/claude
ln -sfn ../runtime/npm/node_modules/opencode-ai/bin/opencode.exe bin/opencode
ln -sfn ../runtime/npm/node_modules/windmill-cli/esm/main.js bin/wmill
if [[ ! -x "runtime/gh_${GH_VERSION}_linux_amd64/bin/gh" ]]; then
  curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" -o /tmp/ae-gh.tar.gz
  curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_checksums.txt" -o /tmp/ae-gh-checksums.txt
  grep " gh_${GH_VERSION}_linux_amd64.tar.gz$" /tmp/ae-gh-checksums.txt | \
    sed "s/gh_${GH_VERSION}_linux_amd64.tar.gz/ae-gh.tar.gz/" | (cd /tmp && sha256sum -c -)
  tar -xzf /tmp/ae-gh.tar.gz -C runtime
fi
ln -sfn "../runtime/gh_${GH_VERSION}_linux_amd64/bin/gh" bin/gh
python3 -m venv .venv
.venv/bin/pip install --disable-pip-version-check -r requirements.txt

python3 - <<'PY'
import json, pathlib, secrets
root=pathlib.Path('/etc/agentic-environment')
config=root/'config.json'
if not config.exists():
    config.write_text(pathlib.Path('config.example.json').read_text())
    config.chmod(0o600)
env=pathlib.Path('.env')
if not env.exists():
    env.write_text('POSTGRES_PASSWORD='+secrets.token_urlsafe(32)+'\n')
    env.chmod(0o600)
token=root/'api.token'
if not token.exists():
    token.write_text(secrets.token_urlsafe(40))
    token.chmod(0o600)
PY
TAILSCALE_HOST="$(tailscale status --json | jq -r '.Self.DNSName' | sed 's/\.$//')"
if [[ -z "$TAILSCALE_HOST" || "$TAILSCALE_HOST" == null ]]; then echo 'Tailscale hostname unavailable' >&2; exit 1; fi
AE_SETUP_HOST="$TAILSCALE_HOST" python3 - <<'PY'
import os, pathlib
path=pathlib.Path('.env')
lines=[line for line in path.read_text().splitlines() if not line.startswith(('TAILSCALE_HOST=','WINDMILL_PUBLIC_URL='))]
host=os.environ['AE_SETUP_HOST']
lines += [f'TAILSCALE_HOST={host}', f'WINDMILL_PUBLIC_URL=https://{host}:8444']
path.write_text('\n'.join(lines)+'\n')
path.chmod(0o600)
PY
if ! grep -q '^AE_API_TOKEN=' .env; then
  printf 'AE_API_TOKEN=%s\n' "$(cat /etc/agentic-environment/api.token)" >> .env
fi

for name in ae-api hapi-hub hapi-runner ae-backup; do
  install -m 644 "systemd/$name.service" "/etc/systemd/system/$name.service"
done
install -m 644 systemd/ae-backup.timer /etc/systemd/system/ae-backup.timer
cat >/etc/agentic-environment/runner.env <<EOF
PATH=$(pwd)/bin:$(pwd)/runtime/node/bin:/usr/local/bin:/usr/bin:/bin
HAPI_API_URL=http://127.0.0.1:3006
EOF
chmod 600 /etc/agentic-environment/runner.env
printf 'HAPI_PUBLIC_URL=https://%s:8445\n' "$TAILSCALE_HOST" > /etc/agentic-environment/hub.env
chmod 600 /etc/agentic-environment/hub.env
systemctl daemon-reload
systemctl enable --now hapi-hub
for attempt in $(seq 1 30); do
  [[ -f /var/lib/agentic-environment/hapi/settings.json ]] && break
  sleep 1
done
HAPI_TOKEN="$(jq -r '.cliApiToken' /var/lib/agentic-environment/hapi/settings.json)"
if [[ -z "$HAPI_TOKEN" || "$HAPI_TOKEN" == null ]]; then echo 'HAPI access token unavailable' >&2; exit 1; fi
printf 'CLI_API_TOKEN=%s\n' "$HAPI_TOKEN" >> /etc/agentic-environment/runner.env
cat >/etc/agentic-environment/api.env <<EOF
AE_CONFIG=/etc/agentic-environment/config.json
AE_DATABASE_URL=postgresql://ae:$(sed -n 's/^POSTGRES_PASSWORD=//p' .env)@127.0.0.1:5433/windmill
AE_API_TOKEN=$(cat /etc/agentic-environment/api.token)
AE_HAPI_TOKEN=$HAPI_TOKEN
AE_HAPI_URL=http://127.0.0.1:3006
CLI_API_TOKEN=$HAPI_TOKEN
HAPI_API_URL=http://127.0.0.1:3006
AE_BIND_HOST=$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}')
GH_CONFIG_DIR=/var/lib/agentic-environment/gh
GIT_CONFIG_GLOBAL=/var/lib/agentic-environment/gh/gitconfig
PATH=$(pwd)/bin:$(pwd)/runtime/node/bin:/usr/local/bin:/usr/bin:/bin
EOF
chmod 600 /etc/agentic-environment/api.env
docker compose --env-file .env up -d
systemctl enable --now hapi-runner ae-api
tailscale serve --bg --https=8443 http://127.0.0.1:3000
tailscale serve --bg --https=8444 http://127.0.0.1:8000
tailscale serve --bg --https=8445 http://127.0.0.1:3006
tailscale serve --bg --https=8446 http://127.0.0.1:8090
tailscale serve --bg --https=8447 http://127.0.0.1:9898
for attempt in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:3006/health >/dev/null 2>&1 && \
     curl -fsS http://127.0.0.1:8000/api/health/status >/dev/null 2>&1 && \
     systemctl is-active --quiet ae-api; then break; fi
  sleep 2
done
bash scripts/doctor.sh
echo "Home: https://${TAILSCALE_HOST}:8443"
echo 'Next: sign in to Windmill, HAPI, Beszel, Backrest, and coding providers; run ./ae register for each repository.'
