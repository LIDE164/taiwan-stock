import unittest
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

import scanner


class BatchMarketDataTests(unittest.TestCase):
    def test_benchmark_context_uses_same_day_taiex_and_regime(self):
        dates = pd.date_range("2026-06-01", periods=60, freq="B")
        frame = pd.DataFrame({"Close": [200.0] * 58 + [202.0, 198.0]}, index=dates)
        result = scanner.build_benchmark_context(frame)
        self.assertEqual(result["symbol"], "TAIEX")
        self.assertEqual(result["daily_return_pct"], -1.98)
        self.assertEqual(result["regime"], "空頭")
        self.assertEqual(scanner.build_benchmark_context(frame.tail(1)), {})

    def test_daily_scan_passes_same_day_gain_to_anti_chase_rules(self):
        twii_dates = pd.date_range("2026-06-01", periods=70, freq="B")
        twii = pd.DataFrame({"Close": np.linspace(22000, 23000, len(twii_dates))}, index=twii_dates)
        stock_dates = pd.to_datetime(["2026-09-03", "2026-09-04"])
        stock_frame = pd.DataFrame(
            {
                "Open": [99.0, 102.0],
                "High": [101.0, 110.0],
                "Low": [98.0, 101.0],
                "Close": [100.0, 108.0],
                "Volume": [1_000_000, 2_000_000],
                "20MA": [99.0, 100.0],
                "MACD_Hist": [0.5, 1.0],
                "ATR": [4.0, 4.0],
            },
            index=stock_dates,
        )
        score_input = {
            "收盤價": 108.0,
            # Keep the price inside the otherwise executable pullback zone. This
            # isolates the same-day +8% anti-chase rule from the zone check.
            "20MA": 108.0,
            "ATR": 4.0,
            "BB_UP": 125.0,
            "RSI": 55.0,
            "BIAS": 1.0,
            "Signal_Conflict": "低",
            "Entry_Pattern": "一般觀察型",
            "Volume_Confirmed": True,
            "Est_Vol_Ratio": 1.2,
        }
        backtest = {
            "win_rate": 50.0,
            "closed_signals": 30,
            "backtest_scope": "test",
            "validation_win_rate": 50.0,
            "validation_samples": 10,
        }

        with (
            patch.object(scanner, "db", None),
            patch.object(scanner, "call_with_backoff", return_value=twii),
            patch.object(scanner, "scan_universe_limit", return_value=1),
            patch.object(scanner, "build_industry_cache"),
            patch.object(scanner, "fetch_top_stocks", return_value=["1234"]),
            patch.object(scanner, "build_scan_pool", return_value=["1234"]),
            patch.object(scanner, "fetch_stock_data_batch", return_value={"1234": stock_frame}),
            patch.object(
                scanner,
                "get_fundamental_and_industry_data",
                return_value={"EPS": "5", "EPS_Period": "ttm", "Industry": "電子", "_status": "ok"},
            ),
            patch.object(scanner, "is_financial_stock", return_value=False),
            patch.object(
                scanner,
                "get_finmind_revenue",
                return_value={"mom": 1.0, "yoy": 2.0, "status": "ok", "period": "2026-08", "source": "test"},
            ),
            patch.object(scanner, "build_score_input", return_value=score_input),
            patch.object(scanner, "build_scan_quality", return_value=({}, 100)),
            patch.object(scanner, "get_decision_score", return_value=(90, "強勢候選", [], "test")),
            patch.object(scanner, "get_institutional_trading", return_value=([], "ok")),
            patch.object(scanner, "calc_winrate", return_value=backtest),
        ):
            rows = scanner.run_daily_scan(force=True, allow_local=True, send_telegram=False)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["漲跌幅"], 8.0)
        self.assertEqual(rows[0]["Entry_Status"], "等待拉回")
        self.assertFalse(rows[0]["Entry_Ready"])
        self.assertIn("單日上漲 8.0%", rows[0]["Entry_Reason"])

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
