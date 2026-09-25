# One-task setup for the shared 4 GB server

This profile runs the core task path on Ubuntu x86-64 with 2 CPUs and 4 GB RAM. It is for the existing shared server, where the machine owner has already created the `ae-lab` account with `/home/ae-lab` as its home and no `sudo` or `docker` group membership. Account creation remains a manual owner step. The full `./ae setup` profile is separate.

The small profile installs a project PostgreSQL container, one native Windmill server and worker, HAPI hub and runner, the integration API, and the Codex and Claude Code CLIs. All web and API ports bind to `127.0.0.1`. It uses one task slot, a one-CPU/one-GB validation limit, and a local result mode: successful work stays in its server worktree and is never pushed to GitHub. It does not install Tailscale, Homepage, Beszel, Backrest, or automatic off-server backup.

## Install

SSH in as the admin account. A clean checkout already exists at `/home/ae-lab/agentic-environment` on the pilot server. For another machine, manually create `ae-lab`, clone this repository as that user, and install only if the preflight passes.

```bash
ssh agentic@2.28.57.254
sudo /home/ae-lab/agentic-environment/ae-small preflight
sudo /home/ae-lab/agentic-environment/ae-small setup
sudo /home/ae-lab/agentic-environment/ae-small doctor
```

Setup downloads pinned Windmill, Codex, HAPI, Windmill CLI, Python and `uv` versions into `/opt/ae-small`; it does not upgrade system packages or modify existing production containers. It creates only `ae-small-*` services, the `ae-small-postgres` container, `ae_small_pgdata` volume, and files under `/etc/ae-small`, `/opt/ae-small`, and `/home/ae-lab`. The first run needs several GB of free disk; the preflight requires 5 GB. Re-running setup preserves the database, configuration and workspaces.

## Open the interfaces

Run this on your own computer and leave the tunnel open:

```bash
ssh -N -L 38000:127.0.0.1:38000 -L 39444:127.0.0.1:39444 agentic@2.28.57.254
```

Open `http://127.0.0.1:38000` for Windmill and `http://127.0.0.1:39444` for HAPI. Windmill's initial login is `admin@windmill.dev` / `changeme`; complete its first-run setup, replace that administrator credential immediately, and create a workspace. The interfaces are not exposed on the server's public IP.

In Windmill, note the workspace ID from the workspace switcher. Create a user token from your username menu → **Account settings** → **Tokens**; the CLI connection below asks for it. Windmill shows the token only once, so keep it in your own terminal/password manager and do not put it in the repository.

HAPI needs no separate account or manual runner setup. The installer creates its hub and runner and generates an access token. To see the token for your browser login, run this on the server:

```bash
sudo jq -r '.cliApiToken' /home/ae-lab/hapi-hub/settings.json
```

Enter that token in the HAPI page opened through the SSH tunnel. Keep it private: it also authenticates the runner. HAPI will show sessions after a task starts.

### Phone and other devices on the pilot server

The pilot server also has Tailscale Serve configured. Join your phone and other computers to the same tailnet, then open:

- HAPI: `https://ae-small-hapi.taile1040e.ts.net:10000/`
- Windmill: `https://ae-small-hapi.taile1040e.ts.net:8443/`

Use the HAPI URL and the hub token above to pair the HAPI mobile app. Windmill is where you submit and inspect the durable task queue. These addresses are private to devices allowed into your tailnet. The HAPI URL uses port 10000 because a production container already occupies port 443 on this host. Tailscale is an optional, separate installation on other servers; the small setup script does not alter network configuration.

## Sign in and try one task

Sign the `ae-lab` runner into **one** Codex account. The CLI prints a URL and device code; complete the sign-in in a browser on your computer. Sign in to Claude Code separately if you want to run Claude tasks. Do not paste credentials into the server chat or repository.

```bash
sudo -u ae-lab -H /opt/ae-small/bin/codex login --device-auth
sudo -iu ae-lab /home/ae-lab/.local/bin/claude
sudo /home/ae-lab/agentic-environment/ae-small register diffmind https://github.com/mohammad-safakhou/diffmind.git 'test -f go.mod'
sudo /home/ae-lab/agentic-environment/ae-small connect-windmill YOUR_WORKSPACE_ID
```

The Windmill connection command asks for a workspace token from the Windmill UI and imports the task scripts and schedules. Run `f/ae/submit` with repository `diffmind`, a small documentation-only instruction, and `review_policy=skip`. Windmill advances the task; HAPI shows the Codex session; the integration API creates a worktree and runs the validation check. A successful small-profile task ends with a local branch and commit in `/home/ae-lab/workspaces`, with no draft PR.

One Codex login is enough for the first test. The second Codex account is not used automatically. This profile has one concurrent task slot, and the current independent-review rule treats Codex as one provider even if two accounts exist. New tasks are started automatically from the Windmill queue. The small profile configures Claude as fallback: it can take a task at spawn time if Codex is unavailable, or continue an existing Codex task when HAPI reports a structured provider-limit event. Claude must be signed in for that handoff. Other agent errors stop for inspection.

## Operate

```bash
sudo /home/ae-lab/agentic-environment/ae-small status
sudo /home/ae-lab/agentic-environment/ae-small doctor
sudo /home/ae-lab/agentic-environment/ae-small stop
sudo /home/ae-lab/agentic-environment/ae-small start
```

The runtime configuration is `/etc/ae-small/config.json`, with a 2 GB free-disk floor for new tasks. Inspect free space before submitting large repositories or checks. A stopped service's logs are available with `sudo journalctl -u ae-small-api -n 100 --no-pager` (replace the unit name as needed). The setup preflight and doctor do not change production services.
