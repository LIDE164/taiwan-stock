import io
import unittest
from unittest.mock import patch

from PIL import Image

import telegram_webhook
from telegram_stock_analysis import (
    StockQueryError,
    extract_stock_query,
    render_stock_analysis_image,
    resolve_stock_query,
)


class TelegramStockAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{
            "代號": "2330",
            "名稱": "台積電",
            "Data_Date": "2026-08-27",
            "Score": 82,
            "評級": "強勢候選",
            "產業": "半導體",
            "收盤價": 1000,
            "漲跌幅": 1.2,
            "5MA": 990,
            "20MA": 950,
            "60MA": 900,
            "RSI": 61,
            "ADX": 28,
            "BIAS": 5.2,
            "MACD柱": 2.5,
            "ATR": 20,
            "WinRate": 55,
            "Backtest_Samples": 40,
            "Confidence": 90,
            "Entry_Status": "現在可執行",
            "Entry_Reason": "價格已進入規劃區間。",
            "Entry_Low": 980,
            "Entry_High": 1005,
            "Entry_Stop": 960,
            "Entry_Target": 1060,
            "Whale_Net": 1200,
            "Whale_Net_Days": 3,
            "Institutional_Status": "ok",
            "EPS": 45,
            "MoM": None,
            "YoY": 12.5,
            "Revenue_Period": "2026-07",
            "Reasons": ["價格站上 20MA", "量能確認"],
        }]

    def test_extracts_stock_commands(self):
        self.assertEqual(extract_stock_query("/stock 2330"), "2330")
        self.assertEqual(extract_stock_query("/analyze@my_bot 台積電"), "台積電")

    def test_resolves_ticker_and_stock_name(self):
        names = {"2330": "台積電", "2317": "鴻海"}
        self.assertEqual(resolve_stock_query("2330", self.rows, names), ("2330", "台積電"))
        self.assertEqual(resolve_stock_query("台積電", self.rows, names), ("2330", "台積電"))

    def test_ambiguous_name_requests_a_ticker(self):
        with self.assertRaisesRegex(StockQueryError, "名稱不夠明確"):
            resolve_stock_query("科技", [], {"1111": "甲科技", "2222": "乙科技"})

    def test_single_stock_renderer_returns_mobile_png(self):
        png = render_stock_analysis_image(self.rows[0])
        image = Image.open(io.BytesIO(png))
        self.assertEqual(image.format, "PNG")
        self.assertEqual(image.size, (1080, 1400))

    def test_process_update_only_sends_to_allowed_chat(self):
        payload = {"update_id": 99, "message": {"message_id": 7, "chat": {"id": 123}, "text": "2330"}}
        with (
            patch.object(telegram_webhook, "_secret", side_effect=lambda name: "123" if "CHAT_ID" in name else "secret"),
            patch.object(telegram_webhook, "_claim_update", return_value=True),
            patch.object(telegram_webhook, "get_stock_analysis", return_value=self.rows[0]),
            patch.object(telegram_webhook, "_send_typing"),
            patch.object(telegram_webhook, "_send_analysis_photo") as send,
            patch.object(telegram_webhook, "_finish_update"),
        ):
            telegram_webhook._process_update(payload)
        send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
