import json
import os
import uuid
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


SCHEMA = """
CREATE SCHEMA IF NOT EXISTS ae;
CREATE TABLE IF NOT EXISTS ae.tasks (
  id uuid PRIMARY KEY,
  repository text NOT NULL,
  instruction text NOT NULL,
  category text,
  base_branch text NOT NULL,
  worker text,
  review_policy text NOT NULL,
  budget_usd numeric,
  state text NOT NULL DEFAULT 'queued',
  slot integer,
  branch text,
  worktree text,
  session_id text,
  message_id uuid,
  selected_worker text,
  selected_model text,
  selection_reason text,
  attempt integer NOT NULL DEFAULT 1,
  checks jsonb,
  review jsonb,
  handoff jsonb,
  usage jsonb,
  review_session_id text,
  review_worktree text,
  review_message_id uuid,
  repair_count integer NOT NULL DEFAULT 0,
  pr_url text,
  commit_sha text,
  accepted_at timestamptz,
  human_code_correction boolean,
  error text,
  cancel_requested boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ae_slot_range CHECK (slot IS NULL OR slot IN (1,2))
);
ALTER TABLE ae.tasks ADD COLUMN IF NOT EXISTS review_session_id text;
ALTER TABLE ae.tasks ADD COLUMN IF NOT EXISTS handoff jsonb;
ALTER TABLE ae.tasks ADD COLUMN IF NOT EXISTS usage jsonb;
ALTER TABLE ae.tasks ADD COLUMN IF NOT EXISTS review_worktree text;
ALTER TABLE ae.tasks ADD COLUMN IF NOT EXISTS review_message_id uuid;
ALTER TABLE ae.tasks ADD COLUMN IF NOT EXISTS repair_count integer NOT NULL DEFAULT 0;
ALTER TABLE ae.tasks ADD COLUMN IF NOT EXISTS accepted_at timestamptz;
ALTER TABLE ae.tasks ADD COLUMN IF NOT EXISTS category text;
ALTER TABLE ae.tasks ADD COLUMN IF NOT EXISTS selected_model text;
ALTER TABLE ae.tasks ADD COLUMN IF NOT EXISTS commit_sha text;
ALTER TABLE ae.tasks ADD COLUMN IF NOT EXISTS human_code_correction boolean;
ALTER TABLE ae.tasks ADD COLUMN IF NOT EXISTS cancel_requested boolean NOT NULL DEFAULT false;
CREATE UNIQUE INDEX IF NOT EXISTS ae_slot_unique ON ae.tasks(slot) WHERE slot IS NOT NULL;
CREATE TABLE IF NOT EXISTS ae.events (
  id bigserial PRIMARY KEY,
  task_id uuid NOT NULL REFERENCES ae.tasks(id),
  kind text NOT NULL,
  data jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
"""


def connect():
    return psycopg.connect(os.environ["AE_DATABASE_URL"], row_factory=dict_row)


def initialize():
    with connect() as conn:
        conn.execute(SCHEMA)


