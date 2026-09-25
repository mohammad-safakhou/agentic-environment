import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(os.environ.get("AE_REPO_ROOT", "/home/ae-agent/repos"))
CONFIG = Path(os.environ.get("AE_CONFIG", "/etc/agentic-environment/config.json"))
AGENT_USER = os.environ.get("AE_AGENT_USER", "ae-agent")


def run(args):
    return subprocess.run(["/usr/sbin/runuser", "-u", AGENT_USER, "--", *args],
                          check=True, text=True, capture_output=True).stdout.strip()


def main():
    if len(sys.argv) < 4:
        raise SystemExit("Usage: ./ae register NAME GITHUB_URL CHECK_COMMAND [CHECK_COMMAND ...]")
    name, url, *checks = sys.argv[1:]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", name):
        raise SystemExit("Invalid repository name")
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:\.git)?", url)
    if not match:
        raise SystemExit("Only GitHub repositories are currently supported")
    path = ROOT / name
    config = json.loads(CONFIG.read_text())
    if name in config["repositories"]:
        raise SystemExit("Repository already registered; edit the configuration to change it")
    if path.exists():
        raise SystemExit("Target path already exists")
    run(["git", "clone", "--", url, str(path)])
    branch = run(["git", "-C", str(path), "symbolic-ref", "--short", "HEAD"])
    config["repositories"][name] = {
        "path": str(path), "github_repo": match.group(1).removesuffix(".git"),
        "default_branch": branch, "checks": checks,
        "check_image": "python:3.12.7-slim"
    }
    temp = CONFIG.with_suffix(".tmp")
    temp.write_text(json.dumps(config, indent=2) + "\n")
    temp.chmod(0o600)
    temp.replace(CONFIG)
    print(f"Registered {name} at {path}")


if __name__ == "__main__":
    main()
