# Agent Environment

A private, single-server environment for two concurrent coding tasks. Homepage links to Windmill, HAPI, Beszel and Backrest. Windmill submits and schedules tasks; HAPI hosts coding sessions and approvals. The integration API currently advances task stages and keeps task-to-session mappings in its own PostgreSQL schema. Git worktrees hold code changes.

An isolated one-slot pilot on a smaller shared server is documented in [PILOT.md](PILOT.md).

## Requirements

- Ubuntu 24.04 LTS, x86-64, at least 16 GB RAM, 4 CPUs and 80 GB free disk (160 GB SSD recommended).
- A Tailscale account and a GitHub account.
- At least one provider sign-in for Codex, Claude Code or OpenCode.
- A remote restic repository and password for backups before production use.

## Install

```bash
git clone https://github.com/mohammad-safakhou/agentic-environment
cd agentic-environment
./ae setup
```

Setup copies the distribution to `/opt/agentic-environment`, installs the pinned runtime and services, connects Tailscale, starts local-only listeners, and configures private HTTPS on ports 8443–8447 of the tailnet hostname. Run it again after an interruption; it preserves configuration, service data and worktrees. The source checkout can be anywhere readable by root.

Setup requires interactive Tailscale authentication. It prints the Homepage URL when ready. Sign in to Windmill and create its first workspace, then complete HAPI and provider sign-ins:

```bash
sudo -u ae-agent /opt/agentic-environment/bin/codex login --device-auth
sudo -u ae-agent /opt/agentic-environment/bin/claude
sudo -u ae-agent /opt/agentic-environment/bin/opencode
```

The exact provider prompts are owned by those official CLIs. HAPI uses a separate runner account and limits its directories to `/home/ae-agent/workspaces`. The HAPI hub access token is generated locally at first start; keep `/etc/agentic-environment` private.

The trusted draft-PR stage needs a GitHub sign-in under its own config directory:

```bash
sudo GH_CONFIG_DIR=/var/lib/agentic-environment/gh /opt/agentic-environment/bin/gh auth login
```

Register a GitHub repository and at least one validation command. The check image is initially `python:3.12.7-slim`; change it in `/etc/agentic-environment/config.json` for other languages. Commands run without network access in a resource-limited container with the worktree mounted read-only.

```bash
./ae register my-repo https://github.com/OWNER/REPO.git 'python -m unittest discover'
./ae connect-windmill WORKSPACE_ID
```

`./ae connect-windmill` installs the one-minute task tick, a five-minute health check and the weekly summary schedule. `f/ae/submit` is the generated task form; `f/ae/cancel` stops a task. HAPI remains the place to steer sessions and answer approvals. A task requiring independent review waits when no second provider is available; `f/ae/waive_review` records an explicit waiver. After accepting a draft PR, use `f/ae/accept` to record whether you corrected its code; unmeasured costs are reported as unknown.
If a task stage is running when cancellation is requested, the request is recorded and applied at the next safe boundary. An ambiguous spawn or PR publication must be reconciled before the task can release its execution slot.
`f/ae/fallback` starts a new attempt with the configured fallback worker after a task has stopped. It includes the exact commit, checks, review and blocker in a bounded handoff.
`f/ae/retry_publish` reconciles a failed or interrupted draft PR stage against GitHub before scheduling a retry; it requires a successful validation record and a prior publishing failure.
Windmill also includes `f/ae/health_check` and a harmless `f/ae/example` script. Recurring coding tasks can be scheduled from `f/ae/submit` with fixed parameters; no external app connector is enabled by default.

After the first Beszel login, add a system and run `./ae enable-monitoring`. Paste the displayed public key and agent token, then use `/beszel_socket/beszel.sock` as its Host / IP in Beszel.

## Configuration

`/etc/agentic-environment/config.json` stores repository settings, worker preferences, personal context, budget and backup destination. It is root-readable only. For an OpenRouter worker, add an `openrouter_model` to the repository, list that model in `openrouter_models`, set `openrouter_key_file`, and configure the same restricted key in OpenCode. The key must have a provider-side monthly limit at or below `openrouter_monthly_usd`; paid tasks require a task budget. The integration API checks `/api/v1/key` before spawning OpenCode. Unknown usage is treated as unknown, never zero.

Backups use `backup_repository` and `backup_password_file`. Once configured, run `./ae backup` to create the first encrypted snapshot and enable the daily timer. The retention policy is seven daily and four weekly snapshots. `./ae restore SNAPSHOT_ID` restores the matching database export, HAPI data, configuration, Git repositories and unfinished worktrees. On a fresh Ubuntu host, clone this repository, provide a restic password file, and run `./ae restore SNAPSHOT_ID RESTIC_REPOSITORY PASSWORD_FILE`; restore runs setup first when needed. Provider sign-ins may require renewal after a restore.

## Operations

```text
./ae doctor       Check installed commands and service health
./ae status       Show services
./ae start        Start services
./ae stop         Stop services
./ae logs         Follow container logs
./ae update       Drain tasks, back up, apply pinned versions, check health
./ae backup       Create an encrypted off-server snapshot
./ae restore ID   Restore a snapshot
```

An update refuses to run while either slot is held. It applies only the versions in `versions.env`; change that file in the source checkout and rerun setup to choose a different version set. The `ae` schema is separate from Windmill's own tables. HAPI keeps its own SQLite database.

## Safety and limits

Only repositories registered under `/home/ae-agent/repos` can be submitted. The agent account has no sudo access, Docker socket, GitHub publisher credential or platform configuration. Worktrees separate concurrent tasks, but they are not a security sandbox for hostile repositories. Validation jobs have CPU, memory, process and network limits. No automatic merge or deployment command exists.

This repository includes contract and database tests, but a clean Ubuntu VM, real provider accounts, Tailscale HTTPS, Windmill script import, mobile approval flow and off-server restore still require deployment testing. The stage transitions currently live in the integration API; moving them into a native Windmill flow remains a release architecture requirement from the plan. The release acceptance list is in [ACCEPTANCE.md](ACCEPTANCE.md).
