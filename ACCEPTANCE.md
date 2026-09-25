# Release acceptance

The distribution is ready for a production label only after these checks pass on a clean Ubuntu 24.04 VM with the pinned versions in `versions.env`.

- [ ] Run `./ae setup` on a clean VM and open all five private HTTPS endpoints from a phone.
- [ ] Interrupt setup, rerun it and confirm configuration and data are retained.
- [ ] Sign in to Codex, Claude Code and OpenCode; verify each in HAPI.
- [ ] Import Windmill scripts, schedule `f/ae/tick` and submit a small task from the phone.
- [ ] Steer, answer a question, cancel and inspect a task from the phone.
- [ ] Run two tasks in one repository and confirm distinct worktree paths; verify a third waits while one task needs attention.
- [ ] Pass checks, collect independent review, perform a bounded repair and create one draft PR.
- [ ] Restart services during an in-flight turn; inspect pending operations and confirm no duplicate prompt, worktree or PR.
- [ ] Exercise expired authentication, rate limits, check failure, cancellation, disk pressure and OpenRouter budget exhaustion.
- [ ] Confirm the agent account cannot read `/etc/agentic-environment` or access Docker.
- [ ] Restore an encrypted snapshot on a fresh VM and recover records and unfinished worktrees.
- [ ] Confirm the complete path works using Community Edition features only.
- [ ] Move stage orchestration into a native Windmill flow; leave only integration metadata and external adapters in the API.

On ambiguous HAPI spawn, message or PR publication, the task remains in an operator-attention state. Inspect the upstream state before any manual retry. Operation identifiers and worktree names are stable; the pipeline does not automatically replay ambiguous external calls.
