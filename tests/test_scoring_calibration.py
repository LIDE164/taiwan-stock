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


if __name__ == "__main__":
    unittest.main()
