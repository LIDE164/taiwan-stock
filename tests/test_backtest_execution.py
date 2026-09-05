import unittest
from unittest.mock import patch

import pandas as pd

from analysis_core import (
    BACKTEST_HOLD_DAYS,
    BACKTEST_MIN_GAP_DAYS,
    _trade_result,
    calculate_historical_performance,
)


class BacktestExecutionTests(unittest.TestCase):
    @staticmethod
    def _flat_bars(count=25):
        return pd.DataFrame(
            {
                "Open": [100.0] * count,
                "High": [100.5] * count,
                "Low": [99.5] * count,
                "Close": [100.0] * count,
                "ATR": [1.0] * count,
            },
            index=pd.date_range("2026-01-01", periods=count, freq="B"),
        )

    def test_trailing_stop_raised_by_high_applies_from_next_bar(self):
        bars = pd.DataFrame([
            {"Open": 100, "High": 110, "Low": 99, "Close": 108},
        ])
        result = _trade_result(
            bars,
            target_price=120,
            stop_price=95,
            entry_price=100,
            fee_rate=0,
            sell_tax_rate=0,
            minimum_commission=0,
            enable_trailing=True,
        )
        self.assertEqual(result["exit_price"], 108)
        self.assertEqual(result["exit_reason"], "到期出場")

    def test_raised_trailing_stop_can_trigger_on_following_bar(self):
        bars = pd.DataFrame([
            {"Open": 100, "High": 110, "Low": 99, "Close": 108},
            {"Open": 108, "High": 109, "Low": 104, "Close": 106},
        ])
        result = _trade_result(
            bars,
            target_price=120,
            stop_price=95,
            entry_price=100,
            fee_rate=0,
            sell_tax_rate=0,
            minimum_commission=0,
            enable_trailing=True,
        )
        self.assertEqual(result["exit_price"], 105)
        self.assertEqual(result["exit_reason"], "停損")

    def test_taiwan_costs_can_turn_small_gross_gain_into_net_loss(self):
        bars = pd.DataFrame([{"Open": 100, "High": 101, "Low": 100, "Close": 100.4}])
        result = _trade_result(
            bars,
            target_price=120,
            stop_price=90,
            entry_price=100,
            buy_fee_rate=0.001425,
            sell_fee_rate=0.001425,
            sell_tax_rate=0.003,
            minimum_commission=20,
            shares=1000,
        )
        self.assertFalse(result["win"])
        self.assertGreater(result["transaction_cost"], 500)

    def test_missing_open_does_not_create_a_synthetic_exit_price(self):
        bars = pd.DataFrame([{"Open": None, "High": 101, "Low": 99, "Close": 100}])
        result = _trade_result(
            bars,
            target_price=110,
            stop_price=90,
            entry_price=100,
            fee_rate=0,
            minimum_commission=0,
        )
        self.assertIsNone(result)

    def test_inconsistent_ohlc_invalidates_the_trade(self):
        bars = pd.DataFrame([{"Open": 100, "High": 99, "Low": 98, "Close": 101}])
        result = _trade_result(
            bars,
            target_price=110,
            stop_price=90,
            entry_price=100,
            fee_rate=0,
            minimum_commission=0,
        )
        self.assertIsNone(result)

    def test_current_fundamental_snapshot_is_not_reused_in_history(self):
        bars = pd.DataFrame({"Close": range(100, 121)})
        with patch("analysis_core.is_strategy_signal", return_value=(False, 0, {})) as signal:
            result = calculate_historical_performance(bars, fund={"EPS": 99})
        self.assertEqual(result["backtest_scope"].startswith("純技術面"), True)
        self.assertTrue(all(call.args[1] == {} for call in signal.call_args_list))

    def test_incomplete_tail_window_without_exit_is_censored(self):
        bars = self._flat_bars()

        def signal_only_near_tail(history, _fund, **_kwargs):
            return len(history) == 23, 80, {}

        with patch("analysis_core.is_strategy_signal", side_effect=signal_only_near_tail):
            result = calculate_historical_performance(
                bars,
                hold_days=5,
                min_gap_days=5,
                lookback_days=len(bars),
                fee_rate=0,
                sell_tax_rate=0,
                minimum_commission=0,
                slippage_rate=0,
            )

        self.assertEqual(result["closed_signals"], 0)
        self.assertEqual(result["trades"], [])

    def test_incomplete_tail_window_with_triggered_exit_is_retained(self):
        bars = self._flat_bars()
        bars.iloc[23, bars.columns.get_loc("High")] = 102.0
        bars.iloc[23, bars.columns.get_loc("Close")] = 101.5

        def signal_only_near_tail(history, _fund, **_kwargs):
            return len(history) == 23, 80, {}

        with patch("analysis_core.is_strategy_signal", side_effect=signal_only_near_tail):
            result = calculate_historical_performance(
                bars,
                hold_days=5,
                min_gap_days=5,
                lookback_days=len(bars),
                fee_rate=0,
                sell_tax_rate=0,
                minimum_commission=0,
                slippage_rate=0,
            )

        self.assertEqual(result["closed_signals"], 1)
        self.assertEqual(result["trades"][0]["exit_reason"], "停利")
        self.assertEqual(result["trades"][0]["holding_days"], 1)

    def test_default_signal_gap_is_at_least_the_holding_window(self):
        self.assertGreaterEqual(BACKTEST_MIN_GAP_DAYS, BACKTEST_HOLD_DAYS)
        bars = self._flat_bars(50)
        bars["ATR"] = 100.0

        with patch("analysis_core.is_strategy_signal", return_value=(True, 80, {})):
            result = calculate_historical_performance(
                bars,
                lookback_days=len(bars),
                fee_rate=0,
                sell_tax_rate=0,
                minimum_commission=0,
                slippage_rate=0,
            )

        entry_positions = [bars.index.get_loc(date) for date in result["buy_dates"]]
        self.assertGreater(len(entry_positions), 1)
        self.assertTrue(
            all(
                later - earlier >= BACKTEST_HOLD_DAYS
                for earlier, later in zip(entry_positions, entry_positions[1:])
            )
        )


if __name__ == "__main__":
    unittest.main()
