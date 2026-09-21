import unittest
from unittest.mock import patch

import pandas as pd

from analysis_core import (
    BACKTEST_HOLD_DAYS,
    BACKTEST_MIN_GAP_DAYS,
    _bars_observable_after_fill,
    _trade_result,
    build_score_input,
    calculate_historical_performance,
)


class BacktestExecutionTests(unittest.TestCase):
    def _assert_diagnostics_conserved(self, result):
        diag = result["diagnostics"]
        self.assertEqual(result["backtest_schema"], "executable_v3")
        self.assertEqual(
            diag["window_bars"],
            diag["evaluated"] + diag["skipped_overlap"] + diag["skipped_no_next_session"],
        )
        self.assertEqual(diag["evaluated"], diag["signal_pass"] + diag["signal_rejected"])
        self.assertEqual(
            diag["signal_pass"],
            diag["filter_rejected"] + diag["plan_rejected"] + diag["plan_ready"],
        )
        self.assertEqual(diag["plan_ready"], diag["fill_rejected"] + diag["filled"])
        self.assertEqual(
            diag["filled"],
            diag["execution_rejected"] + diag["execution_unresolved"]
            + diag["incomplete"] + diag["completed"],
        )
        self.assertEqual(diag["completed"], diag["training"] + diag["validation"])
        self.assertEqual(diag["completed"], result["overall_samples"])
        self.assertEqual(diag["training"], result["training_samples"])
        self.assertEqual(diag["validation"], result["validation_samples"])
        self.assertEqual(diag["execution_unresolved"], result["execution_unresolved"])
        for stage in ("filter", "plan", "fill", "execution"):
            self.assertEqual(
                diag[f"{stage}_rejected"], sum(diag[f"{stage}_rejected_reasons"].values())
            )
        self.assertEqual(diag["plan_rejected"], sum(diag["plan_rejected_statuses"].values()))

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

    def test_exit_slippage_applies_to_stop_but_not_target(self):
        target_bar = pd.DataFrame([
            {"Open": 100, "High": 111, "Low": 99, "Close": 108}
        ])
        stopped_bar = pd.DataFrame([
            {"Open": 100, "High": 101, "Low": 89, "Close": 92}
        ])
        kwargs = {
            "target_price": 110,
            "stop_price": 90,
            "entry_price": 100,
            "fee_rate": 0,
            "sell_tax_rate": 0,
            "minimum_commission": 0,
            "exit_slippage_rate": 0.01,
        }

        target = _trade_result(target_bar, **kwargs)
        stopped = _trade_result(stopped_bar, **kwargs)

        self.assertEqual(target["execution_exit_price"], 110)
        self.assertEqual(stopped["execution_exit_price"], 89.1)

    def test_pullback_target_order_ambiguity_is_explicitly_excluded(self):
        ambiguous = pd.DataFrame([
            {"Open": 110, "High": 113, "Low": 100, "Close": 105}
        ])
        stopped = ambiguous.copy()
        stopped.loc[stopped.index[0], "Low"] = 94
        close_confirmed = ambiguous.copy()
        close_confirmed.loc[close_confirmed.index[0], "Close"] = 112

        self.assertIsNone(_bars_observable_after_fill(
            ambiguous,
            102,
            "PULLBACK_TOUCH",
            target_price=112,
            stop_price=95,
        ))
        self.assertIsNotNone(_bars_observable_after_fill(
            stopped,
            102,
            "PULLBACK_TOUCH",
            target_price=112,
            stop_price=95,
        ))
        observable = _bars_observable_after_fill(
            close_confirmed,
            102,
            "PULLBACK_TOUCH",
            target_price=112,
            stop_price=95,
        )
        self.assertIsNotNone(observable)
        result = _trade_result(
            observable,
            target_price=112,
            stop_price=95,
            entry_price=102,
            fee_rate=0,
            sell_tax_rate=0,
            minimum_commission=0,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["exit_reason"], "停利")
        self.assertTrue(result["win"])

    def test_performance_counts_ambiguous_pullback_as_excluded_not_trade(self):
        bars = self._flat_bars(30)
        bars.iloc[21] = {
            "Open": 110,
            "High": 113,
            "Low": 100,
            "Close": 105,
            "ATR": 1,
        }

        def one_signal(history, _fund, **_kwargs):
            return len(history) == 21, 80, self._executable_signal(history)

        plan = {
            "Entry_Status": "現在可執行",
            "Entry_Low": 100,
            "Entry_High": 102,
            "Entry_Stop": 95,
            "Entry_Target": 112,
        }
        with (
            patch("analysis_core.is_strategy_signal", side_effect=one_signal),
            patch("entry_readiness.build_entry_readiness", return_value=plan),
        ):
            result = calculate_historical_performance(
                bars,
                lookback_days=len(bars),
                hold_days=3,
                min_gap_days=1,
                fee_rate=0,
                sell_tax_rate=0,
                minimum_commission=0,
                slippage_rate=0,
            )

        self.assertEqual(result["closed_signals"], 0)
        self.assertEqual(result["execution_unresolved"], 1)
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["diagnostics"]["incomplete"], 0)
        self.assertEqual(result["diagnostics"]["execution_rejected"], 0)
        self._assert_diagnostics_conserved(result)

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
        self.assertEqual(result["diagnostics"]["status"], "invalid_parameters")
        self._assert_diagnostics_conserved(result)
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
        self.assertEqual(result["diagnostics"]["incomplete"], 1)
        self.assertEqual(result["diagnostics"]["execution_unresolved"], 0)
        self._assert_diagnostics_conserved(result)

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
        self._assert_diagnostics_conserved(result)

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
                later - earlier == BACKTEST_HOLD_DAYS
                for earlier, later in zip(entry_positions, entry_positions[1:])
            )
        )
        self.assertGreater(result["diagnostics"]["skipped_overlap"], 0)
        self._assert_diagnostics_conserved(result)

    def test_default_backtest_risk_sizes_each_trade_to_five_thousand(self):
        bars = self._flat_bars(40)

        def always_signal(history, _fund, **_kwargs):
            return True, 80, self._executable_signal(history)

        with patch("analysis_core.is_strategy_signal", side_effect=always_signal):
            result = calculate_historical_performance(
                bars,
                lookback_days=len(bars),
                hold_days=3,
                min_gap_days=3,
                target_mult=3.0,
            )

        self.assertTrue(result["trades"])
        self.assertTrue(all(trade["shares"] != 1000 for trade in result["trades"]))
        self.assertTrue(
            all(trade["planned_net_risk"] <= 5000 for trade in result["trades"])
        )

    def test_training_and_validation_metrics_are_disjoint(self):
        bars = self._flat_bars(30)

        def four_signals(history, _fund, **_kwargs):
            return (
                len(history) in {21, 23, 25, 27},
                80,
                self._executable_signal(history),
            )

        simulated = [
            {"win": True, "return_pct": 2.0, "exit_reason": "停利"},
            {"win": True, "return_pct": 2.0, "exit_reason": "停利"},
            {"win": False, "return_pct": -1.0, "exit_reason": "停損"},
            {"win": False, "return_pct": -1.0, "exit_reason": "停損"},
        ]
        with (
            patch("analysis_core.is_strategy_signal", side_effect=four_signals),
            patch("analysis_core._trade_result", side_effect=simulated),
        ):
            result = calculate_historical_performance(
                bars,
                lookback_days=len(bars),
                hold_days=1,
                min_gap_days=1,
                fee_rate=0,
                sell_tax_rate=0,
                minimum_commission=0,
                slippage_rate=0,
            )

        self.assertEqual(result["overall_samples"], 4)
        self.assertEqual(result["closed_signals"], 2)
        self.assertEqual(result["validation_samples"], 2)
        self.assertEqual(result["training_raw_win_rate"], 100.0)
        self.assertEqual(result["validation_raw_win_rate"], 0.0)
        self._assert_diagnostics_conserved(result)

    def test_diagnostics_distinguish_signal_plan_fill_and_execution_rejections(self):
        bars = self._flat_bars(31)
        # The candidate following history length 26 gaps beneath support.
        bars.iloc[26] = {"Open": 98, "High": 100, "Low": 97, "Close": 99, "ATR": 1}

        def varied_signals(history, _fund, **_kwargs):
            data = self._executable_signal(history)
            data["audit_case"] = len(history)
            if len(history) == 22:
                data["Confidence"] = 50
            return len(history) in range(22, 30), 80, data

        def varied_plans(data, **_kwargs):
            case = data["audit_case"]
            plan = {
                "Entry_Status": "現在可執行", "Entry_Low": 99.9,
                "Entry_High": 100.4, "Entry_Stop": 98.9, "Entry_Target": 102.1,
            }
            if case == 23:
                return {"Entry_Status": "等待拉回", "Entry_Reason": "價格超出區間"}
            if case == 24:
                raise ValueError("invalid technical input")
            if case == 25:
                plan["Entry_Low"] = None
            if case == 27:
                plan["Entry_Target"] = 100.1
            return plan

        with (
            patch("analysis_core.is_strategy_signal", side_effect=varied_signals),
            patch("entry_readiness.build_entry_readiness", side_effect=varied_plans),
            patch("analysis_core._trade_result", side_effect=[
                None,
                {"win": False, "return_pct": 0, "exit_reason": "到期出場"},
            ]),
        ):
            result = calculate_historical_performance(
                bars, lookback_days=len(bars), min_gap_days=0,
                filter_low_conf=True, fee_rate=0, sell_tax_rate=0,
                minimum_commission=0, slippage_rate=0,
            )

        diag = result["diagnostics"]
        self.assertEqual(diag["signal_pass"], 8)
        self.assertEqual(diag["filter_rejected_reasons"], {"low_confidence": 1})
        self.assertEqual(diag["plan_rejected_reasons"], {
            "價格超出區間": 1, "invalid_plan_input": 1,
        })
        self.assertEqual(diag["fill_rejected_reasons"], {
            "invalid_plan_levels": 1, "gap_below_zone": 1,
        })
        self.assertEqual(diag["execution_rejected_reasons"], {
            "gross_reward_risk": 1, "invalid_exit_data": 1,
        })
        self.assertEqual(diag["incomplete"], 1)
        self.assertEqual(diag["execution_unresolved"], 0)
        self._assert_diagnostics_conserved(result)

    def test_insufficient_history_has_zero_sample_diagnostics(self):
        result = calculate_historical_performance(self._flat_bars(20))
        self.assertEqual(result["diagnostics"]["status"], "insufficient_history")
        self.assertEqual(result["diagnostics"]["history_bars"], 20)
        self._assert_diagnostics_conserved(result)

    def test_historical_daily_gain_uses_previous_close_and_blocks_overheat(self):
        bars = self._flat_bars(30)
        values = {
            "Volume": 1000, "5MA": 100, "20MA": 107, "60MA": 90,
            "BB_UP": 125, "BB_DN": 90, "MACD_Hist": 1,
            "J": 50, "RSI": 55, "ADX": 30,
        }
        for column, value in values.items():
            bars[column] = float(value)
        bars.loc[bars.index[-1], ["Close", "High", "Volume", "ATR"]] = [107, 108, 1300, 4]
        bars.loc[bars.index[-2], "MACD_Hist"] = 0.9
        with patch("analysis_core.advanced_patterns.detect_pattern", return_value={}):
            signal_input = build_score_input(bars)
        self.assertEqual(signal_input["漲跌幅"], 7.0)

        from entry_readiness import build_entry_readiness

        plan = build_entry_readiness(dict(signal_input, Score=80))
        self.assertFalse(plan["Entry_Ready"])
        self.assertIn("單日上漲 7.0%", plan["Entry_Reason"])


if __name__ == "__main__":
    unittest.main()
