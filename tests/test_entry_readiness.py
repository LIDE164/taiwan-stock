import unittest

from entry_readiness import (
    LEGACY_STATUS,
    READY_STATUS,
    WAIT_PULLBACK_STATUS,
    WAIT_TRIGGER_STATUS,
    build_entry_readiness,
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