@contextmanager
def locked_task(task_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM ae.tasks WHERE id=%s FOR UPDATE", (task_id,)).fetchone()
        if row is None:
            raise KeyError("Task not found")
        yield conn, row


@contextmanager
def operation_lock(task_id):
    """Serialize a task's external effects across overlapping Windmill runs."""
    value = uuid.UUID(str(task_id)).int
    keys = ((value >> 96) & 0x7fffffff, value & 0x7fffffff)
    with connect() as conn:
        acquired = conn.execute("SELECT pg_try_advisory_lock(%s,%s) AS locked", keys).fetchone()["locked"]
        try:
            yield acquired
        finally:
            if acquired:
                conn.execute("SELECT pg_advisory_unlock(%s,%s)", keys)


def request_cancel(task_id):
    with locked_task(task_id) as (conn, task):
        if task["state"] not in {"done", "cancelled"} and not task["cancel_requested"]:
            update(conn, task_id, cancel_requested=True)
            event(conn, task_id, "cancel_requested")
    return get(task_id)


def event(conn, task_id, kind, data=None):
    conn.execute("INSERT INTO ae.events(task_id,kind,data) VALUES (%s,%s,%s)",
                 (task_id, kind, json.dumps(data or {})))


def update(conn, task_id, **fields):
    if not fields:
        return
    columns = ", ".join(f"{key}=%s" for key in fields)
    conn.execute(f"UPDATE ae.tasks SET {columns}, updated_at=now() WHERE id=%s",
                 (*fields.values(), task_id))


def create(payload):
    task_id = uuid.UUID(payload.get("id") or str(uuid.uuid4()))
    with connect() as conn:
        row = conn.execute("SELECT * FROM ae.tasks WHERE id=%s", (task_id,)).fetchone()
        if row:
            return row
        conn.execute("""INSERT INTO ae.tasks
          (id,repository,instruction,category,base_branch,worker,review_policy,budget_usd)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
          (task_id, payload["repository"], payload["instruction"], payload.get("category"),
           payload.get("base_branch", "main"), payload.get("worker"),
           payload.get("review_policy", "auto"), payload.get("budget_usd")))
        event(conn, task_id, "submitted")
        return conn.execute("SELECT * FROM ae.tasks WHERE id=%s", (task_id,)).fetchone()


def get(task_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM ae.tasks WHERE id=%s", (task_id,)).fetchone()
        if row is None:
            raise KeyError("Task not found")
        return row


def list_tasks():
    with connect() as conn:
        return conn.execute("SELECT * FROM ae.tasks ORDER BY created_at DESC LIMIT 1000").fetchall()


def summary():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM ae.tasks WHERE created_at >= now() - interval '7 days'").fetchall()
    completed = [r for r in rows if r["state"] == "done"]
    first_pass = [r for r in completed if r["repair_count"] == 0 and
                  r["checks"] and all(x.get("exit_code") == 0 for x in r["checks"])]
    accepted = [r for r in rows if r["accepted_at"] is not None]
    reasons = {}
    for row in rows:
        if row["error"]:
            reasons[row["error"]] = reasons.get(row["error"], 0) + 1
    return {
        "period": "last_7_days", "submitted": len(rows), "completed": len(completed),
        "first_pass_check_success": len(first_pass),
        "accepted": len(accepted),
        "accepted_without_human_code_correction": sum(r["human_code_correction"] is False for r in accepted),
        "mean_minutes_per_accepted": (sum((r["accepted_at"] - r["created_at"]).total_seconds() / 60
                                           for r in accepted) / len(accepted)) if accepted else None,
        "api_cost_usd": None, "subscription_cost_usd": None,
        "common_failure_reasons": reasons,
    }


def status_counts():
    with connect() as conn:
        rows = conn.execute("SELECT state, count(*) AS count FROM ae.tasks GROUP BY state").fetchall()
    counts = {row["state"]: row["count"] for row in rows}
    return {"active": sum(value for state, value in counts.items() if state not in {"queued", "done", "cancelled"}),
            "queued": counts.get("queued", 0), "needs_you": counts.get("needs_you", 0)}


def claim_slot(conn, task_id):
    # Serializes concurrent claims across Windmill workers.
    conn.execute("SELECT pg_advisory_xact_lock(823711)")
    used = {r["slot"] for r in conn.execute("SELECT slot FROM ae.tasks WHERE slot IS NOT NULL")}
    max_slots = int(os.environ.get("AE_MAX_SLOTS", "2"))
    if max_slots not in (1, 2):
        raise ValueError("AE_MAX_SLOTS must be 1 or 2")
    for slot in range(1, max_slots + 1):
        if slot not in used:
            update(conn, task_id, slot=slot)
            event(conn, task_id, "slot_claimed", {"slot": slot})
            return slot
    return None
