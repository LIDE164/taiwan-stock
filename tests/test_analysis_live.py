import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from analysis_live import fetch_analysis_live_quote


class AnalysisLiveQuoteTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 26, 10, 5, tzinfo=timezone(timedelta(hours=8)))

    @patch("analysis_live.fetch_yahoo_live_history_bundle")
    @patch("analysis_live.http_get")
    def test_fugle_quote_is_preferred_and_timestamped(self, get, yahoo):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "openPrice": 100, "highPrice": 104, "lowPrice": 99,
            "closePrice": 103, "total": {"tradeVolume": 3000, "tradeValue": 306000},
        }
        get.return_value = response
        quote = fetch_analysis_live_quote("2330", {}, api_key="token", now_tpe=self.now)
        self.assertEqual(quote["close"], 103)
        self.assertEqual(quote["source"], "Fugle")
        self.assertEqual(quote["freshness"], "即時行情")
        self.assertEqual(quote["quote_time"], "2026-08-26 10:05:00")
        yahoo.assert_not_called()

    @patch("analysis_live.fetch_yahoo_live_history_bundle")
    def test_yahoo_is_used_when_fugle_is_unavailable(self, yahoo):
        yahoo.return_value = ({"6488": {
            "open": 100, "high": 104, "low": 99, "close": 102,
            "volume": 5000, "vwap": None,
        }}, {})
        quote = fetch_analysis_live_quote(
            "6488", {"Revenue_Source": "TPEx OpenAPI"}, api_key="", now_tpe=self.now,
        )
        self.assertEqual(quote["close"], 102)
        self.assertEqual(quote["source"], "Yahoo Chart 1d")
        self.assertEqual(quote["freshness"], "延遲行情")
        records = yahoo.call_args.args[0]
        self.assertEqual(len(records), 2)

    @patch("analysis_live.fetch_yahoo_live_history_bundle", return_value=({}, {}))
    def test_missing_live_quote_is_not_fabricated(self, yahoo):
        self.assertIsNone(fetch_analysis_live_quote("2330", {}, api_key="", now_tpe=self.now))


if __name__ == "__main__":
    unittest.main()
