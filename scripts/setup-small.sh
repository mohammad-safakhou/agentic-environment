#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(realpath "$0")")/.."
set -a
source versions.env
set +a

if [[ $(id -u) != 0 ]]; then echo 'Run with sudo or ./ae-small setup' >&2; exit 1; fi
if [[ $(uname -m) != x86_64 ]] || ! grep -q '^ID=ubuntu$' /etc/os-release; then
  echo 'This small-server profile requires Ubuntu x86-64' >&2; exit 1
fi
if [[ $(getent passwd ae-lab | cut -d: -f6) != /home/ae-lab ]]; then
  echo 'Create the ae-lab account with home /home/ae-lab before setup' >&2; exit 1
fi
if id -nG ae-lab | tr ' ' '\n' | grep -Eq '^(sudo|docker)$'; then
  echo 'Remove ae-lab from sudo and docker groups before setup' >&2; exit 1
fi
for binary in curl docker git jq python3 rsync sha256sum ss sudo systemctl tar; do
  command -v "$binary" >/dev/null || { echo "Missing command: $binary" >&2; exit 1; }
done
systemctl is-active --quiet docker || { echo 'The existing Docker service must be running' >&2; exit 1; }
if (( $(df -BG --output=avail / | tail -1 | tr -dc '0-9') < 5 )) && [[ ! -f /etc/ae-small/config.json ]]; then
  echo 'At least 5 GB free disk is needed for the first small-server install' >&2; exit 1
fi
if [[ ! -f /etc/ae-small/config.json ]]; then
  for port in 38000 39444 38765 55434; do
    if ss -ltn | awk '{print $4}' | grep -Eq ":${port}$"; then
      echo "Port $port is already in use; no service was changed" >&2; exit 1
    fi
  done
fi
if docker container inspect ae-small-postgres >/dev/null 2>&1; then
  image="$(docker inspect ae-small-postgres --format '{{.Config.Image}}')"
  volume="$(docker inspect ae-small-postgres --format '{{range .Mounts}}{{.Name}}{{end}}')"
  if [[ "$image" != "postgres:${SMALL_POSTGRES_VERSION}" || "$volume" != ae_small_pgdata ]]; then
    echo 'ae-small-postgres exists but is not the expected project container' >&2; exit 1
  fi
fi
if [[ ${1:-} == --preflight ]]; then
  echo 'Preflight passed: account, Docker, tools, disk and ports are ready.'
  exit 0
fi
if [[ ${1:-} != --setup ]]; then echo 'Usage: setup-small.sh --preflight|--setup' >&2; exit 1; fi

install -d -m 755 /opt/ae-small
if [[ $(pwd) != /opt/ae-small ]]; then
  rsync -a --exclude=.git --exclude=.env --exclude=.venv --exclude=.codebase-memory \
    --exclude=__pycache__ --exclude=bin --exclude=npm --exclude=runtime \
    --exclude=python --exclude=venv --exclude=backups ./ /opt/ae-small/
  cd /opt/ae-small
fi
install -d -m 755 bin npm python runtime
install -d -m 700 /etc/ae-small /etc/ae-small/gh /etc/ae-small/wmill-cli
install -d -m 700 -o ae-lab -g ae-lab /home/ae-lab/hapi-hub /home/ae-lab/hapi-runner \
  /home/ae-lab/windmill /home/ae-lab/repos /home/ae-lab/workspaces

download_checked() {
  local url="$1" expected="$2" target="$3" temp
  temp="$(mktemp /tmp/ae-small-download.XXXXXX)"
  if ! curl -fL --retry 3 --retry-delay 2 --silent --show-error "$url" -o "$temp"; then
    rm -f "$temp"
    return 1
  fi
  printf '%s  %s\n' "$expected" "$temp" | sha256sum -c - >/dev/null || { rm -f "$temp"; return 1; }
  mv "$temp" "$target"
}

if [[ ! -x bin/uv ]]; then
  archive="$(mktemp /tmp/ae-small-uv.XXXXXX)"
  download_checked "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz" \
    "$UV_SHA256" "$archive"
  tar -xzf "$archive" -C bin --strip-components=1 uv-x86_64-unknown-linux-gnu/uv
  rm -f "$archive"
  chmod 755 bin/uv
