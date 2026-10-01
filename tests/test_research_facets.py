from copy import deepcopy
import unittest

from research_facets import build_research_facets


def report(**changes):
    return {"analysis_date": "2026-09-30", "forecast_date": "2026-10-01", **changes}


def item():
    return {
        "versions": ["legacy"], "new_approved": False, "price_alignment": "matched",
        "technical": {
            "status": "ok", "as_of": "2026-09-30", "data_date": "2026-09-30",
            "daily": {"status": "ok", "date": "2026-09-30", "close": 110, "ma20": 105,
                      "ma60": 100, "macd_hist": 1, "rsi14": 55},
            "weekly": {"status": "ok", "date": "2026-09-25", "close": 109, "ma20": 101, "ma60": 95},
        },
        "institutional": {"Institutional_Status": "ok", "Institutional_Latest_Date": "2026-09-30",
                          "Whale_Net": 1234.5, "Whale_Net_Days": 3},
        "fundamentals": {"Revenue_Status": "ok", "Revenue_Period": "2026-08",
                         "Revenue_Expected_Period": "2026-08", "YoY": 12.3, "MoM": -1.2,
                         "Financial_Status": "ok", "Financial_Period": "2026-Q2",
                         "Financial_Expected_Period": "2026-Q2", "Financial_Risk_Level": "low",
                         "Financial_Operating_Margin": 20.26, "EPS": 99, "EPS_Period": "ttm"},
    }


def text(facet):
    return "；".join(facet["advantages"] + facet["risks"])


