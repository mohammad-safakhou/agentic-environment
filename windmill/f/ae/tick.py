"""Schedule every minute in Windmill to advance durable task stages."""
import json
import urllib.request
import wmill


def request(method, path):
    item = urllib.request.Request(
        wmill.get_variable("f/ae/api_url") + path, data=b"{}" if method == "POST" else None,
        method=method, headers={"Content-Type": "application/json",
                                 "Authorization": "Bearer " + wmill.get_variable("f/ae/api_token")})
    with urllib.request.urlopen(item, timeout=1800) as response:
        return json.load(response)


def main():
    tasks = request("GET", "/tasks")["tasks"]
    results = []
    for task in reversed(tasks):
        if task["state"] in ("done", "cancelled", "needs_you"):
            continue
        try:
            results.append(request("POST", "/tasks/" + task["id"] + "/step")["task"])
        except Exception as error:
            results.append({"id": task["id"], "error": str(error)})
    return results
