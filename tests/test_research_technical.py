"""Synthetic OHLCV fixtures test research logic, never production market data."""

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from research_technical import _summary, analyze_timeframes, run_research_backtest


def candles(size=120):
    index = pd.bdate_range("2025-01-06", periods=size)
    return pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1000.0}, index=index)


def indicators_with_signals(frame):
    result = frame.copy()
    result["ma20"], result["ma60"], result["rsi14"], result["atr14"] = 100.0, 100.0, 40.0, 2.0
    for row in (60, 62, 70):
        if len(result) > row:
            result.iloc[row, result.columns.get_loc("ma20")] = 101.0
            result.iloc[row - 1, result.columns.get_loc("rsi14")] = 20.0
    return result


class TimeframeResearchTests(unittest.TestCase):
    def test_daily_prices_and_rolling_windows_are_observed_not_generated(self):
        data = candles(100)
        data.iloc[30, data.columns.get_loc("High")] = 110
        data.iloc[90, data.columns.get_loc("High")] = 105
        data.iloc[91, data.columns.get_loc("Low")] = 96
        original = data.copy(deep=True)
        report = analyze_timeframes(data, data.index[-1])
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["daily"]["resistance20"], 105)
        self.assertEqual(report["daily"]["resistance60"], 105)
        self.assertEqual(report["daily"]["support20"], 96)
        self.assertEqual(report["daily"]["ma60"], 100)
        self.assertEqual(report["daily"]["rsi14"], 50)
        pd.testing.assert_frame_equal(data, original)

    def test_midweek_excludes_incomplete_week_and_future_rows(self):
        data = candles(100)
        as_of = data.index[-3]  # Wednesday.
        report = analyze_timeframes(data, as_of)
        self.assertEqual(report["data_date"], str(as_of.date()))
        self.assertEqual(report["weekly"]["date"], str(data.index[-6].date()))
        data.loc[data.index[-1], "Close"] = np.nan  # Future bad data is irrelevant.
        self.assertEqual(analyze_timeframes(data, as_of), report)

    def test_completed_friday_week_is_included(self):
        data = candles(100)
        report = analyze_timeframes(data, data.index[-1])
        self.assertEqual(report["weekly"]["date"], str(data.index[-1].date()))
        self.assertEqual(report["weekly"]["bars"], 20)
        self.assertEqual(report["weekly"]["status"], "insufficient_history")
        self.assertIsNone(report["weekly"]["ma60"])
        self.assertEqual(report["weekly"]["ma20"], 100)

    def test_stale_midweek_data_does_not_become_complete_friday_candle(self):
        data = candles(100)
        result = analyze_timeframes(data.iloc[:-2], data.index[-1])
        self.assertEqual(result["weekly"]["date"], str(data.index[-6].date()))
        self.assertTrue(any("最新觀測" in note for note in result["notes"]))

    def test_rejects_missing_nan_duplicate_or_contradictory_prices(self):
        data = candles()
        invalid = [data.drop(columns="Volume"), pd.concat([data, data.tail(1)])]
        nan = data.copy()
        nan.loc[nan.index[-1], "Close"] = np.nan
        invalid.append(nan)
        bad_candle = data.copy()
        bad_candle.loc[bad_candle.index[-1], "High"] = 90
        invalid.append(bad_candle)
        for frame in invalid:
            with self.subTest(frame_shape=frame.shape):
                self.assertEqual(analyze_timeframes(frame, data.index[-1])["status"], "unavailable")

    def test_short_history_has_no_fabricated_moving_average(self):
        data = candles(10)
        report = analyze_timeframes(data, data.index[-1])
        self.assertIsNone(report["daily"]["ma20"])
        self.assertIsNone(report["daily"]["rsi14"])
        self.assertEqual(report["daily"]["status"], "insufficient_history")

    def test_datetime_timezone_uses_taiwan_trading_date(self):
        data = candles(10)
        data.index = data.index.tz_localize("Asia/Taipei").tz_convert("UTC")
        result = analyze_timeframes(data, "2025-01-17")
        self.assertEqual(result["data_date"], "2025-01-17")


