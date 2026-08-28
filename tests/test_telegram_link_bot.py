import json
import unittest
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
    def test_poller_removes_webhook_replies_and_confirms_updates(self, api):
        api.side_effect = [
            {"ok": True, "result": {"url": "https://old-webhook.example"}},
            {"ok": True, "result": True},
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
        ]
        count = telegram_link_bot.poll_once("token", "123", "https://example.streamlit.app/")
        self.assertEqual(count, 1)
        methods = [call.args[1] for call in api.call_args_list]
        self.assertEqual(methods, [
            "getWebhookInfo", "deleteWebhook", "getUpdates", "sendMessage", "getUpdates",
        ])
        send_data = api.call_args_list[3].args[2]
        keyboard = json.loads(send_data["reply_markup"])
        self.assertIn("query=", keyboard["inline_keyboard"][0][0]["url"])
        confirm_data = api.call_args_list[-1].args[2]
        self.assertEqual(confirm_data["offset"], 12)


if __name__ == "__main__":
    unittest.main()
