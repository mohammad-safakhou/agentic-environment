import json
import urllib.request
import wmill


def main(task_id: str):
    request = urllib.request.Request(
        wmill.get_variable("f/ae/api_url") + "/tasks/" + task_id + "/cancel",
        data=b"{}", method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + wmill.get_variable("f/ae/api_token")})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)
