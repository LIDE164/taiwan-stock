import unittest
from datetime import datetime
from unittest.mock import Mock, patch

import pandas as pd

import scanner
from scan_schedule import TPE


def bars(day, count=70):
    return pd.DataFrame({
        "Open": [100.0] * count, "High": [102.0] * count,
        "Low": [99.0] * count, "Close": [101.0] * count,
        "Volume": [1000] * count, "Dividends": [0.0] * count,
        "Stock Splits": [0.0] * count,
    }, index=pd.bdate_range(end=day, periods=count))


class ScannerDailyRecoveryTests(unittest.TestCase):
    def test_fresh_history_does_not_fetch_or_change_prices(self):
        frame = bars("2026-10-12")
        with patch.object(scanner.yf, "Ticker") as ticker:
            self.assertIs(scanner.recover_completed_bar(frame, "2330.TW", "2026-10-12"), frame)
        ticker.assert_not_called()

    def test_stale_history_uses_daily_bar_and_recalculates_indicators(self):
        frame, daily = bars("2026-10-08"), bars("2026-10-12", 1)
        daily.loc[:, "Close"] = 102.0
        with patch.object(scanner.yf, "Ticker") as ticker:
            ticker.return_value.history.return_value = daily
            result = scanner.recover_completed_bar(
                frame, "2330.TW", "2026-10-12", now=datetime(2026, 10, 12, 16, tzinfo=TPE))
        ticker.return_value.history.assert_called_once_with(
            period="1d", interval="1d", auto_adjust=True, actions=True, keepna=True, timeout=10)
        self.assertEqual(len(result), len(frame) + 1)
        self.assertEqual(result.iloc[-1]["Close"], 102)
        self.assertAlmostEqual(result.iloc[-1]["20MA"], (101 * 19 + 102) / 20)
        self.assertEqual(result.attrs["completed_daily_bar_date"], "2026-10-12")
        self.assertEqual(frame.index[-1].date().isoformat(), "2026-10-08")

    def test_bad_fallback_does_not_change_date_or_make_stock_eligible(self):
        for daily in (bars("2026-10-08", 1), bars("2026-10-13", 1), pd.DataFrame()):
            frame = bars("2026-10-08")
            with self.subTest(daily=daily.index.tolist()), patch.object(scanner.yf, "Ticker") as ticker:
                ticker.return_value.history.return_value = daily
                result = scanner.recover_completed_bar(
                    frame, "2330.TW", "2026-10-12", now=datetime(2026, 10, 12, 16, tzinfo=TPE))
            self.assertIs(result, frame)
            self.assertFalse(scanner.stock_frame_matches_scan_date(result, "2026-10-12"))

    def test_partial_current_bar_is_repaired_but_conflicting_observation_is_preserved(self):
        history = bars("2026-10-08")
        daily = bars("2026-10-12", 1)
        partial = pd.concat([history, daily])
        partial.loc[pd.Timestamp("2026-10-12"), "High"] = float("nan")
        with patch.object(scanner.yf, "Ticker") as ticker:
            ticker.return_value.history.return_value = daily
            result = scanner.recover_completed_bar(
                partial, "2330.TW", "2026-10-12", now=datetime(2026, 10, 12, 16, tzinfo=TPE))
        self.assertTrue(scanner.stock_frame_matches_scan_date(result, "2026-10-12"))
        partial.loc[pd.Timestamp("2026-10-12"), "Open"] = 100.5
        with patch.object(scanner.yf, "Ticker") as ticker:
            ticker.return_value.history.return_value = daily
            result = scanner.recover_completed_bar(
                partial, "2330.TW", "2026-10-12", now=datetime(2026, 10, 12, 16, tzinfo=TPE))
        self.assertIs(result, partial)
        self.assertFalse(scanner.stock_frame_matches_scan_date(result, "2026-10-12"))

    def test_batch_preserves_incomplete_last_day_for_conflict_check(self):
        history = bars("2026-10-08")
        daily = bars("2026-10-12", 1)
        daily.loc[:, "Close"] = float("nan")
        raw = pd.concat([history, daily])
        with patch.object(scanner.yf, "download", return_value=raw):
            result = scanner.fetch_stock_data_batch(["2330"])["2330"]
        self.assertEqual(result.index[-1].date().isoformat(), "2026-10-12")
        self.assertEqual(result.iloc[-1]["Open"], 100)
        self.assertTrue(pd.isna(result.iloc[-1]["Close"]))

    def test_benchmark_recovery_occurs_before_lease_with_exact_source_date(self):
        frame, daily = bars("2026-10-08"), bars("2026-10-12", 1)
        with (
            patch.object(scanner, "db", Mock()),
            patch.object(scanner, "_completed_late_backup_rows", return_value=None),
            patch.object(scanner.yf, "Ticker") as ticker,
            patch.object(scanner, "_load_saved_benchmarks", return_value=[]),
            patch.object(scanner, "_load_daily_scan_doc", return_value={}),
            patch.object(scanner, "_acquire_scan_lease", return_value=False) as lease,
            patch.object(scanner, "send_daily_notifications") as send,
        ):
            ticker.return_value.history.side_effect = [frame, daily]
            scanner.run_daily_scan(clock=lambda: datetime(2026, 10, 12, 16, tzinfo=TPE))
        lease.assert_called_once_with("2026-10-12", force=False)
        send.assert_not_called()

    def test_universe_dates_must_match_before_official_ranking_is_used(self):
        good_twse = {"Code": "2330", "TradeVolume": "1000", "Date": "1151012"}
        good_tpex = {"SecuritiesCompanyCode": "6488", "TradingShares": "2000", "Date": "1151012"}
        for side, date in (("twse", "1151008"), ("tpex", "1151013"), ("twse", "")):
            twse, tpex = dict(good_twse), dict(good_tpex)
            (twse if side == "twse" else tpex)["Date"] = date
            responses = [Mock(), Mock()]
            responses[0].json.return_value = [twse]
            responses[1].json.return_value = [tpex]
            with self.subTest(side=side, date=date), patch.object(scanner, "http_get", side_effect=responses):
                with self.assertRaisesRegex(RuntimeError, "宇宙不完整"):
                    scanner.fetch_top_stocks(2, expected_date="2026-10-12")
        responses = [Mock(), Mock()]
        responses[0].json.return_value = [good_twse]
        responses[1].json.return_value = [good_tpex]
        with patch.object(scanner, "http_get", side_effect=responses):
            self.assertEqual(scanner.fetch_top_stocks(2, expected_date="2026-10-12"), ["6488", "2330"])

    def test_provenance_survives_benchmark_reconciliation(self):
        frame = bars("2026-10-12")
        frame.attrs["completed_daily_bar_date"] = "2026-10-12"
        result = scanner.build_benchmark_context(scanner.reconcile_benchmark_history(frame, []))
        self.assertEqual(result["completed_daily_bar_date"], "2026-10-12")
        self.assertIn("verified daily bar", result["source"])

    def test_stale_openapi_uses_dated_official_volume_report_not_old_universe(self):
        stale = Mock()
        stale.json.return_value = [{"Code": "2330", "Name": "台積電", "TradeVolume": "9000", "Date": "1151008"}]
        dated = Mock()
        dated.json.return_value = {
            "stat": "OK", "date": "20261012", "type": "ALLBUT0999",
            "tables": [{"fields": ["證券代號", "證券名稱", "成交股數"],
                        "data": [["2330", "台積電", "1,000"]]}],
        }
        otc = Mock()
        otc.json.return_value = [{"SecuritiesCompanyCode": "6488", "TradingShares": "2000", "Date": "1151012"}]
        with patch.object(scanner, "http_get", side_effect=[stale, dated, otc]) as fetch:
            stocks = scanner.fetch_top_stocks(2, expected_date="2026-10-12")
        self.assertEqual(stocks, ["6488", "2330"])
        self.assertEqual(fetch.call_args_list[1].kwargs["params"],
                         {"date": "20261012", "type": "ALLBUT0999", "response": "json"})
        self.assertEqual(scanner.UNIVERSE_SOURCE_COUNTS, {"twse": 1, "tpex": 1})

    def test_stale_dated_fallback_cannot_relabel_universe(self):
        stale = Mock()
        stale.json.return_value = [{"Code": "2330", "TradeVolume": "1000", "Date": "1151008"}]
        dated = Mock()
        dated.json.return_value = {
            "stat": "OK", "date": "20261008", "type": "ALLBUT0999",
            "tables": [{"fields": ["證券代號", "證券名稱", "成交股數"],
                        "data": [["2330", "台積電", "1,000"]]}],
        }
        otc = Mock()
        otc.json.return_value = [{"SecuritiesCompanyCode": "6488", "TradingShares": "2000", "Date": "1151012"}]
        with patch.object(scanner, "http_get", side_effect=[stale, dated, otc]):
            with self.assertRaisesRegex(RuntimeError, "宇宙不完整"):
                scanner.fetch_top_stocks(2, expected_date="2026-10-12")


if __name__ == "__main__":
    unittest.main()