fi
if [[ ! -x bin/windmill ]]; then
  download_checked "https://github.com/windmill-labs/windmill/releases/download/v${WINDMILL_VERSION}/windmill-amd64" \
    "$WINDMILL_AMD64_SHA256" bin/windmill
  chmod 755 bin/windmill
fi
if [[ ! -x runtime/node/bin/node || "$(runtime/node/bin/node -v)" != "v${NODE_VERSION}" ]]; then
  node_archive="$(mktemp /tmp/ae-small-node.XXXXXX)"
  node_checksums="$(mktemp /tmp/ae-small-node-checksums.XXXXXX)"
  curl -fL --silent --show-error "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" -o "$node_archive"
  curl -fL --silent --show-error "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" -o "$node_checksums"
  grep " node-v${NODE_VERSION}-linux-x64.tar.xz$" "$node_checksums" | \
    sed "s#node-v${NODE_VERSION}-linux-x64.tar.xz#$node_archive#" | sha256sum -c - >/dev/null
  tar -xJf "$node_archive" -C runtime
  ln -sfn "node-v${NODE_VERSION}-linux-x64" runtime/node
  rm -f "$node_archive" "$node_checksums"
fi
export PATH=/opt/ae-small/runtime/node/bin:$PATH
export UV_PYTHON_INSTALL_DIR=/opt/ae-small/python
bin/uv python install "$SMALL_PYTHON_VERSION"
python_path="$(bin/uv python find "$SMALL_PYTHON_VERSION" --managed-python)"
if [[ ! -x venv/bin/python ]]; then bin/uv venv --python "$python_path" venv; fi
bin/uv pip install --python venv/bin/python -r requirements.txt
npm install --prefix npm --no-audit --no-fund \
  "@twsxtd/hapi@${HAPI_VERSION}" "@openai/codex@${CODEX_VERSION}" \
  "windmill-cli@${WINDMILL_VERSION}"
ln -sfn ../npm/node_modules/@twsxtd/hapi-linux-x64/bin/hapi bin/hapi
ln -sfn ../npm/node_modules/@openai/codex/bin/codex.js bin/codex
ln -sfn ../npm/node_modules/windmill-cli/esm/main.js bin/wmill
for binary in bin/hapi bin/codex bin/wmill; do
  [[ -x "$binary" ]] || { echo "$binary did not install correctly" >&2; exit 1; }
done
if [[ ! -x /home/ae-lab/.local/bin/claude ||
      "$(sudo -u ae-lab -H /home/ae-lab/.local/bin/claude --version)" != "${CLAUDE_VERSION} (Claude Code)" ]]; then
  claude_installer="$(mktemp /tmp/ae-small-claude-install.XXXXXX)"
  curl -fsSL https://claude.ai/install.sh -o "$claude_installer"
  sudo -u ae-lab -H bash "$claude_installer" "$CLAUDE_VERSION"
  rm -f "$claude_installer"
fi
ln -sfn /home/ae-lab/.local/bin/claude bin/claude
[[ -x bin/claude ]] || { echo 'Claude Code did not install correctly' >&2; exit 1; }
if [[ ! -x bin/gh ]]; then
  archive="$(mktemp /tmp/ae-small-gh.XXXXXX)"
  checksums="$(mktemp /tmp/ae-small-gh-checksums.XXXXXX)"
  curl -fL --silent --show-error "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" -o "$archive"
  curl -fL --silent --show-error "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_checksums.txt" -o "$checksums"
  grep " gh_${GH_VERSION}_linux_amd64.tar.gz$" "$checksums" | sed "s#gh_${GH_VERSION}_linux_amd64.tar.gz#$archive#" | sha256sum -c - >/dev/null
  tar -xzf "$archive" -C bin --strip-components=2 "gh_${GH_VERSION}_linux_amd64/bin/gh"
  chmod 755 bin/gh
  rm -f "$archive" "$checksums"
fi

