import unittest

from intraday_ranking import (
    annotate_intraday_score,
    institutional_aggregate_from_record,
    institutional_rows_from_record,
    original_ranking_targets,
    support_data_from_postclose_record,
)


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

    def test_postclose_support_reuses_reported_values_without_zero_fill(self):
        fund = support_data_from_postclose_record({
            "產業": "半導體", "EPS": 12.5, "EPS_Period": "ttm",
            "MoM": None, "YoY": 8.2, "Revenue_Source": "TWSE OpenAPI",
            "Institutional_Days": 5, "Institutional_Status": "ok",
            "Institutional_Source": "TWSE OpenAPI", "Whale_Net": 1200,
        }, current_price=250)
        self.assertEqual(fund["EPS"], 12.5)
        self.assertEqual(fund["PE"], 20)
        self.assertIsNone(fund["MoM"])
        self.assertEqual(fund["YoY"], 8.2)
        self.assertEqual(fund["_data_status"]["revenue"], "ok")
        self.assertEqual(fund["_institutional_status"], "ok")

    def test_reported_institutional_aggregate_is_available_without_daily_rows(self):
        snapshot = institutional_aggregate_from_record({
            "Whale_Net": 0,
            "Whale_Net_Days": 3,
            "Institutional_Status": "ok",
            "Institutional_Source": "TWSE T86",
        })
        self.assertEqual(snapshot["net"], 0)
        self.assertEqual(snapshot["days"], 3)
        self.assertEqual(snapshot["source"], "TWSE T86")
        self.assertIsNone(institutional_aggregate_from_record({
            "Whale_Net": None,
            "Whale_Net_Days": 0,
        }))

    def test_persisted_daily_rows_restore_real_breakdown(self):
        rows = institutional_rows_from_record({
            "Institutional_Source": "TPEx 3insti",
            "Institutional_Rows": [{
                "date": "2026-08-21", "foreign": 511, "trust": 0,
                "dealer": 0, "total": 511, "source": "FinMind",
            }],
        })
        self.assertEqual(rows, [{
            "日期": "08/21", "外資(張)": 511, "投信(張)": 0,
            "自營商(張)": 0, "單日合計(張)": 511, "_source": "FinMind",
        }])

    def test_aggregate_is_never_fabricated_into_daily_rows(self):
        rows = institutional_rows_from_record({
            "Whale_Net": 601,
            "Whale_Net_Days": 3,
            "Institutional_Source": "TPEx 3insti",
        })
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
