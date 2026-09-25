import json
import os
import urllib.error
import urllib.parse
import urllib.request


class HapiError(RuntimeError):
    pass


class HapiClient:
    def __init__(self):
        self.base = os.environ.get("AE_HAPI_URL", "http://127.0.0.1:3006").rstrip("/")
        self.access_token = os.environ["AE_HAPI_TOKEN"]
        self.jwt = None

    def request(self, method, path, body=None, authenticated=True, retried=False):
        if authenticated and not self.jwt:
            self.authenticate()
        data = None if body is None else json.dumps(body).encode()
        headers = {"Content-Type": "application/json", "Accept-Encoding": "identity"}
        if authenticated:
            headers["Authorization"] = f"Bearer {self.jwt}"
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            if authenticated and error.code == 401 and not retried:
                self.authenticate()
                return self.request(method, path, body, retried=True)
            raise HapiError(f"HAPI HTTP {error.code}: {error.read()[:300]!r}") from error
        if isinstance(result, dict) and (result.get("success") is False or result.get("type") == "error"):
            raise HapiError(result.get("error") or result.get("message") or "HAPI wrapped failure")
        return result

    def authenticate(self):
        result = self.request("POST", "/api/auth", {"accessToken": self.access_token}, False)
        self.jwt = result["token"]

    def machine(self):
        machines = self.request("GET", "/api/machines")["machines"]
        online = [m for m in machines if m.get("active", m.get("online", True))]
        if len(online) != 1:
            raise HapiError("Expected exactly one online HAPI runner")
        return online[0]["id"]

    def sessions(self):
        return self.request("GET", "/api/sessions?limit=500&order=updatedAt")["sessions"]

    def available(self, machine_id):
        path = f"/api/machines/{urllib.parse.quote(machine_id, safe='')}/agent-availability"
        agents = self.request("GET", path)["agents"]
        return {a["agent"] for a in agents if a.get("available")}

    def spawn(self, machine_id, directory, agent, model=None):
        path = f"/api/machines/{urllib.parse.quote(machine_id, safe='')}/spawn"
        body = {"directory": directory, "agent": agent, "startingMode": "remote",
                "permissionMode": "default"}
        if model:
            body["model"] = model
        result = self.request("POST", path, body)
        if result.get("type") != "success" or not result.get("sessionId"):
            raise HapiError("HAPI spawn returned no session ID")
        return result["sessionId"]

    def session(self, session_id):
        path = f"/api/sessions/{urllib.parse.quote(session_id, safe='')}"
        return self.request("GET", path)["session"]

    def send(self, session_id, text, local_id):
        path = f"/api/sessions/{urllib.parse.quote(session_id, safe='')}/messages"
        return self.request("POST", path, {"text": text, "localId": str(local_id)})

    def message_state(self, session_id, local_id):
        path = f"/api/sessions/{urllib.parse.quote(session_id, safe='')}/messages/queued-state"
        return self.request("POST", path, {"localIds": [str(local_id)]})

    def abort(self, session_id):
        path = f"/api/sessions/{urllib.parse.quote(session_id, safe='')}/abort"
        return self.request("POST", path, {})

    def resume(self, session_id):
        path = f"/api/sessions/{urllib.parse.quote(session_id, safe='')}/resume"
        result = self.request("POST", path, {})
        if result.get("type") != "success" or not result.get("sessionId"):
            raise HapiError("HAPI resume returned no session ID")
        return result["sessionId"]

    def messages(self, session_id):
        path = f"/api/sessions/{urllib.parse.quote(session_id, safe='')}/messages?limit=200"
        return self.request("GET", path)["messages"]