class ResearchBacktestTests(unittest.TestCase):
    @patch("research_technical._indicators", side_effect=indicators_with_signals)
    def test_next_open_non_overlapping_costs_and_model_risk(self, _mock):
        data = candles(81)
        result = run_research_backtest(data, "ma_cross", data.index[-1])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["trades"]), 2)
        first, second = result["trades"]
        self.assertEqual(first["signal_date"], str(data.index[60].date()))
        self.assertEqual(first["entry_date"], str(data.index[61].date()))
        self.assertEqual(first["exit_date"], str(data.index[69].date()))
        self.assertEqual(first["holding_days"], 9)
        self.assertGreater(second["entry_date"], first["exit_date"])
        self.assertLess(first["net_profit"], 0)  # Unchanged price still incurs costs.
        self.assertLessEqual(first["planned_net_risk"], 5000)
        self.assertGreater(first["shares"], 0)
        self.assertEqual(result["diagnostics"]["skipped_overlap"], 1)

    @patch("research_technical._indicators", side_effect=indicators_with_signals)
    def test_same_day_both_levels_is_conservative_stop(self, _mock):
        data = candles(63)
        data.loc[data.index[61], ["High", "Low"]] = [105, 97]
        result = run_research_backtest(data, "ma_cross", data.index[-1])
        first = result["trades"][0]
        self.assertEqual(first["exit_reason"], "同日先算停損")
        self.assertAlmostEqual(first["exit_price"], 98 * 0.9995)
        self.assertLess(first["net_profit"], 0)

    @patch("research_technical._indicators", side_effect=indicators_with_signals)
    def test_unfinished_trade_is_excluded_and_occupies_remaining_window(self, _mock):
        data = candles(64)
        result = run_research_backtest(data, "ma_cross", data.index[-1])
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["diagnostics"]["incomplete"], 1)
        self.assertIsNone(result["metrics"]["overall"]["raw_win_rate_pct"])
        self.assertIsNone(result["metrics"]["overall"]["max_drawdown_closed_trade_pct"])

    @patch("research_technical._indicators", side_effect=indicators_with_signals)
    def test_observed_gap_loss_is_not_capped_at_risk_budget(self, _mock):
        data = candles(63)
        data.loc[data.index[62], ["Open", "High", "Low", "Close"]] = [90, 91, 89, 90]
        first = run_research_backtest(data, "ma_cross", data.index[-1])["trades"][0]
        self.assertEqual(first["exit_reason"], "跳空停損")
        self.assertEqual(first["exit_price"], 90)
        self.assertLess(first["net_profit"], -5000)

    @patch("research_technical._indicators", side_effect=indicators_with_signals)
    def test_future_data_cannot_change_completed_past_report(self, _mock):
        data = candles(90)
        cutoff = data.index[69]
        expected = run_research_backtest(data.iloc[:70], "ma_cross", cutoff)
        data.iloc[70:, :] = 999999
        actual = run_research_backtest(data, "ma_cross", cutoff)
        self.assertEqual(actual, expected)

    @patch("research_technical._indicators", side_effect=indicators_with_signals)
    def test_rsi_strategy_is_explicitly_rebound_not_divergence(self, _mock):
        data = candles(70)
        result = run_research_backtest(data, "rsi_rebound", data.index[-1])
        self.assertEqual(len(result["trades"]), 1)
        self.assertIn("不是背離", result["strategy_label"])

    def test_statistics_hide_small_sample_precision_and_define_profit_factor(self):
        trades = [{"net_profit": 100, "return_pct": 10}, {"net_profit": -50, "return_pct": -20}, {"net_profit": 20, "return_pct": 10}]
        result = _summary(trades)
        self.assertIsNone(result["win_rate_pct"])
        self.assertAlmostEqual(result["raw_win_rate_pct"], 200 / 3)
        self.assertEqual(result["profit_factor"], 2.4)
        self.assertAlmostEqual(result["max_drawdown_closed_trade_pct"], 20)
        self.assertLess(result["confidence_interval_pct"][0], result["raw_win_rate_pct"])
        self.assertGreater(result["confidence_interval_pct"][1], result["raw_win_rate_pct"])
        self.assertIsNotNone(_summary(trades * 10)["win_rate_pct"])
        self.assertIsNone(_summary([trades[0]])["profit_factor"])

    def test_invalid_parameters_and_sparse_history_are_unavailable(self):
        data = candles(90)
        for strategy, period in [("invented", 380), ("ma_cross", 0), ("ma_cross", True)]:
            self.assertEqual(run_research_backtest(data, strategy, data.index[-1], period)["status"], "unavailable")
        self.assertEqual(run_research_backtest(data.head(50), "existing", data.index[-1])["status"], "unavailable")

    @patch("analysis_core.calculate_historical_performance")
    def test_existing_strategy_uses_production_core_with_cutoff_no_current_fund(self, calculate):
        data = candles(90)
        calculate.return_value = {"trades": [{"signal_date": data.index[60], "entry_date": data.index[61], "holding_days": 2, "entry_price": 100, "exit_price": 101, "execution_exit_price": 101, "shares": 100, "return_pct": 0.5, "exit_reason": "停利"}], "diagnostics": {"completed": 1}, "backtest_scope": "test-only fixture"}
        result = run_research_backtest(data, "existing", data.index[69])
        self.assertEqual(result["status"], "ok")
        args, kwargs = calculate.call_args
        self.assertEqual(args[0].index[-1], data.index[69])
        self.assertNotIn("fund", kwargs)
        self.assertEqual(result["trades"][0]["exit_date"], str(data.index[62].date()))
        self.assertEqual(result["metrics"]["overall"]["samples"], 1)
        self.assertEqual(result["metrics"]["validation"]["samples"], 0)


if __name__ == "__main__":
    unittest.main()
