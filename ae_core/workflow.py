import json
import os
import re
import shutil
import subprocess
import uuid
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import store
from .config import load_config, repo_config
from .hapi import HapiClient, HapiError


WORKSPACE_ROOT = Path(os.environ.get("AE_WORKSPACE_ROOT", "/home/ae-agent/workspaces"))
AGENT_USER = os.environ.get("AE_AGENT_USER", "ae-agent")
AGENTS = {"codex", "claude", "opencode"}


def command(args, *, cwd=None, agent=False, timeout=120, check=True):
    if agent:
        args = ["/usr/sbin/runuser", "-u", AGENT_USER, "--", *args]
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True,
                            timeout=timeout, check=False)
    if check and result.returncode:
        raise RuntimeError(f"{args[0]} failed ({result.returncode}): {result.stderr[-1000:]}")
    return result


def safe_name(value):
    if (not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_./-]{0,119}", value)
            or ".." in value or "//" in value or value.endswith("/")
            or any(part.startswith(".") or part.endswith(".lock") for part in value.split("/"))):
        raise ValueError("Invalid repository or branch name")
    return value


def authenticated(worker):
    probe = {"codex": ["codex", "login", "status"],
             "claude": ["claude", "auth", "status"],
             "opencode": ["opencode", "auth", "list"]}[worker]
    try:
        result = command(probe, agent=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    if worker == "opencode":
        return "openrouter" in result.stdout.lower()
    return True


def prepare(task, config):
    repo, path = repo_config(config, task["repository"])
    base = safe_name(task["base_branch"])
    branch = f"codex/ae-{task['id'].hex[:12]}"
    worktree = WORKSPACE_ROOT / str(task["id"])
    if worktree.exists():
        head = command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree, agent=True).stdout.strip()
        if head != branch:
            raise RuntimeError("Existing worktree uses a different branch")
    else:
        command(["git", "-C", str(path), "fetch", "origin",
                 f"+refs/heads/{base}:refs/remotes/origin/{base}"], agent=True, timeout=300)
        existing = command(["git", "-C", str(path), "show-ref", "--verify", "--quiet",
                            f"refs/heads/{branch}"], agent=True, check=False)
        if existing.returncode == 0:
            command(["git", "-C", str(path), "worktree", "add", str(worktree), branch], agent=True)
        else:
            command(["git", "-C", str(path), "worktree", "add", "-b", branch,
                     str(worktree), f"origin/{base}"], agent=True)
    return branch, str(worktree)


def prepare_review(task):
    review_path = WORKSPACE_ROOT / (str(task["id"]) + "-review")
    repo_path = str(Path(task["worktree"]).resolve())
    if review_path.exists():
        commit = command(["git", "rev-parse", "HEAD"], cwd=repo_path, agent=True).stdout.strip()
        command(["git", "-C", str(review_path), "reset", "--hard", commit], agent=True)
    else:
        command(["git", "-C", repo_path, "worktree", "add", "--detach", str(review_path), "HEAD"], agent=True)
    return str(review_path)