python3 - <<'PY'
import json, pathlib, secrets
root = pathlib.Path('/etc/ae-small')
config = root / 'config.json'
if not config.exists():
    value = json.loads(pathlib.Path('/opt/ae-small/config.example.json').read_text())
    value['minimum_free_gb'] = 2
    value['fallback_worker'] = 'claude'
    config.write_text(json.dumps(value, indent=2) + '\n')
    config.chmod(0o600)
for name in ('postgres-password', 'api-token'):
    path = root / name
    if not path.exists():
        path.write_text(secrets.token_urlsafe(40))
        path.chmod(0o600)
PY
printf 'POSTGRES_PASSWORD=%s\n' "$(cat /etc/ae-small/postgres-password)" > /etc/ae-small/postgres.env
chmod 600 /etc/ae-small/postgres.env
printf 'AE_API_TOKEN=%s\n' "$(cat /etc/ae-small/api-token)" > .env
chmod 600 .env

docker volume create ae_small_pgdata >/dev/null
if ! docker container inspect ae-small-postgres >/dev/null 2>&1; then
  docker run -d --name ae-small-postgres --restart unless-stopped \
    --memory=512m --cpus=0.5 --env-file /etc/ae-small/postgres.env \
    -e POSTGRES_USER=ae -e POSTGRES_DB=windmill \
    -v ae_small_pgdata:/var/lib/postgresql/data \
    -p 127.0.0.1:55434:5432 "postgres:${SMALL_POSTGRES_VERSION}" >/dev/null
else
  docker start ae-small-postgres >/dev/null || true
fi
for attempt in $(seq 1 45); do
  if docker exec ae-small-postgres pg_isready -U ae -d windmill >/dev/null 2>&1; then break; fi
  sleep 2
done
docker exec ae-small-postgres pg_isready -U ae -d windmill >/dev/null

database_url="postgres://ae:$(cat /etc/ae-small/postgres-password)@127.0.0.1:55434/windmill"
cat > /etc/ae-small/windmill.env <<EOF
DATABASE_URL=$database_url
BASE_URL=http://127.0.0.1:38000
WINDMILL_DIR=/home/ae-lab/windmill
UV_PYTHON_INSTALL_DIR=/opt/ae-small/python
UV_PATH=/opt/ae-small/bin/uv
PYTHON_PATH=$python_path
PATH=/opt/ae-small/bin:/opt/ae-small/runtime/node/bin:/usr/local/bin:/usr/bin:/bin
EOF
chmod 600 /etc/ae-small/windmill.env

cat > /etc/systemd/system/ae-small-windmill-server.service <<'EOF'
[Unit]
Description=Agent environment small Windmill server
After=network-online.target docker.service
Requires=docker.service

[Service]
User=ae-lab
Group=ae-lab
Environment=HOME=/home/ae-lab
Environment=MODE=server
Environment=PORT=38000
Environment=SERVER_BIND_ADDR=127.0.0.1
EnvironmentFile=/etc/ae-small/windmill.env
WorkingDirectory=/home/ae-lab/windmill
ExecStart=/opt/ae-small/bin/windmill
Restart=on-failure
RestartSec=5
MemoryMax=900M
CPUWeight=20
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/ae-small-windmill-worker.service <<'EOF'
[Unit]
Description=Agent environment small Windmill worker
After=ae-small-windmill-server.service
Requires=ae-small-windmill-server.service

[Service]
User=ae-lab
Group=ae-lab
Environment=HOME=/home/ae-lab
Environment=MODE=worker
Environment=WORKER_GROUP=default
EnvironmentFile=/etc/ae-small/windmill.env
WorkingDirectory=/home/ae-lab/windmill
ExecStart=/opt/ae-small/bin/windmill
Restart=on-failure
RestartSec=5
MemoryMax=1000M
CPUWeight=20
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/ae-small-hub.service <<'EOF'
[Unit]
Description=Agent environment small HAPI hub
After=network-online.target

