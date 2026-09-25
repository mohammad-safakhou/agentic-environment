# Small-server pilot

The current pilot runs on a shared Ubuntu 26.04 host with 2 CPUs and 4 GB RAM. It is deliberately separate from the full Ubuntu 24.04 distribution in `./ae setup`.

It uses a dedicated `ae-pilot` account, a separate PostgreSQL container and volume, and loopback-only listeners. Existing production containers, ports and networks are not changed. Windmill runs as a native server and one native worker under systemd, HAPI runs as a native hub and runner, and the integration API runs under systemd. The repository `mohammad-safakhou/diffmind` is cloned under the pilot account. One task slot is enabled. Validation containers are limited to one CPU and 1 GB RAM.

On the pilot host, the local endpoints are:

| Service | Address |
|---|---|
| Windmill | `http://127.0.0.1:38000` |
| HAPI | `http://127.0.0.1:39444` |
| Integration API | `http://127.0.0.1:38765` |
| PostgreSQL | `127.0.0.1:55434` |

From your computer, tunnel the two user interfaces with:

```bash
ssh -N -L 38000:127.0.0.1:38000 -L 39444:127.0.0.1:39444 agentic@YOUR_SERVER
```

Then open `http://127.0.0.1:38000` for Windmill and `http://127.0.0.1:39444` for HAPI. The Windmill workspace is `agent-environment-pilot`. Its admin account is `admin@windmill.dev`; read the generated password on the server with `sudo cat /etc/ae-pilot-windmill-admin`. Both Windmill and HAPI credentials remain on the server and must not be committed.

The pilot service units are `ae-pilot-hub`, `ae-pilot-runner`, `ae-pilot-windmill-server`, `ae-pilot-windmill-worker`, and `ae-pilot-api`. PostgreSQL is the container `ae-pilot-postgres`. The service configuration is in root-only `/etc/ae-pilot-*` files. Pilot application files are under `/opt/ae-pilot`; its data and workspaces are under `/var/lib/ae-pilot`.

Windmill has the scripts and schedules in `windmill/f/ae`. The `f/ae/api_token` Windmill variable is secret. A live `health_check`, `weekly_summary`, and empty `tick` succeeded. A `submit` followed by `cancel` succeeded without starting an agent.

After Codex sign-in, task `dab6dd9c-e92a-44d0-9c78-c840788f6ad0` created its own worktree, started a HAPI Codex session, wrote one documentation file, committed `c17b009`, and passed the configured container check. The user chose to skip a draft PR. The task was cancelled, its session was verified stopped, and its slot was released. The commit remains only on the local `codex/ae-dab6dd9ce92a` branch; no branch was pushed. GitHub CLI is installed under `/opt/ae-pilot`, but its isolated publisher account is signed out.

The full stack's Homepage, Beszel, Backrest, Tailscale Serve and second worker were intentionally omitted from this low-resource pilot. The complete distribution and its remaining acceptance checks are described in [README.md](README.md) and [ACCEPTANCE.md](ACCEPTANCE.md).
