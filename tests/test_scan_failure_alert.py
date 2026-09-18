import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from scan_failure_alert import build_failure_message, send_failure_alert


class ScanFailureAlertTests(unittest.TestCase):
    def test_message_is_generic_and_links_the_failed_run(self):
        text = build_failure_message(
            repository="owner/repo",
            run_id="123",
            now=datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc),
        )
        self.assertIn("台股每日掃描失敗", text)
        self.assertIn("https://github.com/owner/repo/actions/runs/123", text)
        self.assertIn("未完整完成", text)
        self.assertNotIn("未覆寫", text)
        self.assertNotIn("token", text.lower())

    def test_send_uses_post_and_never_places_token_in_message(self):
        response = Mock()
        session = Mock()
        session.post.return_value = response
        send_failure_alert("secret-token", "42", session=session)
        request = session.post.call_args
        self.assertIn("secret-token", request.args[0])
        self.assertNotIn("secret-token", request.kwargs["data"]["text"])
        response.raise_for_status.assert_called_once_with()

    def test_missing_credentials_fail_closed(self):
        with self.assertRaises(RuntimeError):
            send_failure_alert("", "42")

    @patch("scan_failure_alert.urlopen")
    def test_default_sender_uses_standard_library_only(self, mocked_urlopen):
        response = mocked_urlopen.return_value.__enter__.return_value
        send_failure_alert("secret-token", "42")
        request = mocked_urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertIn(b"chat_id=42", request.data)
        response.read.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
