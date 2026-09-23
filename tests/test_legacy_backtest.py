from copy import deepcopy
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import legacy_backtest_core as legacy
from legacy_backtest import calculate_legacy_backtest, LEGACY_BACKTEST_COMMIT, LEGACY_BACKTEST_SCHEMA
from backtest_reporting import primary_backtest_display, replace_backtest_snapshot
from ranking_comparison import build_comparison_rows
from top10_telegram import build_executable_display_rows, build_top10_display_rows
from ui_components import generate_cards_html


def frame(count=120):
    x = np.arange(count)
    close = 100 + x * .1 + np.sin(x / 4) * 3
    return pd.DataFrame({
        "Open": close - .2, "High": close + 1, "Low": close - 1,
        "Close": close, "Volume": 100000 + (x % 5) * 10000,
    }, index=pd.bdate_range("2025-01-02", periods=count))


def snapshot(samples=38, wins=18):
    return {
        "schema": LEGACY_BACKTEST_SCHEMA, "source_commit": LEGACY_BACKTEST_COMMIT,
        "as_of_date": "2026-09-22", "data_through": "2026-09-22", "status": "complete",
        "samples": samples, "wins": wins, "losses": samples - wins,
        "win_rate": legacy.summarize_winrate(wins, samples)["adjusted_win_rate"] if samples else None,
    }


class LegacyBacktestTests(unittest.TestCase):
    def test_matches_frozen_engine_and_ignores_current_derived_indicators(self):
        data = frame()
        expected = legacy.calculate_historical_performance(legacy.apply_technical_indicators(data))
        data["ATR"] = 9999
        data["20MA"] = -1
        before = data.copy(deep=True)
        with patch("scoring.get_decision_score", side_effect=AssertionError("current model leaked")):
            result = calculate_legacy_backtest(data, as_of_date=str(data.index[-1].date()))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["samples"], expected["closed_signals"])
        self.assertEqual(result["wins"], expected["wins"])
        self.assertEqual(result["win_rate"], expected["win_rate"] if expected["closed_signals"] else None)
        pd.testing.assert_frame_equal(data, before)

    def test_future_bars_cannot_change_as_of_snapshot(self):
        data = frame(130)
        date = str(data.index[119].date())
        self.assertEqual(calculate_legacy_backtest(data, as_of_date=date), calculate_legacy_backtest(data.iloc[:120], as_of_date=date))

    def test_float_roundoff_is_not_rejected_but_actual_bad_prices_are(self):
        data = frame()
        date = str(data.index[-1].date())
        close = data.iloc[-1]["Close"]
        data.loc[data.index[-1], "High"] = np.nextafter(close, 0)
        original = data.copy(deep=True)
        self.assertEqual(calculate_legacy_backtest(data, as_of_date=date)["status"], "complete")
        pd.testing.assert_frame_equal(data, original)
        data.loc[data.index[-1], "High"] = close - .000001
        self.assertEqual(calculate_legacy_backtest(data, as_of_date=date)["status"], "invalid_history")

    def test_invalid_stale_and_insufficient_history_stay_unavailable(self):
        data = frame()
        date = str(data.index[-1].date())
        bad = data.copy()
        bad.loc[bad.index[-1], "High"] = 1
        for value, as_of, status in (
            (bad, date, "invalid_history"),
            (data.iloc[:-1], date, "stale_history"),
            (data.iloc[-10:], date, "insufficient_history"),
            (data, "bad", "invalid_history"),
            (pd.concat([data, data.tail(1)]), date, "invalid_history"),
        ):
            result = calculate_legacy_backtest(value, as_of_date=as_of)
            self.assertEqual(result["status"], status)
            self.assertNotIn("win_rate", result)

    def test_legitimate_zero_samples_are_not_a_zero_percent_forecast(self):
        with patch.object(legacy, "is_strategy_signal", return_value=(False, 0, {})):
            data = frame()
            result = calculate_legacy_backtest(data, as_of_date=str(data.index[-1].date()))
        self.assertEqual(result["samples"], 0)
        self.assertEqual(result["status"], "complete")
        self.assertIsNone(result["win_rate"])
        evidence = primary_backtest_display({"Legacy_Backtest": snapshot(0, 0)})
        self.assertTrue(evidence["available"])
        self.assertEqual(evidence["samples"], 0)
        self.assertIsNone(evidence["win_rate"])

    def test_old_count_includes_all_completed_trades_not_training_only(self):
        data = frame(180)
        with patch.object(legacy, "is_strategy_signal", return_value=(True, 70, {})):
            stats = legacy.calculate_historical_performance(legacy.apply_technical_indicators(data))
            result = calculate_legacy_backtest(data, as_of_date=str(data.index[-1].date()))
        self.assertGreater(stats["validation_samples"], 0)
        self.assertEqual(result["samples"], len(stats["trades"]))
        self.assertGreater(result["samples"], result["validation_samples"])
        self.assertEqual(legacy.summarize_winrate(18, 38)["adjusted_win_rate"], 47.4)

    def test_invalid_provenance_date_and_counts_never_fall_back_to_new_rate(self):
        for updates in ({"schema": "executable_v3"}, {"source_commit": "other"},
                        {"as_of_date": "2026-09-21"}, {"status": "stale_history"},
                        {"wins": 100}, {"win_rate": float("nan")}, {"samples": -1}):
            row = {"Data_Date": "2026-09-22", "WinRate": 99, "Backtest_Samples": 100,
                   "Legacy_Backtest": {**snapshot(), **updates}}
            result = primary_backtest_display(row)
            self.assertFalse(result["available"])
            self.assertIsNone(result["win_rate"])

    def test_display_uses_legacy_with_current_evidence_independently_preserved(self):
        row = {
            "代號": "2330", "名稱": "測試", "Data_Date": "2026-09-22", "Score": 80,
            "收盤價": 100, "Entry_Status": "現在可執行", "Entry_Ready": True,
            "Backtest_Schema": "executable_v3", "WinRate": 56.1,
            "Backtest_Samples": 1, "Backtest_Training_Samples": 1,
            "Backtest_Overall_Samples": 2, "Validation_Samples": 1,
            "Model_Confidence_Label": "新制樣本不足", "Legacy_Backtest": snapshot(),
        }
        before = deepcopy(row)
        top = build_top10_display_rows([row])[0]
        exe = build_executable_display_rows([row])[0]
        for value in (top, exe):
            self.assertEqual(value["win_rate_text"], "47.4%")
            self.assertEqual(value["sample_breakdown_text"], "舊樣本 38")
            self.assertEqual(value["current_sample_text"], "新制 全2/訓1/驗1")
        self.assertEqual(exe["credibility_text"], "中等可信")
        html = generate_cards_html(pd.DataFrame([row]))
        self.assertIn("47.4%", html)
        self.assertIn("38｜中等可信", html)
        self.assertNotIn("56.1%", html)
        self.assertEqual(row, before)
        comparison = build_comparison_rows([row])
        self.assertEqual(comparison[0]["WinRate"], 56.1)
        self.assertEqual(comparison[0]["Backtest_Samples"], 1)
        target = {"Legacy_Backtest": snapshot(10, 5)}
        replace_backtest_snapshot(target, row)
        target["Legacy_Backtest"]["samples"] = 10
        self.assertEqual(row["Legacy_Backtest"]["samples"], 38)


if __name__ == "__main__":
    unittest.main()
