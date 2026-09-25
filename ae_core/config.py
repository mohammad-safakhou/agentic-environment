import json
import os
from pathlib import Path


def load_config():
    path = Path(os.environ.get("AE_CONFIG", "/etc/agentic-environment/config.json"))
    with path.open() as handle:
        config = json.load(handle)
    config.setdefault("repositories", {})
    config.setdefault("default_worker", "codex")
    config.setdefault("fallback_worker", None)
    config.setdefault("minimum_free_gb", 10)
    return config


def repo_config(config, name):
    if not isinstance(name, str) or name not in config["repositories"]:
        raise ValueError("Repository is not registered")
    repo = config["repositories"][name]
    path = Path(repo["path"]).resolve(strict=True)
    root = Path(os.environ.get("AE_REPO_ROOT", "/home/ae-agent/repos")).resolve()
    if not path.is_relative_to(root) or not (path / ".git").exists():
        raise ValueError("Repository must be a checkout under /home/ae-agent/repos")
    return repo, path
