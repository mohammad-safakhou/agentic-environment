import unittest
from unittest.mock import MagicMock, patch

from ae_core.workflow import advance, cancel, parse_review, requires_review, safe_name


class WorkflowTests(unittest.TestCase):
    @patch("ae_core.workflow.command")
    def test_explicit_review_skip_never_requests_another_provider(self, command):
        self.assertFalse(requires_review({"review_policy": "skip"}, {}))
        command.assert_not_called()

    @patch.dict("os.environ", {"AE_PUBLISH_MODE": "local"})
    @patch("ae_core.workflow.publish")
    @patch("ae_core.workflow.command")
    @patch("ae_core.workflow.HapiClient")
    @patch("ae_core.workflow.store")
    @patch("ae_core.workflow.load_config", return_value={})
    def test_local_result_stops_session_without_publishing(self, load_config, store, client_type,
                                                           command, publish):
        task = {"state": "publishing", "cancel_requested": False,
                "branch": "codex/test", "commit_sha": "abc123", "session_id": "session-1"}
        store.get.return_value = task
        client_type.return_value.session.side_effect = [{"active": True, "thinking": False},
                                                        {"active": False}]
        locked = MagicMock()
        locked.__enter__.return_value = (MagicMock(), task)
        store.locked_task.return_value = locked

        advance("task-id")

        publish.assert_not_called()
        command.assert_called_once_with(["hapi", "runner", "stop-session", "session-1"], agent=True)
        store.update.assert_called_once_with(locked.__enter__.return_value[0],
                                             "task-id", state="done", slot=None)
        store.event.assert_called_once_with(locked.__enter__.return_value[0],
                                            "task-id", "local_result_ready",
                                            {"branch": "codex/test", "commit": "abc123"})

    def test_repository_name_rejects_path_escape(self):
        for value in ("../etc", "x/../y", "-bad", ""):
            with self.assertRaises(ValueError):
                safe_name(value)
        self.assertEqual(safe_name("feature/change"), "feature/change")

    def test_review_requires_structured_findings(self):
        messages = [{"content": {"role": "agent", "content": {"data": {
            "message": '{"findings": [{"file": "src/a.py", "line": 4, "reason": "bug"}]}'
        }}}}]
        self.assertEqual(parse_review(messages)["findings"][0]["file"], "src/a.py")
        with self.assertRaises(RuntimeError):
            parse_review([{"content": {"role": "agent", "content": {"data": {"message": "Looks good"}}}}])

    @patch("ae_core.workflow.store")
    @patch("ae_core.workflow.command")
    @patch("ae_core.workflow.HapiClient")
    def test_cancellation_stops_implementation_and_review(self, client_type, command, store):
        task = {"state": "review_waiting", "session_id": "implementation",
                "review_session_id": "review"}
        store.get.return_value = task
        client = client_type.return_value
        client.session.side_effect = [
            {"active": True, "thinking": False}, {"active": False},
            {"active": True, "thinking": False}, {"active": False},
        ]
        locked = MagicMock()
        locked.__enter__.return_value = (MagicMock(), task)
        store.locked_task.return_value = locked

        cancel("task-id")

        self.assertEqual(command.call_count, 2)
        self.assertEqual(command.call_args_list[0].args[0][-1], "implementation")
        self.assertEqual(command.call_args_list[1].args[0][-1], "review")
        store.update.assert_called_once_with(locked.__enter__.return_value[0], "task-id",
                                             state="cancelled", slot=None,
                                             error=None, failed_stage=None)
