import io
import unittest
from unittest.mock import patch

import pandas as pd
from PIL import Image

import telegram_webhook
import telegram_stock_analysis
from telegram_stock_analysis import (
    StockQueryError,
    extract_stock_query,
    render_stock_analysis_image,
    resolve_stock_query,
)


class _Snapshot:
    def __init__(self, value):
        self._value = value
        self.exists = value is not None

    def to_dict(self):
        return dict(self._value or {})


class _Document:
    def __init__(self, value):
        self._value = value

    def get(self):
        return _Snapshot(self._value)


class _Collection:
    def __init__(self, documents):
        self._documents = documents

    def document(self, document_id):
        return _Document(self._documents.get(document_id))


class _Database:
    def __init__(self, collections):
        self._collections = collections

    def collection(self, collection_name):
        return _Collection(self._collections.get(collection_name, {}))


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

    def test_scan_rows_hydrates_schema2_daily_scan_chunks(self):
        chunk_id = "daily_scan__2026-09-07__000"
        database = _Database({
            "market_data": {
                "daily_scan": {
                    "storage_schema": 2,
                    "scan_date": "2026-09-07",
                    "record_count": 1,
                    "chunk_ids": [chunk_id],
                }
            },
            "daily_scan_chunks": {
                chunk_id: {
                    "storage_schema": 2,
                    "chunk_index": 0,
                    "chunk_count": 1,
                    "item_count": 1,
                    "items": [self.rows[0]],
                }
            },
        })

        with patch.object(telegram_stock_analysis.scanner, "db", database):
            rows, scan_date = telegram_stock_analysis._scan_rows()

        self.assertEqual(rows, self.rows)
        self.assertEqual(scan_date, "2026-09-07")

    def test_ambiguous_name_requests_a_ticker(self):
        with self.assertRaisesRegex(StockQueryError, "名稱不夠明確"):
            resolve_stock_query("科技", [], {"1111": "甲科技", "2222": "乙科技"})

    def test_single_stock_renderer_returns_mobile_png(self):
        png = render_stock_analysis_image(self.rows[0])
        image = Image.open(io.BytesIO(png))
        self.assertEqual(image.format, "PNG")
        self.assertEqual(image.size, (1080, 1400))

    @staticmethod
    def _technical_frame(data_date: str, five_ma: float) -> pd.DataFrame:
        return pd.DataFrame(
            [{
                "5MA": five_ma,
                "20MA": five_ma - 1,
                "60MA": five_ma - 2,
                "MACD_Hist": 1.25,
                "RSI": 60,
                "ADX": 25,
                "BIAS_20": 2.5,
                "ATR": 3,
            }],
            index=pd.to_datetime([data_date]),
        )

    def test_cached_snapshot_is_enriched_only_from_the_same_market_date(self):
        frame = self._technical_frame("2026-08-27", 995)
        with (
            patch("telegram_stock_analysis._scan_rows", return_value=(self.rows, "2026-08-27")),
            patch("telegram_stock_analysis._name_map", return_value={"2330": "台積電"}),
            patch("telegram_stock_analysis.scanner.get_stock_data", return_value=frame),
        ):
            result = __import__("telegram_stock_analysis").get_stock_analysis("2330")

        self.assertEqual(result["Data_Date"], "2026-08-27")
        self.assertEqual(result["Score"], 82)
        self.assertEqual(result["5MA"], 995)

    def test_newer_market_frame_returns_complete_fresh_analysis(self):
        frame = self._technical_frame("2026-08-28", 1005)
        fresh = {
            "代號": "2330",
            "名稱": "台積電",
            "Data_Date": "2026-08-28",
            "Score": 91,
            "5MA": 1005,
            "Analysis_Source": "即時重新計算",
        }
        with (
            patch("telegram_stock_analysis._scan_rows", return_value=(self.rows, "2026-08-27")),
            patch("telegram_stock_analysis._name_map", return_value={"2330": "台積電"}),
            patch("telegram_stock_analysis.scanner.get_stock_data", return_value=frame),
            patch("telegram_stock_analysis.analyze_stock_fresh", return_value=fresh) as analyze,
        ):
            result = __import__("telegram_stock_analysis").get_stock_analysis("2330")

        self.assertIs(result, fresh)
        analyze.assert_called_once()
        self.assertEqual(analyze.call_args.args, ("2330", "台積電"))
        self.assertIs(analyze.call_args.kwargs["frame"], frame)

    def test_older_market_frame_preserves_cached_snapshot_without_mixing(self):
        frame = self._technical_frame("2026-08-26", 1234)
        with (
            patch("telegram_stock_analysis._scan_rows", return_value=(self.rows, "2026-08-27")),
            patch("telegram_stock_analysis._name_map", return_value={"2330": "台積電"}),
            patch("telegram_stock_analysis.scanner.get_stock_data", return_value=frame),
            patch("telegram_stock_analysis.analyze_stock_fresh") as analyze,
        ):
            result = __import__("telegram_stock_analysis").get_stock_analysis("2330")

        self.assertEqual(result["Data_Date"], "2026-08-27")
        self.assertEqual(result["Score"], 82)
        self.assertEqual(result["5MA"], 990)
        analyze.assert_not_called()

    def test_process_update_only_sends_to_allowed_chat(self):
        payload = {"update_id": 99, "message": {"message_id": 7, "chat": {"id": 123}, "text": "2330"}}
        with (
            patch.object(telegram_webhook, "_secret", side_effect=lambda name: "123" if "CHAT_ID" in name else "secret"),
            patch.object(telegram_webhook, "_claim_update", return_value=True),
            patch.object(telegram_webhook, "_send_analysis_link") as send,
            patch.object(telegram_webhook, "_finish_update"),
        ):
            telegram_webhook._process_update(payload)
        send.assert_called_once_with("123", "2330", 7)


if __name__ == "__main__":
    unittest.main()
