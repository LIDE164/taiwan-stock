import unittest
from datetime import datetime

import pandas as pd

from intraday_quotes import quote_from_intraday_frame, yahoo_symbol_for_record


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


if __name__ == "__main__":
    unittest.main()