def parse_review(messages):
    for item in reversed(messages):
        envelope = item.get("content") or {}
        if not isinstance(envelope, dict) or envelope.get("role") != "agent":
            continue
        payload = envelope.get("content") or {}
        data = payload.get("data") if isinstance(payload, dict) else None
        pieces = []
        if isinstance(data, dict):
            if isinstance(data.get("message"), str):
                pieces.append(data["message"])
            message = data.get("message")
            if isinstance(message, dict):
                content = message.get("content", [])
                if isinstance(content, str):
                    pieces.append(content)
                elif isinstance(content, list):
                    pieces += [x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") == "text"]
        for piece in pieces:
            match = re.search(r"\{.*\}", piece, re.DOTALL)
            if not match:
                continue
            try:
                result = json.loads(match.group())
            except ValueError:
                continue
            if isinstance(result, dict) and isinstance(result.get("findings"), list):
                return result
    raise RuntimeError("Reviewer did not return a JSON findings list")


def route(task, config, hapi, machine_id):
    repo, _ = repo_config(config, task["repository"])
    preferred = task["worker"] or repo.get("default_worker") or config["default_worker"]
    if preferred not in AGENTS:
        raise ValueError("Unsupported worker")
    available = hapi.available(machine_id)
    if preferred in available and authenticated(preferred):
        return preferred, "explicit" if task["worker"] else "repository/default"
    fallback = repo.get("fallback_worker", config.get("fallback_worker"))
    if fallback in AGENTS and fallback in available and authenticated(fallback):
        return fallback, f"{preferred} unavailable or signed out; configured fallback"
    raise RuntimeError(f"{preferred} unavailable or signed out and no configured fallback is available")


def openrouter_budget(config, task):
    key_file = config.get("openrouter_key_file")
    if not key_file or not Path(key_file).exists():
        raise RuntimeError("OpenRouter key file is not configured")
    key = Path(key_file).read_text().strip()
    request = urllib.request.Request("https://openrouter.ai/api/v1/key",
                                     headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.load(response)["data"]
    limit = data.get("limit")
    remaining = data.get("limit_remaining")
    if data.get("limit_reset") != "monthly" or limit is None or remaining is None:
        raise RuntimeError("OpenRouter key must have a monthly spending limit")
    if float(limit) > float(config.get("openrouter_monthly_usd", 20)):
        raise RuntimeError("OpenRouter key limit exceeds configured monthly allowance")
    if task["budget_usd"] is None or float(task["budget_usd"]) <= 0:
        raise RuntimeError("Paid task requires an explicit budget")
    if float(remaining) < float(task["budget_usd"]):
        raise RuntimeError("OpenRouter budget exhausted")
    return {"provider": "openrouter", "monthly_usage_usd": data.get("usage_monthly"),
            "remaining_usd": remaining, "recorded_at": datetime.now(timezone.utc).isoformat()}


def requires_review(task, config):
    policy = task["review_policy"]
    if policy == "skip":
        return False
    if policy == "required":
        return True
    changed = command(["git", "diff", "--name-only", f"origin/{task['base_branch']}...HEAD"],
                      cwd=task["worktree"], agent=True).stdout.splitlines()
    sensitive = ("auth", "permission", "migration", "deploy", ".github/workflows")
    if any(any(item in name.lower() for item in sensitive) for name in changed):
        return True
    return False


def run_checks(task, config):
    repo, _ = repo_config(config, task["repository"])
    image = repo.get("check_image", "python:3.12.7-slim")
    checks = repo.get("checks", [])
    if not checks:
        raise RuntimeError("No validation commands configured for repository")
    results = []
    check_cpus = os.environ.get("AE_CHECK_CPUS", "2")
    check_memory = os.environ.get("AE_CHECK_MEMORY", "2g")
    for check in checks:
        if not isinstance(check, str) or not check.strip():
            raise ValueError("Invalid validation command")
        run = command(["docker", "run", "--rm", "--network", "none", "--pids-limit", "256",
                       "--cpus", check_cpus, "--memory", check_memory, "--read-only", "--tmpfs", "/tmp:rw,size=512m",
                       "-v", f"{task['worktree']}:/workspace:ro", "-w", "/workspace",
                       image, "sh", "-lc", check], timeout=1800, check=False)
        results.append({"command": check, "exit_code": run.returncode,
                        "stdout": run.stdout[-8000:], "stderr": run.stderr[-8000:]})
        if run.returncode:
            break
    return results


def commit_work(task):
    path = task["worktree"]
    command(["git", "add", "-A"], cwd=path, agent=True)
    staged = command(["git", "diff", "--cached", "--quiet"], cwd=path,
                     agent=True, check=False)
    if staged.returncode == 1:
        command(["git", "-c", "user.name=Agent Environment", "-c",
                 "user.email=agent-environment@localhost", "commit", "-m",
                 f"Implement task {task['id']}"], cwd=path, agent=True)
    return command(["git", "rev-parse", "HEAD"], cwd=path, agent=True).stdout.strip()


def publish(task, config):
    repo, _ = repo_config(config, task["repository"])
    path = task["worktree"]
    branch = task["branch"]
    remote = command(["git", "remote", "get-url", "origin"], cwd=path, agent=True).stdout.strip()
    if not (remote.startswith("https://github.com/") or remote.startswith("git@github.com:")):
        raise RuntimeError("Draft PR publishing only supports GitHub repositories")
    # The trusted stage owns GH_TOKEN. A preceding read prevents duplicate PRs.
    found = command(["gh", "pr", "list", "--repo", repo["github_repo"],
                     "--head", branch, "--state", "all", "--json", "url"],
                    cwd=path)
    existing = json.loads(found.stdout)
    if existing:
        return existing[0]["url"]
    command(["gh", "auth", "setup-git"], timeout=30)
    command(["git", "-c", f"safe.directory={path}", "-C", path,
             "push", "origin", f"HEAD:refs/heads/{branch}"], timeout=300)
    result = command(["gh", "pr", "create", "--repo", repo["github_repo"],
                      "--draft", "--base", task["base_branch"], "--head", branch,
                      "--title", f"Agent task {task['id']}",
                      "--body", f"Automated draft for task {task['id']}.\n\nChecks and review are recorded in Agent Environment."],
                     cwd=path, timeout=120)
    return result.stdout.strip()


def existing_pr(task, config):
    repo, _ = repo_config(config, task["repository"])
    found = command(["gh", "pr", "list", "--repo", repo["github_repo"],
                     "--head", task["branch"], "--state", "all", "--json", "url"])
    rows = json.loads(found.stdout)
    return rows[0]["url"] if len(rows) == 1 else None


def advance(task_id):
    config = load_config()
    task = store.get(task_id)
    state = task["state"]
    if state in {"done", "cancelled"}:
        return task
    if task["cancel_requested"] and state not in {"spawn_pending", "review_spawn_pending", "publish_pending"}:
        return cancel(task_id)
    if state == "needs_you":
        return task
    if state == "queued":
        free_gb = shutil.disk_usage(WORKSPACE_ROOT).free / 1024**3
        if free_gb < float(config["minimum_free_gb"]):
            return fail(task_id, f"Disk pressure: only {free_gb:.1f} GiB free")
        with store.locked_task(task_id) as (conn, current):
            if current["state"] != "queued":
                return current
            if not store.claim_slot(conn, task_id):
                return current
            store.update(conn, task_id, state="preparing")
            store.event(conn, task_id, "preparing")
        return store.get(task_id)
    if state == "preparing":
        # Worktree creation is idempotent. Persist branch and path before spawn.
        branch, path = prepare(task, config)
        with store.locked_task(task_id) as (conn, current):
            if current["state"] == "preparing":
                store.update(conn, task_id, state="spawning", branch=branch, worktree=path)
        return store.get(task_id)
    if state == "spawning":
        hapi = HapiClient()
        machine = hapi.machine()
        worker, reason = route(task, config, hapi, machine)
        usage = None
        model = None
        if worker == "opencode":
            usage = openrouter_budget(config, task)
            repo, _ = repo_config(config, task["repository"])
            model = repo.get("openrouter_model")
            if not model or model not in config.get("openrouter_models", []):
                raise RuntimeError("Repository OpenRouter model is not in the allowlist")
        # A timeout can mean spawn succeeded. Mark indeterminate instead of retrying.
        with store.locked_task(task_id) as (conn, current):
            if current["state"] != "spawning":
                return current
            store.update(conn, task_id, state="spawn_pending", selected_worker=worker,
                         selected_model=model, selection_reason=reason,
                         usage=json.dumps(usage) if usage else None)
        session_id = hapi.spawn(machine, task["worktree"], worker, model)
        with store.locked_task(task_id) as (conn, current):
            store.update(conn, task_id, state="messaging", session_id=session_id)
            store.event(conn, task_id, "session_started", {"session_id": session_id, "worker": worker})
        return store.get(task_id)
    if state == "messaging":
        message_id = task["message_id"] or uuid.uuid5(uuid.NAMESPACE_URL, f"ae:{task_id}:attempt:{task['attempt']}")
        with store.locked_task(task_id) as (conn, current):
            store.update(conn, task_id, state="message_pending", message_id=message_id)
        context = config.get("personal_context", "")
        repo, _ = repo_config(config, task["repository"])
        handoff = json.dumps(task["handoff"] or {}, default=str)[:12_000]
        prompt = (f"Task: {task['instruction']}\n\nPersonal context: {context}\n"
                  f"Repository checks: {repo.get('checks', [])}\nBase branch: {task['base_branch']}\n"
                  f"Prior attempt handoff: {handoff}\n\nWork in this checkout. Do not merge or deploy.")
        HapiClient().send(task["session_id"], prompt, message_id)
        with store.locked_task(task_id) as (conn, current):
            store.update(conn, task_id, state="implementing")
            store.event(conn, task_id, "message_sent", {"local_id": str(message_id)})
        return store.get(task_id)
    if state == "spawn_pending":
        hapi = HapiClient()
        matches = []
        for summary in hapi.sessions():
            detail = hapi.session(summary["id"])
            if ((detail.get("metadata") or {}).get("path") == task["worktree"] and
                    detail.get("createdAt", 0) / 1000 >= task["updated_at"].timestamp() - 5):
                matches.append(detail["id"])
        if len(matches) == 1:
            with store.locked_task(task_id) as (conn, current):
                store.update(conn, task_id, state="messaging", session_id=matches[0])
                store.event(conn, task_id, "spawn_reconciled", {"session_id": matches[0]})
            return store.get(task_id)
        if len(matches) > 1:
            return fail(task_id, "Multiple HAPI sessions use this task worktree; inspect before continuing")
        return task
    if state == "message_pending":
        result = HapiClient().message_state(task["session_id"], task["message_id"])
        key = str(task["message_id"])
        if key in result.get("queuedLocalIds", []) or key in result.get("indeterminateLocalIds", []) or any(
                m.get("localId") == key for m in result.get("invokedLocalMessages", [])):
            with store.locked_task(task_id) as (conn, current):
                store.update(conn, task_id, state="implementing")
            return store.get(task_id)
        return fail(task_id, "Message delivery unresolved; inspect HAPI before retry")
    if state == "implementing":
        session = HapiClient().session(task["session_id"])
        if not session.get("active"):
            return fail(task_id, "Agent session stopped; inspect worktree and resume explicitly")
        if (session.get("agentState") or {}).get("requests"):
            return task
        if session.get("active") and session.get("thinking"):
            return task
        message = HapiClient().message_state(task["session_id"], task["message_id"])
        if not any(m.get("localId") == str(task["message_id"]) for m in message.get("invokedLocalMessages", [])):
            return task
        commit = commit_work(task)
        with store.locked_task(task_id) as (conn, current):
            store.update(conn, task_id, state="checking", commit_sha=commit,
                         selected_model=session.get("model") or task["selected_model"])
            store.event(conn, task_id, "implementation_completed", {"commit": commit})
        return store.get(task_id)
    if state == "checking":
        checks = run_checks(task, config)
        ok = all(x["exit_code"] == 0 for x in checks)
        with store.locked_task(task_id) as (conn, current):
            store.update(conn, task_id, checks=json.dumps(checks),
                         state="reviewing" if ok and requires_review(task, config) else
                               "publishing" if ok else "repair_messaging" if task["repair_count"] < 2 else "needs_you",
                         error=None if ok else "Validation failed")
            store.event(conn, task_id, "checks_completed", {"passed": ok})
        return store.get(task_id)
    if state == "reviewing":
        hapi = HapiClient()
        if task["review_session_id"]:
            previous = hapi.session(task["review_session_id"])
            if previous.get("active"):
                if previous.get("thinking"):
                    hapi.abort(task["review_session_id"])
                command(["hapi", "runner", "stop-session", task["review_session_id"]], agent=True)
                if hapi.session(task["review_session_id"]).get("active"):
                    raise RuntimeError("Previous reviewer is still active")
        machine = hapi.machine()
        available = {agent for agent in hapi.available(machine) - {task["selected_worker"]}
                     if agent in AGENTS and authenticated(agent)}
        if not available:
            return fail(task_id, "Independent provider unavailable; explicit review waiver required")
        repo, _ = repo_config(config, task["repository"])
        preferred = repo.get("review_worker")
        reviewer = preferred if preferred in available else sorted(available)[0]
        review_model = None
        if reviewer == "opencode":
            openrouter_budget(config, task)
            review_model = repo.get("openrouter_model")
            if not review_model or review_model not in config.get("openrouter_models", []):
                raise RuntimeError("Reviewer OpenRouter model is not in the allowlist")
        review_path = prepare_review(task)
        with store.locked_task(task_id) as (conn, current):
            store.update(conn, task_id, state="review_spawn_pending", review_worktree=review_path)
        session_id = hapi.spawn(machine, review_path, reviewer, review_model)
        with store.locked_task(task_id) as (conn, current):
            store.update(conn, task_id, state="review_messaging", review_session_id=session_id)
            store.event(conn, task_id, "review_started", {"session_id": session_id, "worker": reviewer})
        return store.get(task_id)
    if state == "review_messaging":
        message_id = uuid.uuid5(uuid.NAMESPACE_URL, f"ae:{task_id}:review:{task['repair_count']}")
        diff = command(["git", "diff", f"origin/{task['base_branch']}...HEAD"],
                       cwd=task["review_worktree"], agent=True).stdout
        if len(diff) > 100_000:
            return fail(task_id, "Diff too large for bounded review prompt")
        prompt = (f"Independently review this change for concrete defects. Do not edit files.\n"
                  f"Task: {task['instruction']}\nChecks: {json.dumps(task['checks'])}\n"
                  f"Diff:\n{diff}\nReturn only JSON: {{\"findings\": [{{\"file\": \"...\", "
                  f"\"line\": 1, \"reason\": \"...\"}}]}}. Use an empty list if none.")
        with store.locked_task(task_id) as (conn, current):
            store.update(conn, task_id, state="review_message_pending", review_message_id=message_id)
        HapiClient().send(task["review_session_id"], prompt, message_id)
        with store.locked_task(task_id) as (conn, current):
            store.update(conn, task_id, state="review_waiting")
        return store.get(task_id)
    if state == "review_spawn_pending":
        hapi = HapiClient()
        matches = []
        for summary in hapi.sessions():
            detail = hapi.session(summary["id"])
            if ((detail.get("metadata") or {}).get("path") == task["review_worktree"] and
                    detail.get("createdAt", 0) / 1000 >= task["updated_at"].timestamp() - 5):
                matches.append(detail["id"])
        if len(matches) == 1:
            with store.locked_task(task_id) as (conn, current):
                store.update(conn, task_id, state="review_messaging", review_session_id=matches[0])
            return store.get(task_id)
        if len(matches) > 1:
            return fail(task_id, "Multiple review sessions use this checkout")
        return task
    if state == "review_message_pending":
        result = HapiClient().message_state(task["review_session_id"], task["review_message_id"])
        key = str(task["review_message_id"])
        if key in result.get("queuedLocalIds", []) or any(
                m.get("localId") == key for m in result.get("invokedLocalMessages", [])):
            with store.locked_task(task_id) as (conn, current):
                store.update(conn, task_id, state="review_waiting")
            return store.get(task_id)
        return fail(task_id, "Review message delivery unresolved")
    if state == "review_waiting":
        hapi = HapiClient()
        session = hapi.session(task["review_session_id"])
        if not session.get("active"):
            return fail(task_id, "Review session stopped before a confirmed result")
        if session.get("thinking") or (session.get("agentState") or {}).get("requests"):
            return task
        delivery = hapi.message_state(task["review_session_id"], task["review_message_id"])
        if not any(m.get("localId") == str(task["review_message_id"]) for m in delivery.get("invokedLocalMessages", [])):
            return task
        result = parse_review(hapi.messages(task["review_session_id"]))
        findings = result["findings"]
        with store.locked_task(task_id) as (conn, current):
            store.update(conn, task_id, review=json.dumps(result),
                         state="publishing" if not findings else "repair_messaging" if task["repair_count"] < 2 else "needs_you",
                         error=None if not findings else "Reviewer findings require repair" if task["repair_count"] < 2 else "Review findings remain after two repairs")
            store.event(conn, task_id, "review_completed", {"findings": findings})
        return store.get(task_id)
    if state == "repair_messaging":
        message_id = uuid.uuid5(uuid.NAMESPACE_URL, f"ae:{task_id}:repair:{task['repair_count']+1}")
        hapi = HapiClient()
        session_id = task["session_id"]
        if not hapi.session(session_id).get("active"):
            session_id = hapi.resume(session_id)
            with store.locked_task(task_id) as (conn, current):
                store.update(conn, task_id, session_id=session_id)
        with store.locked_task(task_id) as (conn, current):
            store.update(conn, task_id, state="repair_message_pending", message_id=message_id,
                         repair_count=task["repair_count"] + 1)
        cause = task["review"] if task["error"] != "Validation failed" else task["checks"]
        hapi.send(session_id,
                          f"Address these findings or validation failures, then stop: {json.dumps(cause)}", message_id)
        with store.locked_task(task_id) as (conn, current):
            store.update(conn, task_id, state="implementing")
        return store.get(task_id)
    if state == "repair_message_pending":
        result = HapiClient().message_state(task["session_id"], task["message_id"])
        key = str(task["message_id"])
        if key in result.get("queuedLocalIds", []) or any(
                m.get("localId") == key for m in result.get("invokedLocalMessages", [])):
            with store.locked_task(task_id) as (conn, current):
                store.update(conn, task_id, state="implementing")
            return store.get(task_id)
        return fail(task_id, "Repair message delivery unresolved")
    if state == "publishing":
        if os.environ.get("AE_PUBLISH_MODE") == "local":
            with store.locked_task(task_id) as (conn, current):
                store.update(conn, task_id, state="done", slot=None)
                store.event(conn, task_id, "local_result_ready",
                            {"branch": task["branch"], "commit": task["commit_sha"]})
            return store.get(task_id)
        with store.locked_task(task_id) as (conn, current):
            store.update(conn, task_id, state="publish_pending")
        url = publish(task, config)
        with store.locked_task(task_id) as (conn, current):
            store.update(conn, task_id, state="done", pr_url=url, slot=None)
            store.event(conn, task_id, "draft_pr_created", {"url": url})
        return store.get(task_id)
    if state == "publish_pending":
        url = existing_pr(task, config)
        if url:
            with store.locked_task(task_id) as (conn, current):
                store.update(conn, task_id, state="done", pr_url=url, slot=None)
                store.event(conn, task_id, "draft_pr_reconciled", {"url": url})
            return store.get(task_id)
        return task
    # spawn_pending and publish_pending are intentionally reconciled by an operator.
    return task


def fail(task_id, reason):
    with store.locked_task(task_id) as (conn, task):
        store.update(conn, task_id, state="needs_you", error=reason,
                     failed_stage=task["state"])
        store.event(conn, task_id, "needs_you", {"reason": reason,
                                                 "failed_stage": task["state"]})
    return store.get(task_id)


def retry_publish(task_id):
    task = store.get(task_id)
    if (task["state"] != "needs_you" or task["failed_stage"] not in
            {"publishing", "publish_pending"} or task["cancel_requested"]):
        raise ValueError("Task is not waiting for a publishing retry")
    if not task["commit_sha"] or not task["branch"] or not task["worktree"]:
        raise ValueError("Publishing inputs are incomplete")
    if not task["checks"] or any(item["exit_code"] != 0 for item in task["checks"]):
        raise ValueError("Validation must pass before publishing")
    # First reconcile the external state; PR creation may have succeeded before
    # the previous attempt lost its response.
    url = existing_pr(task, load_config())
    with store.locked_task(task_id) as (conn, current):
        if current["state"] != "needs_you":
            return current
        if url:
            store.update(conn, task_id, state="done", pr_url=url, slot=None,
                         error=None, failed_stage=None)
            store.event(conn, task_id, "draft_pr_reconciled", {"url": url})
        else:
            store.update(conn, task_id, state="publishing", error=None,
                         failed_stage=None)
            store.event(conn, task_id, "publish_retry_requested")
    return store.get(task_id)


def cancel(task_id):
    task = store.get(task_id)
    if task["state"] in {"done", "cancelled"}:
        return task
    if task["state"] in {"spawn_pending", "review_spawn_pending", "publish_pending"}:
        # The external operation may already have succeeded. Reconcile it first.
        return task
    for session_id in (task["session_id"], task["review_session_id"]):
        if not session_id:
            continue
        hapi = HapiClient()
        session = hapi.session(session_id)
        if session.get("active"):
            if session.get("thinking"):
                hapi.abort(session_id)
            command(["hapi", "runner", "stop-session", session_id], agent=True)
            if hapi.session(session_id).get("active"):
                return fail(task_id, f"Cancellation requested; session {session_id} termination not verified")
    with store.locked_task(task_id) as (conn, current):
        store.update(conn, task_id, state="cancelled", slot=None,
                     error=None, failed_stage=None)
        store.event(conn, task_id, "cancelled")
    return store.get(task_id)


def waive_review(task_id):
    with store.locked_task(task_id) as (conn, task):
        if task["state"] != "needs_you" or not task["error"].startswith("Independent provider unavailable"):
            raise ValueError("Task is not waiting for an independent-review waiver")
        store.update(conn, task_id, state="publishing", error=None,
                     review=json.dumps({"waived": True, "reason": "explicit operator action"}))
        store.event(conn, task_id, "review_waived")
    return store.get(task_id)


def fallback_attempt(task_id):
    config = load_config()
    task = store.get(task_id)
    if task["state"] != "needs_you" or not task["selected_worker"]:
        raise ValueError("Only a stopped task with a selected worker can start a fallback attempt")
    repo, _ = repo_config(config, task["repository"])
    fallback = repo.get("fallback_worker", config.get("fallback_worker"))
    if fallback not in AGENTS or fallback == task["selected_worker"]:
        raise ValueError("No different fallback worker is configured")
    if task["session_id"]:
        hapi = HapiClient()
        session = hapi.session(task["session_id"])
        if session.get("active"):
            if session.get("thinking"):
                hapi.abort(task["session_id"])
            command(["hapi", "runner", "stop-session", task["session_id"]], agent=True)
            if hapi.session(task["session_id"]).get("active"):
                raise RuntimeError("Previous agent is still active")
    commit = command(["git", "rev-parse", "HEAD"], cwd=task["worktree"], agent=True).stdout.strip()
    handoff = {"repository": task["repository"], "base_branch": task["base_branch"],
               "working_commit": commit, "instruction": task["instruction"],
               "checks": task["checks"], "review": task["review"],
               "blocker": task["error"], "next_action": "Continue from this commit"}
    with store.locked_task(task_id) as (conn, current):
        if current["state"] != "needs_you":
            raise ValueError("Task state changed while preparing fallback")
        store.update(conn, task_id, state="spawning", worker=fallback,
                     selected_worker=None, selected_model=None, session_id=None,
                     message_id=None, attempt=task["attempt"] + 1,
                     handoff=json.dumps(handoff), error=None)
        store.event(conn, task_id, "fallback_attempt_started",
                    {"worker": fallback, "attempt": task["attempt"] + 1, "commit": commit})
    return store.get(task_id)
