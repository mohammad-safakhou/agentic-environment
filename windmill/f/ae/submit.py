"""Windmill generated form for a coding task."""
import json
import urllib.request
import uuid
import wmill


def main(repository: str, instruction: str, base_branch: str = "", category: str = "general",
         worker: str = "", review_policy: str = "auto", budget_usd: float = 0):
    payload = {
        "id": str(uuid.uuid4()), "repository": repository,
        "instruction": instruction, "base_branch": base_branch, "category": category,
        "worker": worker or None, "review_policy": review_policy,
        "budget_usd": budget_usd or None,
    }
    request = urllib.request.Request(
        wmill.get_variable("f/ae/api_url") + "/tasks", data=json.dumps(payload).encode(),
        method="POST", headers={"Content-Type": "application/json",
                                 "Authorization": "Bearer " + wmill.get_variable("f/ae/api_token")})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)
