import io
import json
import os
import unittest
from unittest.mock import patch

from ae_core.hapi import HapiClient, HapiError


class HapiClientTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"AE_HAPI_TOKEN": "test-token"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    @patch("ae_core.hapi.urllib.request.urlopen")
    def test_wrapped_failure_is_not_treated_as_success(self, open_url):
        open_url.return_value.__enter__.return_value = io.BytesIO(
            json.dumps({"success": False, "error": "runner unavailable"}).encode()
        )
        client = HapiClient()
        client.jwt = "authenticated"
        with self.assertRaisesRegex(HapiError, "runner unavailable"):
            client.request("POST", "/api/machines/example/spawn", {})

    def test_resume_returns_new_session_id(self):
        client = HapiClient()
        with patch.object(client, "request", return_value={"type": "success", "sessionId": "new-id"}) as request:
            self.assertEqual(client.resume("old-id"), "new-id")
        self.assertIn("old-id", request.call_args.args[1])
