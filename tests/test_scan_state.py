import unittest
from datetime import datetime

from scan_state import (
    build_scan_quality,
    latest_trading_date,
    next_streak,
    previous_scan_state,
    scan_universe_limit,
    should_complete_candidate,
)
from scoring import get_decision_score


class ScanStateTests(unittest.TestCase):
    def test_latest_trading_date_comes_from_market_history(self):
        index = [datetime(2026, 8, 14), datetime(2026, 8, 17)]
        self.assertEqual(latest_trading_date(index), "2026-08-17")

    def test_new_trading_day_uses_previous_rank_and_increments_streak(self):
        previous = {
            "scan_date": "2026-08-14",
            "data": [{"代號": "2330", "Rank": 3, "Streak": 4}],
        }
        streaks, ranks, same_day = previous_scan_state(previous, "2026-08-17")
        self.assertFalse(same_day)
        self.assertEqual(ranks["2330"], 3)
        self.assertEqual(next_streak("2330", streaks, same_day), 5)

    def test_same_day_rerun_preserves_prior_day_comparison_and_streak(self):
        previous = {
            "scan_date": "2026-08-17",
            "data": [{"代號": "2330", "Rank": 1, "Prev_Rank": 3, "Streak": 5}],
        }
        streaks, ranks, same_day = previous_scan_state(previous, "2026-08-17")
        self.assertTrue(same_day)
        self.assertEqual(ranks["2330"], 3)
        self.assertEqual(next_streak("2330", streaks, same_day), 5)

    def test_missing_sources_reduce_confidence(self):
        quality, confidence = build_scan_quality({
            "price": "ok",
            "revenue": "missing",
            "institutional": "missing",
            "market": "ok",
        })
        self.assertEqual(quality["revenue"], "missing")
        self.assertLess(confidence, 100)

    def test_prefilter_keeps_every_candidate_that_can_reach_45(self):
        self.assertTrue(should_complete_candidate(36))
        self.assertFalse(should_complete_candidate(35))
        self.assertTrue(should_complete_candidate(5, "Buy"))

    def test_scan_universe_is_300_daily_and_500_on_friday(self):
        self.assertEqual(scan_universe_limit("2026-08-17"), 300)  # Monday
        self.assertEqual(scan_universe_limit("2026-08-21"), 500)  # Friday

    def test_scan_universe_explicit_override(self):
        self.assertEqual(scan_universe_limit("2026-08-17", "500"), 500)
        self.assertEqual(scan_universe_limit("2026-08-21", "300"), 300)
        self.assertEqual(scan_universe_limit("2026-08-21", "invalid"), 500)


class ScoringIntegrationTests(unittest.TestCase):
    def test_empty_payload_does_not_generate_a_plausible_score(self):
        score, label, reasons, feature = get_decision_score({}, {}, with_reason=True)
        self.assertEqual(score, 0)
        self.assertIn("資料不足", label)
        self.assertIn("資料不足", feature)
        self.assertTrue(reasons)

    def test_whale_net_is_part_of_final_score(self):
        base = {
            "收盤價": 100,
            "5MA": 100,
            "20MA": 100,
            "BB_DN": 90,
            "BB_UP": 110,
            "成交量": 1000,
            "5日均量": 1000,
            "MACD柱": 1,
            "前日MACD柱": 0,
            "J值": 50,
            "RSI": 50,
            "Momentum_Score": 50,
            "Confidence": 100,
        }
        neutral, *_ = get_decision_score(base, {}, mode="post", with_reason=False)
        institutional_buying, *_ = get_decision_score(
            {**base, "Whale_Net": 4000}, {}, mode="post", with_reason=False
        )
        self.assertGreater(institutional_buying, neutral)

    def test_institutional_argument_is_used_when_whale_net_is_absent(self):
        base = {
            "收盤價": 100, "5MA": 100, "20MA": 100, "BB_DN": 90, "BB_UP": 110,
            "成交量": 1000, "5日均量": 1000, "MACD柱": 1, "前日MACD柱": 0,
            "J值": 50, "RSI": 50, "Momentum_Score": 50, "Confidence": 100,
        }
        neutral, *_ = get_decision_score(base, {}, mode="post", with_reason=False)
        buying, *_ = get_decision_score(
            base, {}, [{"單日合計(張)": 4000}], mode="post", with_reason=False
        )
        self.assertGreater(buying, neutral)

    def test_institutional_lots_are_converted_to_shares_for_volume_ratio(self):
        base = {
            "收盤價": 100, "5MA": 100, "20MA": 100, "BB_DN": 90, "BB_UP": 110,
            "成交量": 1_000_000, "5日均量": 1_000_000, "MACD柱": 1, "前日MACD柱": 0,
            "J值": 50, "RSI": 50, "Momentum_Score": 50, "Confidence": 100,
        }
        neutral, *_ = get_decision_score(base, {}, mode="post", with_reason=False)
        ratio_buying, *_ = get_decision_score(
            {**base, "Whale_Net": 300}, {}, mode="post", with_reason=False
        )
        self.assertGreater(ratio_buying, neutral)


if __name__ == "__main__":
    unittest.main()