[Service]
User=ae-lab
Group=ae-lab
Environment=HOME=/home/ae-lab
Environment=HAPI_HOME=/home/ae-lab/hapi-hub
Environment=HAPI_LISTEN_HOST=127.0.0.1
Environment=HAPI_LISTEN_PORT=39444
Environment=HAPI_PUBLIC_URL=http://127.0.0.1:39444
ExecStart=/opt/ae-small/bin/hapi hub --no-relay
Restart=on-failure
RestartSec=5
MemoryMax=500M
CPUWeight=20
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now ae-small-windmill-server ae-small-windmill-worker ae-small-hub
for attempt in $(seq 1 90); do
  [[ -f /home/ae-lab/hapi-hub/settings.json ]] && \
    curl -fsS http://127.0.0.1:38000/api/health/status >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS http://127.0.0.1:38000/api/health/status >/dev/null
curl -fsS http://127.0.0.1:39444/health >/dev/null
hapi_token="$(jq -r '.cliApiToken' /home/ae-lab/hapi-hub/settings.json)"
if [[ -z "$hapi_token" || "$hapi_token" == null ]]; then echo 'HAPI token unavailable' >&2; exit 1; fi

cat > /etc/ae-small/runner.env <<EOF
HOME=/home/ae-lab
HAPI_HOME=/home/ae-lab/hapi-runner
HAPI_API_URL=http://127.0.0.1:39444
HAPI_RUNNER_SUPERVISED=1
CLI_API_TOKEN=$hapi_token
PATH=/opt/ae-small/bin:/opt/ae-small/runtime/node/bin:/usr/local/bin:/usr/bin:/bin
EOF
chmod 600 /etc/ae-small/runner.env
cat > /etc/systemd/system/ae-small-runner.service <<'EOF'
[Unit]
Description=Agent environment small HAPI runner
After=ae-small-hub.service
Requires=ae-small-hub.service

[Service]
User=ae-lab
Group=ae-lab
EnvironmentFile=/etc/ae-small/runner.env
ExecStart=/opt/ae-small/bin/hapi runner start-sync --workspace-root /home/ae-lab/workspaces
KillMode=process
Restart=always
RestartSec=5
MemoryMax=1200M
CPUWeight=20
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
cat > /etc/ae-small/api.env <<EOF
AE_CONFIG=/etc/ae-small/config.json
AE_DATABASE_URL=$database_url
AE_API_TOKEN=$(cat /etc/ae-small/api-token)
AE_HAPI_TOKEN=$hapi_token
AE_HAPI_URL=http://127.0.0.1:39444
HAPI_HOME=/home/ae-lab/hapi-runner
CLI_API_TOKEN=$hapi_token
HAPI_API_URL=http://127.0.0.1:39444
AE_BIND_HOST=127.0.0.1
AE_PORT=38765
AE_MAX_SLOTS=1
AE_CHECK_CPUS=1
AE_CHECK_MEMORY=1g
AE_PUBLISH_MODE=local
AE_AGENT_USER=ae-lab
AE_REPO_ROOT=/home/ae-lab/repos
AE_WORKSPACE_ROOT=/home/ae-lab/workspaces
GH_CONFIG_DIR=/etc/ae-small/gh
GIT_CONFIG_GLOBAL=/etc/ae-small/gh/gitconfig
PATH=/opt/ae-small/bin:/opt/ae-small/runtime/node/bin:/usr/local/bin:/usr/bin:/bin
EOF
chmod 600 /etc/ae-small/api.env
cat > /etc/systemd/system/ae-small-api.service <<'EOF'
[Unit]
Description=Agent environment small integration API
After=ae-small-hub.service docker.service
Requires=ae-small-hub.service docker.service

[Service]
User=root
EnvironmentFile=/etc/ae-small/api.env
WorkingDirectory=/opt/ae-small
ExecStart=/opt/ae-small/venv/bin/python -m ae_core.server
Restart=on-failure
RestartSec=5
MemoryMax=300M
CPUWeight=20
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now ae-small-runner ae-small-api
for attempt in $(seq 1 30); do
  if curl -fsS -H "Authorization: Bearer $(cat /etc/ae-small/api-token)" \
    http://127.0.0.1:38765/health >/dev/null 2>&1; then break; fi
  sleep 2
done
bash scripts/doctor-small.sh
echo 'Small-server setup is running. Tunnel ports 38000 (Windmill) and 39444 (HAPI) over SSH.'
echo 'Next: sign ae-lab into one Codex account, create a Windmill workspace, then register a repository.'
