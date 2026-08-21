import unittest

from intraday_ranking import annotate_intraday_score, original_ranking_targets


class IntradayRankingTests(unittest.TestCase):
    def test_targets_preserve_original_order_and_remove_duplicates(self):
        records = [
            {"代號": "2330", "Score": 80},
            {"代號": "2317", "Score": 70},
            {"代號": "2330.TW", "Score": 60},
            {"代號": "", "Score": 90},
        ]
        self.assertEqual(original_ranking_targets(records), ["2330", "2317"])

    def test_targets_do_not_truncate_a_large_original_ranking(self):
        records = [{"代號": str(1000 + index)} for index in range(300)]
        self.assertEqual(len(original_ranking_targets(records)), 300)

    def test_intraday_result_keeps_baseline_and_exposes_score_delta(self):
        result = annotate_intraday_score(
            {"代號": "2330", "Score": 72, "Rank": 3, "名稱": "台積電"},
            {"代號": "2330", "Score": 78, "評級": "強勢"},
        )
        self.assertEqual(result["Original_Score"], 72)
        self.assertEqual(result["Score"], 78)
        self.assertEqual(result["Score_Diff"], 6)
        self.assertEqual(result["Rank"], 3)
        self.assertEqual(result["Score_Mode_Raw"], "realtime")
        self.assertIn("盤後 72 → 盤中 78", result["Score_Source"])

    def test_missing_live_score_is_not_marked_as_rescored(self):
        result = annotate_intraday_score({"Score": 72}, {"Score": None})
        self.assertFalse(result["Intraday_Rescored"])
        self.assertIsNone(result["Score_Diff"])


if __name__ == "__main__":
    unittest.main()