class ResearchFacetTests(unittest.TestCase):
    def test_three_facets_are_short_factual_and_non_mutating(self):
        row, snapshot = item(), report()
        originals = deepcopy((row, snapshot))
        facets = build_research_facets(row, snapshot)
        self.assertEqual(set(facets), {"technical", "institutional", "fundamental"})
        self.assertIn("日線與完整週線均呈多頭排列", text(facets["technical"]))
        self.assertIn("MACD 柱值為正", text(facets["technical"]))
        self.assertIn("買超 1,234.5 張", text(facets["institutional"]))
        self.assertIn("不代表逐日連買", text(facets["institutional"]))
        self.assertIn("2026-08 營收年增+12.3%", text(facets["fundamental"]))
        self.assertIn("月增-1.2%", text(facets["fundamental"]))
        self.assertIn("20.3% 為正", text(facets["fundamental"]))
        self.assertNotIn("EPS", str(facets))
        self.assertNotIn("勝率", str(facets))
        self.assertNotIn("便宜", str(facets))
        self.assertEqual((row, snapshot), originals)
        for facet in facets.values():
            for phrases in facet.values():
                self.assertLessEqual(len(phrases), 2)
                self.assertLessEqual(len("；".join(phrases)), 180)

    def test_missing_analysis_date_has_no_current_advantages(self):
        for value in (None, "", "2026-99-99", "20260930", True):
            facets = build_research_facets(item(), report(analysis_date=value))
            for facet in facets.values():
                self.assertEqual(facet["advantages"], [])
                self.assertIn("日期不明", text(facet))

    def test_expired_report_is_still_dated_historical_research(self):
        row = item()
        self.assertEqual(build_research_facets(row, report()),
                         build_research_facets(row, report(forecast_period_elapsed=True)))

    def test_missing_or_malformed_nested_data_is_explicit_unknown(self):
        for row in ({}, {"technical": [], "institutional": True, "fundamentals": "bad"}):
            for facet in build_research_facets(row, report()).values():
                self.assertEqual(facet["advantages"], [])
                self.assertTrue(facet["risks"])

    def test_technical_alignment_or_dates_must_match(self):
        for alignment in ("price_adjustment_mismatch", "stale_history", "unavailable", None):
            row = item()
            row["price_alignment"] = alignment
            self.assertEqual(build_research_facets(row, report())["technical"]["advantages"], [])
        for key in ("data_date", "as_of"):
            for value in ("2026-09-29", "2026-10-01", None):
                row = item()
                row["technical"][key] = value
                self.assertEqual(build_research_facets(row, report())["technical"]["advantages"], [])
        row = item()
        row["technical"]["daily"]["date"] = None
        self.assertEqual(build_research_facets(row, report())["technical"]["advantages"], [])

    def test_technical_insufficient_or_non_finite_prices_not_used(self):
        row = item()
        row["technical"]["daily"]["status"] = "insufficient_history"
        self.assertIn("日線歷史不足", text(build_research_facets(row, report())["technical"]))
        for value in (None, float("nan"), float("inf"), True, -1, 0, [], "bad"):
            row = item()
            row["technical"]["daily"]["ma20"] = value
            self.assertEqual(build_research_facets(row, report())["technical"]["advantages"], [])

    def test_technical_hot_and_negative_momentum_take_priority(self):
        row = item()
        row["technical"]["daily"].update(rsi14=80, macd_hist=-1)
        result = build_research_facets(row, report())["technical"]
        self.assertEqual(len(result["risks"]), 2)
        self.assertIn("偏熱", result["risks"][0])
        self.assertIn("MACD 柱值為負", result["risks"][1])

    def test_technical_oversold_not_called_reversal(self):
        row = item()
        row["technical"]["daily"].update(close=90, rsi14=20, macd_hist=0)
        row["technical"]["weekly"]["close"] = 90
        result = build_research_facets(row, report())["technical"]
        self.assertEqual(result["advantages"], [])
        self.assertIn("低於 20MA", text(result))
        self.assertIn("超賣不等同反轉", text(result))

    def test_mixed_daily_and_weak_weekly_are_separate(self):
        row = item()
        row["technical"]["daily"].update(ma60=120, macd_hist=0, rsi14=50)
        row["technical"]["weekly"]["close"] = 90
        result = build_research_facets(row, report())["technical"]
        self.assertIn("高於 20MA", text(result))
        self.assertIn("長週期偏弱", text(result))
        self.assertNotIn("均呈多頭", text(result))

    def test_weekly_bad_date_missing_and_stale_are_not_positive(self):
        for changes in ({"date": None}, {"date": "2026-10-02"}, {"date": "2026-08-28"},
                        {"status": "insufficient_history"}, {"ma20": float("nan")}):
            row = item()
            row["technical"]["weekly"].update(changes)
            result = build_research_facets(row, report())["technical"]
            self.assertNotIn("均呈多頭", text(result))
            self.assertTrue(result["risks"])

    def test_missing_or_out_of_range_momentum_remains_unknown(self):
        for value in (None, float("nan"), -1, 101, True):
            row = item()
            row["technical"]["daily"]["rsi14"] = value
            result = build_research_facets(row, report())["technical"]
            self.assertIn("動能指標缺值", text(result))
            self.assertNotIn("RSI 未達過熱", text(result))

    def test_institutional_negative_and_zero_are_not_missing_or_positive(self):
        row = item()
        row["institutional"]["Whale_Net"] = -1234
        result = build_research_facets(row, report())["institutional"]
        self.assertIn("賣超 1,234 張", text(result))
        row["institutional"]["Whale_Net"] = 0
        result = build_research_facets(row, report())["institutional"]
        self.assertEqual(result["advantages"], [])
        self.assertIn("方向中性", text(result))
        self.assertNotIn("缺少", text(result))

    def test_institutional_partial_stale_and_bad_values_are_not_direction(self):
        changes = [{"Institutional_Status": "partial"}, {"Institutional_Status": "stale"},
                   {"Institutional_Latest_Date": "2026-09-29"}, {"Institutional_Latest_Date": "2026-10-01"},
                   {"Institutional_Latest_Date": None}, {"Whale_Net_Days": 2}, {"Whale_Net_Days": 3.5},
                   {"Whale_Net_Days": 4}]
        changes += [{"Whale_Net": value} for value in (None, float("nan"), float("inf"), True)]
        for update in changes:
            row = item()
            row["institutional"].update(update)
            result = build_research_facets(row, report())["institutional"]
            self.assertEqual(result["advantages"], [])
            self.assertNotIn("買超", text(result))
            self.assertNotIn("賣超", text(result))

    def test_revenue_wrong_period_status_and_future_do_not_imply_growth(self):
        for changes in ({"Revenue_Status": "partial"}, {"Revenue_Expected_Period": None},
                        {"Revenue_Period": "2026-07"}, {"Revenue_Period": "2026-99"},
                        {"Revenue_Period": "2026-10", "Revenue_Expected_Period": "2026-10"}):
            row = item()
            row["fundamentals"].update(changes)
            result = build_research_facets(row, report())["fundamental"]
            self.assertNotIn("年增+", text(result))
            self.assertIn("月營收未齊", text(result))

    def test_revenue_zero_and_unknown_not_substituted_for_each_other(self):
        row = item()
        row["fundamentals"].update(YoY=0, MoM=0)
        self.assertIn("皆為 0%", text(build_research_facets(row, report())["fundamental"]))
        for value in (None, float("nan"), True):
            row["fundamentals"].update(YoY=value, MoM=value)
            result = build_research_facets(row, report())["fundamental"]
            self.assertIn("資料不足", text(result))
            self.assertNotIn("皆為 0%", text(result))

    def test_expected_revenue_must_match_analysis_date_even_if_saved_status_is_ok(self):
        row = item()
        row["fundamentals"].update(Revenue_Period="2024-08", Revenue_Expected_Period="2024-08")
        result = build_research_facets(row, report())["fundamental"]
        self.assertNotIn("年增+", text(result))
        self.assertIn("期別未核實", text(result))

    def test_revenue_uses_same_conservative_month_boundary_across_year_end(self):
        for analysis_date, expected in (("2026-09-10", "2026-07"), ("2026-09-11", "2026-08"),
                                        ("2026-01-10", "2025-11"), ("2026-01-11", "2025-12")):
            row = item()
            row["fundamentals"].update(Revenue_Period=expected, Revenue_Expected_Period=expected)
            result = build_research_facets(row, report(analysis_date=analysis_date))["fundamental"]
            self.assertIn(f"{expected} 營收年增+12.3%", text(result))

    def test_early_published_completed_month_is_valid_after_expected_lower_bound(self):
        row = item()
        row["fundamentals"].update(Revenue_Period="2026-09", Revenue_Expected_Period="2026-08")
        result = build_research_facets(row, report(analysis_date="2026-10-05"))["fundamental"]
        self.assertIn("2026-09 營收年增+12.3%", text(result))
        self.assertNotIn("月營收未齊", text(result))

    def test_early_published_completed_quarter_is_valid_after_expected_lower_bound(self):
        row = item()
        row["fundamentals"].update(Financial_Period="2026-Q3", Financial_Expected_Period="2026-Q2")
        result = build_research_facets(row, report(analysis_date="2026-10-15"))["fundamental"]
        self.assertIn("2026-Q3 營業利益率 20.3% 為正", text(result))
        self.assertNotIn("財報未齊", text(result))

    def test_financial_expected_lower_bound_must_match_analysis_date(self):
        row = item()
        row["fundamentals"].update(Financial_Period="2024-Q2", Financial_Expected_Period="2024-Q2")
        result = build_research_facets(row, report())["fundamental"]
        self.assertNotIn("利益率 20.3%", text(result))
        self.assertIn("財報未齊", text(result))

    def test_financial_expected_period_boundary_and_previous_year(self):
        for analysis_date, expected in (("2026-03-31", "2025-Q3"), ("2026-04-01", "2025-Q4"),
                                        ("2026-05-16", "2026-Q1"), ("2026-08-15", "2026-Q2"),
                                        ("2026-11-15", "2026-Q3")):
            row = item()
            row["fundamentals"].update(Financial_Period=expected, Financial_Expected_Period=expected)
            result = build_research_facets(row, report(analysis_date=analysis_date))["fundamental"]
            self.assertIn(f"{expected} 營業利益率 20.3% 為正", text(result))

    def test_financial_requires_verified_non_future_expected_period(self):
        for update in ({"Financial_Status": "partial"}, {"Financial_Expected_Period": None},
                       {"Financial_Period": "2026-Q1"}, {"Financial_Period": "2026-Q5"},
                       {"Financial_Period": "2026-Q4", "Financial_Expected_Period": "2026-Q4"}):
            row = item()
            row["fundamentals"].update(update)
            result = build_research_facets(row, report())["fundamental"]
            self.assertIn("財報未齊或期別未核實", text(result))
            self.assertNotIn("利益率 20.3%", text(result))

    def test_financial_medium_high_risk_not_hidden_by_growth(self):
        for level in ("medium", "high"):
            row = item()
            row["fundamentals"].update(MoM=2, Financial_Risk_Level=level,
                                       Financial_Risk_Flags=["high_debt_ratio", "low_current_ratio", "not_real"])
            result = build_research_facets(row, report())["fundamental"]
            self.assertIn("財報風險", result["risks"][0])
            self.assertIn("負債比偏高", result["risks"][0])
            self.assertNotIn("not_real", text(result))
            self.assertTrue(result["advantages"])

    def test_financial_risk_and_margin_do_not_repeat_and_hide_revenue_decline(self):
        row = item()
        row["fundamentals"].update(Financial_Risk_Level="high", Financial_Operating_Margin=-2.3,
                                   Financial_Risk_Flags=["negative_operating_margin", "negative_net_margin"])
        result = build_research_facets(row, report())["fundamental"]
        self.assertIn("財報風險偏高", result["risks"][0])
        self.assertIn("營業利益率 -2.3% 為負", result["risks"][0])
        self.assertIn("月增-1.2%", result["risks"][1])
        self.assertEqual(text(result).count("營業利益率"), 1)

    def test_small_finite_changes_do_not_round_to_false_zero(self):
        row = item()
        row["fundamentals"].update(YoY=0.01, MoM=-0.02, Financial_Operating_Margin=0.03)
        row["institutional"]["Whale_Net"] = -0.01
        facets = build_research_facets(row, report())
        self.assertIn("年增+0.01%", text(facets["fundamental"]))
        self.assertIn("月增-0.02%", text(facets["fundamental"]))
        self.assertIn("利益率 0.03%", text(facets["fundamental"]))
        self.assertIn("賣超 0.01 張", text(facets["institutional"]))

    def test_financial_negative_zero_missing_and_unknown_risk(self):
        for margin, expected in ((-2.3, "為負"), (0, "本業未見獲利"), (None, "利益率缺值")):
            row = item()
            row["fundamentals"].update(MoM=1, Financial_Operating_Margin=margin)
            result = build_research_facets(row, report())["fundamental"]
            self.assertIn(expected, text(result))
            self.assertNotIn("利益率 20.3%", text(result))
        row["fundamentals"]["Financial_Risk_Level"] = None
        self.assertIn("風險尚無完整判定", text(build_research_facets(row, report())["fundamental"]))

    def test_version_and_score_never_imply_good_fundamentals(self):
        row = {"versions": ["new"], "new_approved": True, "score": 99,
               "fundamentals": {"EPS": 99, "EPS_Period": "ttm", "Financial_Risk_Level": "low"}}
        facets = build_research_facets(row, report())
        self.assertTrue(all(not facet["advantages"] for facet in facets.values()))


if __name__ == "__main__":
    unittest.main()
