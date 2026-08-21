import unittest
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

import scanner


class BatchMarketDataTests(unittest.TestCase):
    def test_scanner_keeps_institutional_daily_breakdown_for_persistence(self):
        provider_rows = [{
            "date": "2026-08-21", "foreign": 511, "trust": 0,
            "dealer": 15, "total": 526, "source": "FinMind",
        }]
        with patch.object(scanner, "fetch_institutional_rows", return_value=(provider_rows, "ok")):
            rows, status = scanner.get_institutional_trading("4416", with_status=True)
        self.assertEqual(status, "ok")
        self.assertEqual(rows[0]["外資(張)"], 511)
        self.assertEqual(rows[0]["投信(張)"], 0)
        self.assertEqual(rows[0]["自營商(張)"], 15)
        self.assertEqual(rows[0]["單日合計(張)"], 526)
        self.assertEqual(rows[0]["日期"], "08/21")

    def test_top_stock_pool_accepts_current_tpex_trading_shares_field(self):
        twse_response = Mock()
        twse_response.raise_for_status.return_value = None
        twse_response.json.return_value = [{"Code": "2330", "TradeVolume": "1000", "Name": "台積電"}]
        tpex_response = Mock()
        tpex_response.raise_for_status.return_value = None
        tpex_response.json.return_value = [{
            "SecuritiesCompanyCode": "6488",
            "TradingShares": "2000",
            "CompanyName": "環球晶",
        }]
        with patch.object(scanner, "http_get", side_effect=[twse_response, tpex_response]):
            ranked = scanner.fetch_top_stocks(2)
        self.assertEqual(ranked, ["6488", "2330"])
        self.assertEqual(scanner.MARKET_SYMBOL_CACHE["6488"], "6488.TWO")

    def test_scan_pool_keeps_exact_limit_and_core_names(self):
        ranked = [f"{1000 + index}" for index in range(10)]
        pool = scanner.build_scan_pool(ranked, 5, core_tickers=["2330", "2454"])
        self.assertEqual(len(pool), 5)
        self.assertIn("2330", pool)
        self.assertIn("2454", pool)

    def test_batch_download_is_mapped_back_to_stock_code(self):
        dates = pd.date_range("2026-07-01", periods=30, freq="B")
        symbol = "2330.TW"
        fields = ["Open", "High", "Low", "Close", "Volume"]
        columns = pd.MultiIndex.from_product([[symbol], fields])
        close = np.linspace(100, 115, len(dates))
        values = np.column_stack([
            close - 1,
            close + 1,
            close - 2,
            close,
            np.full(len(dates), 1000),
        ])
        downloaded = pd.DataFrame(values, index=dates, columns=columns)
        scanner.MARKET_SYMBOL_CACHE = {"2330": symbol}

        with patch.object(scanner.yf, "download", return_value=downloaded) as download:
            result = scanner.fetch_stock_data_batch(["2330"], chunk_size=50)

        self.assertIn("2330", result)
        self.assertIn("20MA", result["2330"].columns)
        download.assert_called_once()


if __name__ == "__main__":
    unittest.main()
