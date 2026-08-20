import unittest

from strategy_advice import build_strategy_text


class StrategyAdviceTests(unittest.TestCase):
    def _base(self, **overrides):
        data = {
            "Score": 76,
            "收盤價": 110,
            "20MA": 100,
            "ATR_Target": 116,
            "ATR_Stop": 104,
            "RSI": 62,
            "Est_Vol_Ratio": 1.8,
            "Volume_Confirmed": True,
            "MACD柱": 0.5,
            "前日MACD柱": 0.2,
            "Entry_Pattern": "趨勢突破型",
            "Signal_Conflict": "低",
            "Institutional_Status": "ok",
            "Whale_Net": -500,
            "Whale_Net_Days": 3,
            "Revenue_Status": "ok",
            "YoY": 20,
        }
        data.update(overrides)
        return data

    def test_breakout_includes_evidence_levels_and_external_context(self):
        text = build_strategy_text(self._base())
        self.assertIn("趨勢突破", text)
        self.assertIn("量比 1.80×", text)
        self.assertIn("MACD 動能增強", text)
        self.assertIn("20MA 100.00", text)
        self.assertIn("ATR 目標 116.00", text)
        self.assertIn("ATR 防守價 104.00", text)
        self.assertIn("法人近 3 日合計賣超 500 張", text)
        self.assertIn("最新營收年增 +20.00%", text)

    def test_high_conflict_overrides_bullish_score(self):
        text = build_strategy_text(self._base(Signal_Conflict="高"))
        self.assertIn("多空訊號衝突高", text)
        self.assertNotIn("趨勢突破：", text)

    def test_overheated_setup_warns_against_chasing(self):
        text = build_strategy_text(self._base(Entry_Pattern="過熱追高型", RSI=79))
        self.assertIn("過熱勿追", text)
        self.assertIn("RSI 79.0", text)

    def test_generic_strong_setup_reports_distance_from_ma20(self):
        text = build_strategy_text(self._base(
            Entry_Pattern="一般觀察型",
            Institutional_Status="missing",
            Revenue_Status="missing",
        ))
        self.assertIn("收盤高於 20MA 10.00%", text)
        self.assertNotIn("佐證／風險", text)

    def test_missing_required_data_does_not_invent_levels(self):
        text = build_strategy_text({"Score": 80, "收盤價": None})
        self.assertEqual(text, "必要行情或分數不足，本次不提供進出場建議。")


if __name__ == "__main__":
    unittest.main()
