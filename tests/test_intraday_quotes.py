import unittest
from datetime import datetime
from unittest.mock import patch

import pandas as pd

from intraday_quotes import (
    fetch_yahoo_history_frames,
    history_and_quote_from_chart_result,
    merge_intraday_quote_into_history,
    quote_from_intraday_frame,
    yahoo_symbol_for_record,
)


class IntradayQuoteTests(unittest.TestCase):
    def test_tpex_provenance_uses_two_suffix(self):
        self.assertEqual(
            yahoo_symbol_for_record({"代號": "6488", "Revenue_Source": "TPEx OpenAPI"}),
            "6488.TWO",
        )
        self.assertEqual(
            yahoo_symbol_for_record({"代號": "2330", "Revenue_Source": "TWSE OpenAPI"}),
            "2330.TW",
        )

    def test_intraday_bars_are_aggregated_without_synthetic_values(self):
        index = pd.DatetimeIndex([
            "2026-08-21 09:00:00+08:00",
            "2026-08-21 09:05:00+08:00",
        ])
        frame = pd.DataFrame({
            "Open": [100, 101], "High": [102, 104], "Low": [99, 100],
            "Close": [101, 103], "Volume": [1000, 2000],
        }, index=index)
        quote = quote_from_intraday_frame(frame, trading_date="2026-08-21")
        self.assertEqual(quote["open"], 100)
        self.assertEqual(quote["high"], 104)
        self.assertEqual(quote["low"], 99)
        self.assertEqual(quote["close"], 103)
        self.assertEqual(quote["volume"], 3000)
        self.assertEqual(quote["source"], "Yahoo 5m")

    def test_previous_day_bars_are_rejected(self):
        frame = pd.DataFrame({
            "Open": [100], "High": [101], "Low": [99], "Close": [100], "Volume": [1000],
        }, index=pd.DatetimeIndex([datetime(2026, 8, 20, 9, 0)]))
        self.assertIsNone(quote_from_intraday_frame(frame, trading_date="2026-08-21"))

    def test_live_quote_replaces_today_without_synthetic_ohlcv(self):
        history = pd.DataFrame({
            "Open": [90], "High": [95], "Low": [89], "Close": [94], "Volume": [800],
        }, index=pd.DatetimeIndex(["2026-08-20"]))
        quote = {
            "date": "2026-08-21", "open": 100, "high": 104, "low": 99,
            "close": 103, "volume": 3000, "vwap": 102.2,
        }
        merged = merge_intraday_quote_into_history(
            history, quote, trading_date="2026-08-21",
        )
        latest = merged.loc[pd.Timestamp("2026-08-21")]
        self.assertEqual(latest["Open"], 100)
        self.assertEqual(latest["High"], 104)
        self.assertEqual(latest["Low"], 99)
        self.assertEqual(latest["Close"], 103)
        self.assertEqual(latest["Volume"], 3000)

    def test_stale_live_quote_is_not_merged(self):
        history = pd.DataFrame({
            "Open": [90], "High": [95], "Low": [89], "Close": [94], "Volume": [800],
        }, index=pd.DatetimeIndex(["2026-08-20"]))
        quote = {
            "date": "2026-08-20", "open": 100, "high": 104, "low": 99,
            "close": 103, "volume": 3000,
        }
        self.assertIsNone(merge_intraday_quote_into_history(
            history, quote, trading_date="2026-08-21",
        ))

    def test_chart_json_parser_requires_a_current_complete_daily_bar(self):
        index = pd.date_range("2026-07-20 01:00:00+00:00", periods=25, freq="B")
        index = index[:-1].append(pd.DatetimeIndex(["2026-08-21 01:00:00+00:00"]))
        result = {
            "timestamp": [int(value.timestamp()) for value in index],
            "indicators": {
                "quote": [{
                    "open": [100.0] * 25,
                    "high": [104.0] * 25,
                    "low": [99.0] * 25,
                    "close": [103.0] * 25,
                    "volume": [3000] * 25,
                }],
                "adjclose": [{"adjclose": [103.0] * 25}],
            },
        }
        history, quote = history_and_quote_from_chart_result(
            result,
            trading_date="2026-08-21",
        )
        self.assertEqual(len(history), 25)
        self.assertEqual(quote["date"], "2026-08-21")
        self.assertEqual(quote["close"], 103)
        self.assertIsNone(quote["vwap"])

    @patch("intraday_quotes.yf.download")
    def test_daily_history_uses_one_batch_download(self, download_mock):
        index = pd.date_range("2026-07-01", periods=25, freq="B")
        columns = pd.MultiIndex.from_product([
            ["2330.TW"], ["Open", "High", "Low", "Close", "Volume"],
        ])
        download_mock.return_value = pd.DataFrame(
            [[100, 102, 99, 101, 1000]] * 25,
            index=index,
            columns=columns,
        )
        histories = fetch_yahoo_history_frames([
            {"代號": "2330", "Revenue_Source": "TWSE OpenAPI"},
        ])
        self.assertIn("2330", histories)
        self.assertEqual(len(histories["2330"]), 25)
        self.assertEqual(download_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
