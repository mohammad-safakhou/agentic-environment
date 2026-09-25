import os
import unittest
import uuid

from ae_core import store


@unittest.skipUnless(os.environ.get("AE_DATABASE_URL"), "requires disposable PostgreSQL")
class StoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.initialize()

    def test_two_slots_and_idempotent_submission(self):
        ids = [str(uuid.uuid4()) for _ in range(3)]
        payloads = [{"id": item, "repository": "sample", "instruction": "test",
                     "base_branch": "main", "review_policy": "auto"} for item in ids]
        rows = [store.create(item) for item in payloads]
        self.assertEqual(store.create(payloads[0])["id"], rows[0]["id"])
        with store.locked_task(ids[0]) as (conn, _):
            self.assertEqual(store.claim_slot(conn, ids[0]), 1)
            store.update(conn, ids[0], state="needs_you")
        with store.locked_task(ids[1]) as (conn, _):
            self.assertEqual(store.claim_slot(conn, ids[1]), 2)
        with store.locked_task(ids[2]) as (conn, _):
            self.assertIsNone(store.claim_slot(conn, ids[2]))
        with store.locked_task(ids[0]) as (conn, _):
            store.update(conn, ids[0], state="cancelled", slot=None)
        with store.locked_task(ids[2]) as (conn, _):
            self.assertEqual(store.claim_slot(conn, ids[2]), 1)
        with store.connect() as conn:
            conn.execute("DELETE FROM ae.events WHERE task_id = ANY(%s::uuid[])", (ids,))
            conn.execute("DELETE FROM ae.tasks WHERE id = ANY(%s::uuid[])", (ids,))

    def test_operation_lock_and_deferred_cancellation(self):
        task_id = str(uuid.uuid4())
        store.create({"id": task_id, "repository": "sample", "instruction": "test",
                      "base_branch": "main", "review_policy": "auto"})
        with store.operation_lock(task_id) as acquired:
            self.assertTrue(acquired)
            with store.operation_lock(task_id) as second:
                self.assertFalse(second)
            self.assertTrue(store.request_cancel(task_id)["cancel_requested"])
        with store.operation_lock(task_id) as acquired:
            self.assertTrue(acquired)
        with store.connect() as conn:
            conn.execute("DELETE FROM ae.events WHERE task_id=%s", (task_id,))
            conn.execute("DELETE FROM ae.tasks WHERE id=%s", (task_id,))
