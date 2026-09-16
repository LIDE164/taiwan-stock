import unittest

from scoring import (
    _calibrate_score,
    _combine_correlated_scores,
    get_decision_score,
)


class ScoringCalibrationTests(unittest.TestCase):
    @staticmethod
    def _base(**overrides):
        data = {
            "收盤價": 100,
            "5MA": 101,
            "20MA": 98,
            "BB_DN": 90,
            "BB_UP": 120,
            "成交量": 1_000,
            "5日均量": 1_000,
            "MACD柱": 0,
            "前日MACD柱": 1,
            "J值": 50,
            "RSI": 40,
            "Momentum_Score": 50,
            "Confidence": 100,
        }
        data.update(overrides)
        return data

    def test_weak_moderate_and_extreme_cases_remain_ordered_without_easy_99(self):
        weak, *_ = get_decision_score(self._base(), {}, with_reason=False)
        moderate, *_ = get_decision_score(
            self._base(
                訊號=True,
                ADX=27,
                ROC_20=8,
                收盤價=102,
                **{
                    "5MA": 101,
                    "5MA已上彎": True,
                    "MACD柱": 2,
                    "前日MACD柱": 1,
                    "RSI": 55,
                    "成交量": 1_500,
                    "Est_Vol_Ratio": 1.5,
                },
            ),
            {},
            with_reason=False,
        )
        extreme, *_ = get_decision_score(
            self._base(
                訊號=True,
                ADX=35,
                ROC_20=20,
                收盤價=100,
                MoM=5,
                YoY=20,
                Whale_Net=4_000,
                Entry_Pattern="趨勢突破型",
                Box_Breakout=True,
                紅吞=True,
                回測有撐=True,
                **{
                    "5MA": 98,
                    "20MA": 95,
                    "5MA已上彎": True,
                    "MACD柱": 2,
                    "前日MACD柱": 1,
                    "RSI": 55,
                    "Momentum_Score": 90,
                    "成交量": 2_000,
                    "Est_Vol_Ratio": 2,
                },
            ),
            {
                "EPS": 5,
                "TWII_Close": 25_000,
                "TWII_MA20": 24_000,
                "TWII_MA60": 23_000,
            },
            with_reason=False,
        )

        self.assertLess(weak, moderate)
        self.assertLess(moderate, extreme)
        self.assertLess(extreme, 99)
        self.assertTrue(all(5 <= score <= 99 for score in (weak, moderate, extreme)))

    def test_related_overheat_signals_receive_diminishing_penalties(self):
        single, *_ = get_decision_score(
            self._base(Entry_Pattern="過熱追高型", MACD柱=1, 前日MACD柱=0),
            {},
            with_reason=False,
        )
        stacked, *_ = get_decision_score(
            self._base(
                收盤價=110,
                BIAS=10,
                Entry_Pattern="過熱追高型",
                J值=85,
                RSI=80,
                Est_Vol_Ratio=4,
                BB_UP=110,
                MACD柱=1,
                前日MACD柱=0,
            ),
            {},
            with_reason=False,
        )

        self.assertLess(stacked, single)
        self.assertLessEqual(single - stacked, 8)
        self.assertGreater(stacked, 5)

    def test_correlated_combiner_preserves_strongest_and_discounts_confirmations(self):
        self.assertEqual(_combine_correlated_scores([-4]), -4)
        combined = _combine_correlated_scores([-4, -3, -2, -2], limit=7)
        self.assertGreater(combined, sum([-4, -3, -2, -2]))
        self.assertLess(combined, -4)

    def test_calibration_retains_declared_bounds(self):
        self.assertEqual(_calibrate_score(-10_000), 5)
        self.assertEqual(_calibrate_score(10_000), 99)

    def test_reported_revenue_decline_reduces_rank_but_missing_data_is_neutral(self):
        missing_score, *_ = get_decision_score(
            self._base(), {"MoM": None, "YoY": None}, with_reason=False
        )
        growth_score, *_ = get_decision_score(
            self._base(), {"MoM": 4.0, "YoY": 8.0}, with_reason=False
        )
        decline_score, _, reasons, _ = get_decision_score(
            self._base(), {"MoM": -4.0, "YoY": -8.0}, with_reason=True
        )

        self.assertLess(decline_score, missing_score)
        self.assertLess(missing_score, growth_score)
        self.assertTrue(any("月營收雙減" in reason and "-3分" in reason for reason in reasons))

    def test_mixed_revenue_direction_keeps_both_positive_and_negative_evidence(self):
        _, _, reasons, _ = get_decision_score(
            self._base(), {"MoM": -3.0, "YoY": 20.0}, with_reason=True
        )

        self.assertTrue(any("月營收年增" in reason and "+2分" in reason for reason in reasons))
        self.assertTrue(any("月營收月減" in reason and "-1分" in reason for reason in reasons))

    def test_negative_eps_is_penalized_even_during_a_strong_technical_trend(self):
        trend = self._base(ADX=35, ROC_20=12, 訊號=True)
        missing_score, *_ = get_decision_score(trend, {"EPS": None}, with_reason=False)
        loss_score, _, reasons, _ = get_decision_score(
            trend, {"EPS": -2.5}, with_reason=True
        )

        self.assertLess(loss_score, missing_score)
        self.assertTrue(any("即使技術趨勢強" in reason and "-2分" in reason for reason in reasons))

    def test_high_financial_statement_risk_reduces_rank(self):
        neutral_score, *_ = get_decision_score(self._base(), {}, with_reason=False)
        risk_score, _, reasons, _ = get_decision_score(
            self._base(),
            {
                "Financial_Risk_Level": "high",
                "Financial_Operating_Margin": -8.0,
                "Financial_Net_Margin": -12.0,
            },
            with_reason=True,
        )
        self.assertLess(risk_score, neutral_score)
        self.assertTrue(any("季度財報品質屬高風險" in reason for reason in reasons))

    def test_optional_institutional_sell_streak_and_divergence_reduce_rank(self):
        neutral_score, *_ = get_decision_score(self._base(), {}, with_reason=False)
        risk_score, _, reasons, _ = get_decision_score(
            self._base(
                Institutional_Sell_Streak=3,
                Foreign_Net=800,
                Trust_Net=-650,
            ),
            {},
            with_reason=True,
        )

        self.assertLess(risk_score, neutral_score)
        self.assertTrue(any("連續賣超 3 日" in reason for reason in reasons))
        self.assertTrue(any("外資與投信方向分歧" in reason for reason in reasons))

    def test_institutional_risk_can_be_derived_from_complete_daily_rows(self):
        rows = [
            {"total": -200, "foreign": -150, "trust": -50},
            {"total": -250, "foreign": -200, "trust": -50},
            {"total": -150, "foreign": -100, "trust": -50},
        ]
        aggregate_only, *_ = get_decision_score(
            self._base(Whale_Net=-600, Whale_Net_Days=3), {}, with_reason=False
        )
        derived_score, _, reasons, _ = get_decision_score(
            self._base(), {}, rows, with_reason=True
        )

        self.assertLess(derived_score, aggregate_only)
        self.assertTrue(any("連續賣超 3 日" in reason for reason in reasons))
        self.assertFalse(any("方向分歧" in reason for reason in reasons))

    def test_missing_institutional_components_do_not_create_zero_or_divergence(self):
        neutral_score, *_ = get_decision_score(self._base(), {}, with_reason=False)
        partial_score, _, reasons, _ = get_decision_score(
            self._base(), {}, [{"foreign": 900}], with_reason=True
        )

        self.assertEqual(partial_score, neutral_score)
        self.assertFalse(any("法人" in reason or "外資與投信" in reason for reason in reasons))


if __name__ == "__main__":
    unittest.main()
