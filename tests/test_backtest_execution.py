import unittest
from unittest.mock import patch

import pandas as pd

from analysis_core import _trade_result, calculate_historical_performance


class BacktestExecutionTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
