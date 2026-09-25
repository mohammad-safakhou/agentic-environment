"""Record human acceptance and whether code correction was needed."""
import json
import urllib.request
import wmill


def main(task_id: str, human_code_correction: bool):
    request = urllib.request.Request(
        wmill.get_variable("f/ae/api_url") + "/tasks/" + task_id + "/accept",
        data=json.dumps({"human_code_correction": human_code_correction}).encode(),
        method="POST", headers={"Content-Type": "application/json",
                                 "Authorization": "Bearer " + wmill.get_variable("f/ae/api_token")})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)
