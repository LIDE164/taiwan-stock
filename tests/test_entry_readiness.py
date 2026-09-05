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
            "收盤價": 101,
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
        self.assertEqual(result["Entry_High"], 102)
        self.assertGreater(result["Entry_Target"], result["Entry_High"])
        self.assertEqual(result["Entry_Reason"], "進入20MA回測區｜量比1.20×已確認")

    def test_entry_summary_uses_real_evidence_and_prioritizes_risk(self):
        institutional = build_entry_summary(self._base(Whale_Net=1234, Whale_Net_Days=3))
        conflicted = build_entry_summary(self._base(Whale_Net=1234, Signal_Conflict="中"))
        self.assertEqual(institutional, "進入20MA回測區｜法人3日買超1,234張")
        self.assertEqual(conflicted, "進入20MA回測區｜訊號分歧，嚴守停損")

    def test_price_inside_zone_with_weak_volume_waits_for_confirmation(self):
        result = build_entry_readiness(self._base(Est_Vol_Ratio=0.96))
        self.assertEqual(result["Entry_Status"], WAIT_VOLUME_STATUS)
        self.assertEqual(result["Entry_Status_Group"], "wait")
        self.assertIn("0.96", result["Entry_Reason"])

    def test_general_observation_score_cannot_be_execution_ready(self):
        result = build_entry_readiness(self._base(Score=64))
        self.assertEqual(result["Entry_Status"], "條件不足")
        self.assertFalse(result["Entry_Ready"])
        self.assertIn("65", result["Entry_Reason"])

    def test_breakout_waits_postclose_then_live_price_can_activate_plan(self):
        postclose = build_entry_readiness(self._base(最高價=101, Entry_Pattern="趨勢突破型"))
        self.assertEqual(postclose["Entry_Status"], WAIT_TRIGGER_STATUS)
        live = build_entry_readiness(
            self._base(收盤價=102, 最高價=102, Entry_Pattern="趨勢突破型"),
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
