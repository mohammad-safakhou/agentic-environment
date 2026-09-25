"""Schedule weekly in Windmill; costs remain unknown until measured per task."""
import json
import urllib.request
import wmill


def main():
    request = urllib.request.Request(
        wmill.get_variable("f/ae/api_url") + "/summary", method="GET",
        headers={"Authorization": "Bearer " + wmill.get_variable("f/ae/api_token")})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)
