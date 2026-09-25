import datetime
import hmac
import json
import os
import uuid
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import store, workflow


def encode(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Cannot encode {type(value).__name__}")


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, result):
        data = json.dumps(result, default=encode).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        expected = os.environ["AE_API_TOKEN"]
        supplied = self.headers.get("Authorization", "")
        return hmac.compare_digest(supplied, f"Bearer {expected}")

    def body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 100_000:
            raise ValueError("Request body too large")
        body = json.loads(self.rfile.read(length))
        if not isinstance(body, dict):
            raise ValueError("Request body must be an object")
        return body

    def dispatch(self, method):
        if not self.authorized():
            return self.respond(401, {"error": "Unauthorized"})
        parts = [part for part in urlparse(self.path).path.split("/") if part]
        try:
            if parts == ["health"] and method == "GET":
                from .hapi import HapiClient
                hapi = HapiClient().request("GET", "/health", authenticated=False)
                with store.connect() as conn:
                    conn.execute("SELECT 1")
                healthy = hapi.get("status") == "ok"
                return self.respond(200 if healthy else 503,
                                    {"status": "ok" if healthy else "unhealthy",
                                     "hapi": hapi.get("status"), "postgres": "ok"})
            if parts == ["tasks"] and method == "GET":
                return self.respond(200, {"tasks": store.list_tasks()})
            if parts == ["summary"] and method == "GET":
                return self.respond(200, store.summary())
            if parts == ["status"] and method == "GET":
                return self.respond(200, store.status_counts())
            if parts == ["tasks"] and method == "POST":
                payload = self.body()
                if not isinstance(payload.get("instruction"), str) or not payload["instruction"].strip():
                    raise ValueError("Instruction is required")
                if payload.get("review_policy", "auto") not in {"auto", "required", "skip"}:
                    raise ValueError("Invalid review policy")
                from .config import load_config, repo_config
                repo, _ = repo_config(load_config(), payload["repository"])
                payload["base_branch"] = payload.get("base_branch") or repo.get("default_branch", "main")
                return self.respond(201, {"task": store.create(payload)})
            if len(parts) == 2 and parts[0] == "tasks" and method == "GET":
                return self.respond(200, {"task": store.get(parts[1])})
            if len(parts) == 3 and parts[0] == "tasks" and method == "POST":
                if parts[2] == "step":
                    with store.operation_lock(parts[1]) as acquired:
                        if not acquired:
                            return self.respond(409, {"error": "Task operation already running"})
                        return self.respond(200, {"task": workflow.advance(parts[1])})
                if parts[2] == "cancel":
                    store.request_cancel(parts[1])
                    with store.operation_lock(parts[1]) as acquired:
                        task = workflow.cancel(parts[1]) if acquired else store.get(parts[1])
                    return self.respond(200, {"task": task})
                if parts[2] == "waive-review":
                    return self.respond(200, {"task": workflow.waive_review(parts[1])})
                if parts[2] == "fallback":
                    return self.respond(200, {"task": workflow.fallback_attempt(parts[1])})
                if parts[2] == "retry-publish":
                    with store.operation_lock(parts[1]) as acquired:
                        if not acquired:
                            return self.respond(409, {"error": "Task operation already running"})
                        return self.respond(200, {"task": workflow.retry_publish(parts[1])})
                if parts[2] == "accept":
                    payload = self.body()
                    if not isinstance(payload.get("human_code_correction"), bool):
                        raise ValueError("human_code_correction must be boolean")
                    with store.locked_task(parts[1]) as (conn, task):
                        if task["state"] != "done":
                            raise ValueError("Only completed tasks can be accepted")
                        conn.execute("UPDATE ae.tasks SET accepted_at=now(), human_code_correction=%s WHERE id=%s",
                                     (payload["human_code_correction"], parts[1]))
                        store.event(conn, parts[1], "human_accepted", payload)
                    return self.respond(200, {"task": store.get(parts[1])})
            return self.respond(404, {"error": "Not found"})
        except (KeyError, ValueError) as error:
            return self.respond(400, {"error": str(error)})
        except Exception as error:
            self.log_error("Request failed: %s", error)
            if len(parts) == 3 and parts[0] == "tasks" and parts[2] == "step":
                try:
                    workflow.fail(parts[1], str(error))
                except Exception:
                    pass
            return self.respond(500, {"error": str(error)})

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")


def main():
    store.initialize()
    address = os.environ.get("AE_BIND_HOST", "172.17.0.1")
    server = ThreadingHTTPServer((address, int(os.environ.get("AE_PORT", "8765"))), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
