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

    @staticmethod
    def _executable_signal(history):
        latest = history.iloc[-1]
        return {
            "收盤價": float(latest["Close"]),
            "最高價": float(latest["High"]),
            "最低價": float(latest["Low"]),
            "20MA": 99.9,
            "ATR": 1.0,
            "BB_UP": 110.0,
            "RSI": 55.0,
            "BIAS": 1.0,
            "漲跌幅": 0.0,
            "Entry_Pattern": "一般觀察型",
            "Signal_Conflict": "低",
            "Est_Vol_Ratio": 1.2,
            "Volume_Confirmed": True,
            "Confidence": 100,
        }

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

    def test_nonfinite_trade_prices_and_parameters_are_rejected(self):
        bars = pd.DataFrame([{"Open": 100, "High": 105, "Low": 95, "Close": 101}])
        valid = {"target_price": 110, "stop_price": 90, "entry_price": 100}

        self.assertIsNone(_trade_result(bars, **{**valid, "target_price": float("nan")}))
        self.assertIsNone(_trade_result(bars, **valid, exit_slippage_rate=float("inf")))
        self.assertIsNone(_trade_result(bars, **valid, shares=1.5))

    def test_trade_prices_must_be_strictly_ordered_and_positive(self):
        bars = pd.DataFrame([{"Open": 100, "High": 105, "Low": 95, "Close": 101}])

        invalid_levels = (
            {"stop_price": 0, "entry_price": 100, "target_price": 110},
            {"stop_price": 100, "entry_price": 100, "target_price": 110},
            {"stop_price": 90, "entry_price": 100, "target_price": 100},
            {"stop_price": 105, "entry_price": 100, "target_price": 110},
        )
        for levels in invalid_levels:
            with self.subTest(levels=levels):
                self.assertIsNone(_trade_result(bars, **levels))

    def test_nonfinite_backtest_configuration_is_rejected_before_scoring(self):
        bars = self._flat_bars()
        with patch("analysis_core.is_strategy_signal") as signal:
            result = calculate_historical_performance(bars, target_mult=float("nan"))

        self.assertEqual(result["closed_signals"], 0)
        self.assertEqual(result["trades"], [])
        signal.assert_not_called()

    def test_current_fundamental_snapshot_is_not_reused_in_history(self):
        bars = pd.DataFrame({"Close": range(100, 121)})
        with patch("analysis_core.is_strategy_signal", return_value=(False, 0, {})) as signal:
            result = calculate_historical_performance(bars, fund={"EPS": 99})
        self.assertEqual(result["backtest_scope"].startswith("純技術面"), True)
        self.assertTrue(all(call.args[1] == {} for call in signal.call_args_list))

    def test_incomplete_tail_window_without_exit_is_censored(self):
        bars = self._flat_bars()

        def signal_only_near_tail(history, _fund, **_kwargs):
            return len(history) == 23, 80, self._executable_signal(history)

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
        bars.iloc[23, bars.columns.get_loc("High")] = 103.0
        bars.iloc[23, bars.columns.get_loc("Close")] = 102.5

        def signal_only_near_tail(history, _fund, **_kwargs):
            return len(history) == 23, 80, self._executable_signal(history)

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

        def always_signal(history, _fund, **_kwargs):
            return True, 80, self._executable_signal(history)

        with patch("analysis_core.is_strategy_signal", side_effect=always_signal):
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
