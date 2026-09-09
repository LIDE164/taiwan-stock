import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import telegram_link_bot
from telegram_links import build_analysis_url, extract_stock_query, is_valid_stock_query


class TelegramLinkTests(unittest.TestCase):
    def test_query_extraction_and_validation(self):
        self.assertEqual(extract_stock_query("/stock 2330"), "2330")
        self.assertEqual(extract_stock_query("/analyze@bot 台積電"), "台積電")
        self.assertTrue(is_valid_stock_query("2330"))
        self.assertTrue(is_valid_stock_query("台積電"))
        self.assertFalse(is_valid_stock_query("https://example.com"))

    def test_analysis_url_encodes_ticker_and_name(self):
        base = "https://example.streamlit.app/"
        self.assertEqual(build_analysis_url("2330", base), f"{base}?stock=2330")
        name_url = build_analysis_url("台積電", base)
        self.assertIn("query=%E5%8F%B0%E7%A9%8D%E9%9B%BB", name_url)
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            build_analysis_url("2330", "http://example.com")

    @patch("telegram_link_bot._telegram_api")
    def test_poller_replies_and_checkpoints_each_update(self, api):
        api.side_effect = [
            {"ok": True, "result": {"url": ""}},
            {"ok": True, "result": [
                {"update_id": 10, "message": {
                    "message_id": 7, "chat": {"id": 123}, "text": "台積電",
                }},
                {"update_id": 11, "message": {
                    "message_id": 8, "chat": {"id": 999}, "text": "2330",
                }},
            ]},
            {"ok": True, "result": {"message_id": 20}},
            {"ok": True, "result": []},
            {"ok": True, "result": []},
        ]
        count = telegram_link_bot.poll_once("token", "123", "https://example.streamlit.app/")
        self.assertEqual(count, 1)
        methods = [call.args[1] for call in api.call_args_list]
        self.assertEqual(methods, [
            "getWebhookInfo", "getUpdates", "sendMessage", "getUpdates", "getUpdates",
        ])
        send_data = api.call_args_list[2].args[2]
        keyboard = json.loads(send_data["reply_markup"])
        self.assertIn("query=", keyboard["inline_keyboard"][0][0]["url"])
        confirm_data = api.call_args_list[-1].args[2]
        self.assertEqual(confirm_data["offset"], 12)

    @patch("telegram_link_bot._telegram_api")
    def test_poller_never_deletes_an_active_webhook(self, api):
        api.return_value = {"ok": True, "result": {"url": "https://webhook.example/secret-path"}}
        output = io.StringIO()
        with redirect_stdout(output):
            count = telegram_link_bot.poll_once("token", "123")
        self.assertEqual(count, 0)
        self.assertEqual([call.args[1] for call in api.call_args_list], ["getWebhookInfo"])
        self.assertIn("poll_skipped=webhook_active", output.getvalue())
        self.assertNotIn("secret-path", output.getvalue())

    @patch("telegram_link_bot._telegram_api")
    def test_later_reply_failure_does_not_replay_a_checkpointed_reply(self, api):
        updates = [
            {"update_id": 10, "message": {"message_id": 7, "chat": {"id": 123}, "text": "2330"}},
            {"update_id": 11, "message": {"message_id": 8, "chat": {"id": 123}, "text": "2317"}},
        ]
        api.side_effect = [
            {"ok": True, "result": {"url": ""}},
            {"ok": True, "result": updates},
            {"ok": True, "result": {"message_id": 20}},
            {"ok": True, "result": []},
            RuntimeError("temporary send failure"),
        ]
        with self.assertRaisesRegex(RuntimeError, "temporary"):
            telegram_link_bot.poll_once("token", "123")
        checkpoint_calls = [
            call for call in api.call_args_list
            if call.args[1] == "getUpdates" and call.args[2].get("offset") is not None
        ]
        self.assertEqual(len(checkpoint_calls), 1)
        self.assertEqual(checkpoint_calls[0].args[2]["offset"], 11)

    @patch("telegram_link_bot.time.sleep")
    @patch("telegram_link_bot._telegram_api")
    def test_checkpoint_retries_idempotent_get_updates(self, api, sleep):
        api.side_effect = [RuntimeError("temporary"), {"ok": True, "result": []}]
        telegram_link_bot._checkpoint_update("token", 12)
        self.assertEqual(api.call_count, 2)
        sleep.assert_called_once()


if __name__ == "__main__":
    unittest.main()
