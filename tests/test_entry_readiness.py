import unittest

from entry_readiness import (
    LEGACY_STATUS,
    READY_STATUS,
    WAIT_PULLBACK_STATUS,
    WAIT_TRIGGER_STATUS,
    WAIT_VOLUME_STATUS,
    build_entry_readiness,
    build_entry_summary,
    ensure_entry_readiness,
)


class EntryReadinessTests(unittest.TestCase):
    def _base(self, **updates):
        record = {
            "Score": 82,
            "收盤價": 100.5,
            "最高價": 103,
            "漲跌幅": 1.0,
            "20MA": 100,
            "ATR": 4,
            "BB_UP": 112,
            "RSI": 55,
            "BIAS": 1,
            "Confidence": 88,
            "Signal_Conflict": "低",
            "Entry_Pattern": "一般觀察型",
            "Volume_Confirmed": True,
            "Est_Vol_Ratio": 1.2,
        }
        record.update(updates)
        return record

    def test_limit_up_high_score_is_never_ready(self):
        result = build_entry_readiness(self._base(Score=99, 漲跌幅=10, 收盤價=110, 最高價=110))
        self.assertEqual(result["Entry_Status"], WAIT_PULLBACK_STATUS)
        self.assertFalse(result["Entry_Ready"])

    def test_pullback_inside_zone_can_be_ready(self):
        result = build_entry_readiness(self._base())
        self.assertEqual(result["Entry_Status"], READY_STATUS)
        self.assertEqual(result["Entry_Status_Group"], "ready")
        self.assertEqual(result["Entry_Low"], 100)
        self.assertEqual(result["Entry_High"], 100.5)
        self.assertGreaterEqual(result["Entry_Net_RRR"], 1.3)
        self.assertGreater(result["Entry_Target"], result["Entry_High"])
        self.assertEqual(result["Entry_Reason"], "進入20MA回測區｜量比1.20×已確認")

    def test_entry_summary_uses_real_evidence_and_prioritizes_risk(self):
        institutional = build_entry_summary(self._base(Whale_Net=1234, Whale_Net_Days=3))
        conflicted = build_entry_summary(self._base(Whale_Net=1234, Signal_Conflict="中"))
        bearish = build_entry_summary(self._base(Whale_Net=1234, Market_Regime="空頭"))
        self.assertEqual(institutional, "進入20MA回測區｜法人3日買超1,234張")
        self.assertEqual(conflicted, "進入20MA回測區｜訊號分歧，嚴守停損")
        self.assertEqual(bearish, "進入20MA回測區｜大盤在月季線下，降低部位")

    def test_price_inside_zone_with_weak_volume_waits_for_confirmation(self):
        result = build_entry_readiness(self._base(Est_Vol_Ratio=0.96))
        self.assertEqual(result["Entry_Status"], WAIT_VOLUME_STATUS)
        self.assertEqual(result["Entry_Status_Group"], "wait")
        self.assertIn("0.96", result["Entry_Reason"])

    def test_execution_requires_minimum_backtest_and_validation_evidence(self):
        result = build_entry_readiness(self._base(
            WinRate=55,
            Backtest_Samples=12,
            Validation_WinRate=50,
            Validation_Samples=5,
        ))
        self.assertEqual(result["Entry_Status"], WAIT_TRIGGER_STATUS)
        self.assertIn("15", result["Entry_Reason"])

    def test_execution_requires_cost_adjusted_break_even_rate(self):
        result = build_entry_readiness(self._base(
            WinRate=45,
            Backtest_Samples=30,
            Validation_WinRate=45,
            Validation_Samples=10,
        ))
        self.assertEqual(result["Entry_Status"], WAIT_TRIGGER_STATUS)
        self.assertIn("損益兩平", result["Entry_Reason"])

    def test_nonpositive_expectancy_or_weak_wilson_bound_vetoes_entry(self):
        cases = (
            ({"Backtest_Net_Expectancy": -0.1}, "期望值"),
            ({"Validation_Net_Expectancy": 0}, "期望值"),
            ({"Validation_Wilson_Low": 29.9}, "勝率下限"),
        )
        for updates, expected in cases:
            with self.subTest(expected=expected):
                result = build_entry_readiness(self._base(
                    WinRate=55,
                    Backtest_Samples=30,
                    Validation_WinRate=55,
                    Validation_Samples=10,
                    **updates,
                ))
                self.assertEqual(result["Entry_Status"], WAIT_TRIGGER_STATUS)
                self.assertIn(expected, result["Entry_Reason"])

    def test_explicit_critical_data_failure_never_becomes_ready(self):
        for updates in (
            {"Critical_Data_Ready": False},
            {"Critical_Data_Ready": True, "Critical_Data_Issues": ["institutional_stale"]},
        ):
            with self.subTest(updates=updates):
                result = build_entry_readiness(self._base(**updates))
                self.assertEqual(result["Entry_Status"], WAIT_TRIGGER_STATUS)
                self.assertIn("等待資料確認", result["Entry_Reason"])

    def test_bearish_market_financial_loss_and_chip_selloff_each_veto_entry(self):
        cases = (
            (self._base(Market_Regime="空頭"), "大盤"),
            (self._base(
                Financial_Operating_Income=-10,
                Financial_Net_Income=-8,
            ), "損益"),
            (self._base(
                Institutional_Sell_Streak=3,
                Whale_Net=-800,
            ), "連續賣超"),
        )
        for record, expected in cases:
            with self.subTest(expected=expected):
                result = build_entry_readiness(record)
                self.assertEqual(result["Entry_Status"], WAIT_TRIGGER_STATUS)
                self.assertIn(expected, result["Entry_Reason"])

    def test_price_above_cost_adjusted_zone_waits_for_pullback(self):
        result = build_entry_readiness(self._base(收盤價=102))
        self.assertEqual(result["Entry_Status"], WAIT_PULLBACK_STATUS)
        self.assertLess(result["Entry_High"], 102)
        self.assertIn("不追", result["Entry_Reason"])

    def test_gross_ratio_cannot_hide_cost_adjusted_ratio_below_minimum(self):
        baseline = {
            "Entry_Plan_Type": "pullback",
            "Entry_Low": 10,
            "Entry_High": 10,
            "Entry_Stop": 9.9,
            "Entry_Target": 10.15,
            "Entry_RRR": 1.5,
            "No_Chase_Price": 10.2,
        }
        result = build_entry_readiness(
            self._base(收盤價=10, 最高價=10, BB_UP=12),
            intraday=True,
            baseline_plan=baseline,
        )
        self.assertEqual(result["Entry_Status"], WAIT_PULLBACK_STATUS)
        self.assertIn("成本後風險報酬比", result["Entry_Reason"])

    def test_general_observation_score_cannot_be_execution_ready(self):
        result = build_entry_readiness(self._base(Score=64))
        self.assertEqual(result["Entry_Status"], "條件不足")
        self.assertFalse(result["Entry_Ready"])
        self.assertIn("65", result["Entry_Reason"])

    def test_breakout_waits_postclose_then_live_price_can_activate_plan(self):
        postclose = build_entry_readiness(self._base(最高價=101, Entry_Pattern="趨勢突破型"))
        self.assertEqual(postclose["Entry_Status"], WAIT_TRIGGER_STATUS)
        live = build_entry_readiness(
            self._base(
                收盤價=postclose["Entry_Low"],
                最高價=postclose["Entry_Low"],
                Entry_Pattern="趨勢突破型",
            ),
            intraday=True,
            baseline_plan=postclose,
        )
        self.assertEqual(live["Entry_Status"], READY_STATUS)

    def test_price_above_saved_no_chase_price_waits_for_pullback(self):
        postclose = build_entry_readiness(self._base(最高價=101, Entry_Pattern="趨勢突破型"))
        live = build_entry_readiness(
            self._base(收盤價=106, 最高價=106, Entry_Pattern="趨勢突破型"),
            intraday=True,
            baseline_plan=postclose,
        )
        self.assertEqual(live["Entry_Status"], WAIT_PULLBACK_STATUS)

    def test_legacy_limit_up_is_flagged_without_inventing_prices(self):
        result = ensure_entry_readiness({"Score": 99, "漲跌幅": 10, "收盤價": 50})
        self.assertEqual(result["Entry_Status"], WAIT_PULLBACK_STATUS)
        self.assertIsNone(result["Entry_Low"])
        self.assertIsNone(result["Entry_Target"])

    def test_missing_technical_inputs_do_not_create_levels(self):
        result = ensure_entry_readiness({"Score": 80, "漲跌幅": 1, "收盤價": 50})
        self.assertEqual(result["Entry_Status"], LEGACY_STATUS)
        self.assertIsNone(result["Entry_Low"])
        self.assertIn("缺少", result["Entry_Reason"])


if __name__ == "__main__":
    unittest.main()
