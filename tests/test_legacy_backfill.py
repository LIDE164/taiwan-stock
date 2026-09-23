from copy import deepcopy
import unittest

from backfill_legacy_backtest import merge_evidence
from test_legacy_backtest import snapshot


class LegacyBackfillTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{"代號": "2330", "Data_Date": "2026-09-22", "收盤價": 100,
                      "WinRate": 56.1, "Backtest_Samples": 1, "Validation_Samples": 0,
                      "Entry_Ready": False, "Entry_Status": "等待觸發", "Rank": 1,
                      "Legacy_Entry_Plan": {"Entry_Status": "現在可執行"}}]
        self.additions = {"2330": {"Legacy_Backtest": snapshot()}}

    def test_preserves_every_current_field_and_does_not_mutate_source(self):
        before = deepcopy(self.rows)
        result = merge_evidence(self.rows, self.additions, "2026-09-22")
        self.assertEqual(self.rows, before)
        for key, value in before[0].items():
            self.assertEqual(result[0][key], value)
        self.assertEqual(result[0]["Legacy_Backtest"]["samples"], 38)
        result[0]["Legacy_Backtest"]["samples"] = 9
        self.assertEqual(self.additions["2330"]["Legacy_Backtest"]["samples"], 38)

    def test_rejects_changes_to_trading_fields_and_unknown_tickers(self):
        for additions in ({"2330": {**self.additions["2330"], "Entry_Ready": True}},
                          {"9999": self.additions["2330"]}):
            with self.assertRaises(ValueError):
                merge_evidence(self.rows, additions, "2026-09-22")

    def test_stale_evidence_and_mixed_dates_are_rejected(self):
        for date in ("2026-09-21", "2026-09-23"):
            with self.assertRaises(ValueError):
                merge_evidence(self.rows, self.additions, date)
        additions = deepcopy(self.additions)
        additions["2330"]["Legacy_Backtest"]["data_through"] = "2026-09-21"
        with self.assertRaises(ValueError):
            merge_evidence(self.rows, additions, "2026-09-22")

    def test_mini_k_requires_matching_real_date_and_prices(self):
        bar = {"date": "2026-09-22", "open": 99, "high": 101, "low": 98, "close": 100}
        valid = {"2330": {**self.additions["2330"], "Mini_K": [bar]}}
        self.assertEqual(merge_evidence(self.rows, valid, "2026-09-22")[0]["Mini_K"], [bar])
        for changes in ({"date": "2026-09-23"}, {"close": 103}, {"high": 95}, {"low": float("nan")}):
            additions = {"2330": {**self.additions["2330"], "Mini_K": [{**bar, **changes}]}}
            with self.assertRaises(ValueError):
                merge_evidence(self.rows, additions, "2026-09-22")

    def test_intraday_prior_close_marker_cannot_admit_old_backfill(self):
        rows = deepcopy(self.rows)
        rows[0]["Legacy_Backtest_As_Of_Date"] = "2026-09-21"
        additions = deepcopy(self.additions)
        additions["2330"]["Legacy_Backtest"].update({
            "as_of_date": "2026-09-21", "data_through": "2026-09-21",
        })
        with self.assertRaises(ValueError):
            merge_evidence(rows, additions, "2026-09-22")


if __name__ == "__main__":
    unittest.main()
