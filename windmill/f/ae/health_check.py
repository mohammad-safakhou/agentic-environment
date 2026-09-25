"""Schedule to check core service health without changing state."""
import urllib.request
import wmill


def main():
    base = wmill.get_variable("f/ae/api_url")
    targets = {
        "integration_api": base + "/health",
        "windmill": wmill.get_variable("f/ae/windmill_url") + "/api/health/status",
    }
    result = {}
    for name, url in targets.items():
        headers = {"Authorization": "Bearer " + wmill.get_variable("f/ae/api_token")} if name == "integration_api" else {}
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=10) as response:
                result[name] = response.status == 200
        except Exception:
            result[name] = False
    if not all(result.values()):
        raise RuntimeError(f"Service health check failed: {result}")
    return result
