"""Portfolio research must not infer ownership or fabricate missing evidence."""

from copy import deepcopy
from datetime import date, datetime, timezone
import unittest

import pandas as pd

from research_portfolio import analyze_portfolio, build_daily_checklist


def holdings():
    return [
        {"ticker": "2330", "weight_pct": 30, "industry": "半導體"},
        {"ticker": "2454", "weight_pct": 20, "industry": "半導體"},
    ]


def prices(count=40):
    dates = pd.bdate_range("2026-06-01", periods=count)
    values = [100.0]
    for index in range(1, count):
        values.append(values[-1] * (1 + (index % 5 - 2) / 100))
    return pd.DataFrame({"2330": values, "2454": [value * 2 for value in values]}, index=dates)


class PortfolioResearchTests(unittest.TestCase):
    def test_keeps_actual_weights_and_cash_without_normalization(self):
        source = holdings()
        original = deepcopy(source)
        result = analyze_portfolio(source)
        self.assertEqual(source, original)
        self.assertEqual(result["source"], "user_entered")
        self.assertEqual(result["total_stock_weight_pct"], 50)
        self.assertEqual(result["cash_weight_pct"], 50)
        self.assertIsNone(result["assumptions"]["equity"])
        self.assertIsNone(result["stress_scenario"]["estimated_loss_amount"])
        self.assertFalse(result["rebalance_proposal"]["orders_created"])

    def test_rejects_excess_weight_instead_of_rescaling(self):
        with self.assertRaises(ValueError):
            analyze_portfolio([{"ticker": "A", "weight_pct": 70}, {"ticker": "B", "weight_pct": 40}])

    def test_rejects_invalid_weights_and_duplicates(self):
        for value in [-1, None, float("nan"), float("inf"), True, "invalid"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                analyze_portfolio([{"ticker": "A", "weight_pct": value}])
        for rows in [
            [{"ticker": " A ", "weight_pct": 10}, {"ticker": "a", "weight_pct": 10}],
            [{"ticker": "", "weight_pct": 10}],
            ["2330"],
        ]:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                analyze_portfolio(rows)

    def test_rejects_invalid_assumptions(self):
        for kwargs in [{"equity": 0}, {"equity": -1}, {"max_stock_weight_pct": 101},
                       {"max_sector_weight_pct": -1}, {"loss_tolerance_pct": float("nan")}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                analyze_portfolio(holdings(), **kwargs)

    def test_reports_concentration_and_unknown_sector_separately(self):
        rows = holdings() + [{"ticker": "A", "weight_pct": 30, "industry": "未知"}]
        result = analyze_portfolio(rows)
        codes = {warning["code"] for warning in result["warnings"]}
        self.assertTrue({"STOCK_CONCENTRATION", "SECTOR_CONCENTRATION", "UNKNOWN_SECTOR_EXPOSURE"} <= codes)
        self.assertEqual(result["concentration"]["unknown_sector_weight_pct"], 30)
        self.assertEqual(result["concentration"]["sector_coverage_pct"], 62.5)
        self.assertFalse(result["rebalance_proposal"]["sector_assessment_complete"])

    def test_stress_is_explicit_hypothesis_and_funded_cash(self):
        rows = [{"ticker": "A", "weight_pct": 80, "industry": "航運"}]
        result = analyze_portfolio(rows, equity=1_000_000, max_stock_weight_pct=100,
                                   max_sector_weight_pct=100, loss_tolerance_pct=10)
        stress = result["stress_scenario"]
        self.assertEqual(stress["portfolio_return_pct"], -16)
        self.assertEqual(stress["estimated_loss_amount"], 160_000)
        self.assertEqual(stress["minimum_stock_reduction_pct_points"], 30)
        self.assertIn("非價格預測", stress["label"])
        plan = result["rebalance_proposal"]
        self.assertEqual(plan["cash_increase_pct_points"], 30)
        self.assertEqual(plan["cash_weight_pct"], 50)
        self.assertEqual(plan["scenario_return_pct"], -10)

    def test_reduction_proposal_satisfies_known_caps_without_any_increase(self):
        result = analyze_portfolio(holdings(), max_stock_weight_pct=25, max_sector_weight_pct=30)
        targets = result["rebalance_proposal"]["holdings"]
        self.assertAlmostEqual(sum(row["illustrative_weight_pct"] for row in targets), 30)
        for row in targets:
            self.assertLessEqual(row["illustrative_weight_pct"], row["current_weight_pct"])
            self.assertLessEqual(row["illustrative_weight_pct"], 25)
        self.assertEqual(result["rebalance_proposal"]["cash_weight_pct"], 70)

    def test_all_cash_is_valid_and_zero_risk_not_unknown(self):
        result = analyze_portfolio([], equity=100_000)
        self.assertEqual(result["cash_weight_pct"], 100)
        self.assertEqual(result["stress_scenario"]["portfolio_return_pct"], 0)
        self.assertEqual(result["stop_risk"]["total_risk_amount"], 0)
        self.assertEqual(result["correlations"]["status"], "not_applicable")

    def test_zero_tolerance_or_caps_does_not_divide_by_zero(self):
        result = analyze_portfolio(holdings(), max_stock_weight_pct=0, max_sector_weight_pct=0, loss_tolerance_pct=0)
        self.assertEqual(result["rebalance_proposal"]["cash_weight_pct"], 100)

    def test_missing_correlation_is_none_not_zero(self):
        pair = analyze_portfolio(holdings())["correlations"]["pairs"][0]
        self.assertIsNone(pair["correlation"])
        self.assertEqual(pair["samples"], 0)
        self.assertIsNone(pair["through_date"])

    def test_correlation_requires_at_least_thirty_actual_return_pairs(self):
        pair = analyze_portfolio(holdings(), prices(30))["correlations"]["pairs"][0]
        self.assertEqual(pair["samples"], 29)
        self.assertIsNone(pair["correlation"])
        pair = analyze_portfolio(holdings(), prices(31))["correlations"]["pairs"][0]
        self.assertEqual(pair["samples"], 30)
        self.assertEqual(pair["correlation"], 1.0)
        self.assertEqual(pair["from_date"], "2026-06-02")

    def test_missing_close_does_not_forward_fill_or_match_multiday_return(self):
        frame = prices(34)
        frame.loc[frame.index[10], "2454"] = None
        original = frame.copy(deep=True)
        result = analyze_portfolio(holdings(), frame)
        pair = result["correlations"]["pairs"][0]
        self.assertEqual(pair["samples"], 31)  # 33 returns minus gap day and next day.
        pd.testing.assert_frame_equal(frame, original)

    def test_nonoverlapping_histories_do_not_create_samples(self):
        frame = prices(70)
        frame.loc[frame.index[:35], "2454"] = None
        frame.loc[frame.index[35:], "2330"] = None
        pair = analyze_portfolio(holdings(), frame)["correlations"]["pairs"][0]
        self.assertEqual(pair["samples"], 0)
        self.assertIsNone(pair["correlation"])

    def test_constant_returns_are_undefined_not_zero_correlation(self):
        frame = prices(40)
        frame["2330"] = 100
        pair = analyze_portfolio(holdings(), frame)["correlations"]["pairs"][0]
        self.assertEqual(pair["status"], "undefined_constant_returns")
        self.assertIsNone(pair["correlation"])

    def test_invalid_price_is_treated_as_missing(self):
        frame = prices(35)
        frame.loc[frame.index[10], "2454"] = float("inf")
        frame.loc[frame.index[20], "2454"] = 0
        pair = analyze_portfolio(holdings(), frame)["correlations"]["pairs"][0]
        self.assertEqual(pair["samples"], 30)

    def test_dated_dict_history_supported(self):
        frame = prices(40)
        pair = analyze_portfolio(holdings(), frame.to_dict())["correlations"]["pairs"][0]
        self.assertEqual(pair["samples"], 39)

    def test_undated_and_duplicate_dates_rejected(self):
        for frame in [prices(40).reset_index(drop=True), pd.concat([prices(40), prices(40).iloc[:1]])]:
            with self.subTest(index=type(frame.index).__name__), self.assertRaises(ValueError):
                analyze_portfolio(holdings(), frame)

    def test_missing_stop_inputs_dont_invent_portfolio_risk(self):
        result = analyze_portfolio(holdings(), equity=100_000)
        self.assertEqual(result["stop_risk"]["status"], "incomplete")
        self.assertIsNone(result["stop_risk"]["total_risk_amount"])
        self.assertIsNone(result["stop_risk"]["known_risk_amount"])

    def test_stop_risk_uses_true_price_stop_and_shares(self):
        rows = [{"ticker": "A", "weight_pct": 20, "price": 100, "stop": 95, "shares": 200}]
        risk = analyze_portfolio(rows, equity=100_000)["stop_risk"]
        self.assertEqual(risk["total_risk_amount"], 1000)
        self.assertEqual(risk["total_risk_pct"], 1)
        self.assertIn("非最大虧損保證", risk["assumption"])

    def test_inconsistent_allocation_is_not_used_for_stop_total(self):
        rows = [{"ticker": "A", "weight_pct": 20, "price": 100, "stop": 95, "shares": 2000}]
        result = analyze_portfolio(rows, equity=100_000)
        self.assertIsNone(result["stop_risk"]["total_risk_amount"])
        self.assertIn("HOLDING_VALUE_MISMATCH", {warning["code"] for warning in result["warnings"]})

    def test_partial_stop_risk_only_shows_known_subtotal(self):
        rows = [{"ticker": "A", "weight_pct": 20, "price": 100, "stop": 95, "shares": 200},
                {"ticker": "B", "weight_pct": 30}]
        risk = analyze_portfolio(rows, equity=100_000)["stop_risk"]
        self.assertEqual(risk["known_risk_amount"], 1000)
        self.assertIsNone(risk["total_risk_amount"])
        self.assertIsNone(risk["total_risk_pct"])


class DailyChecklistTests(unittest.TestCase):
    def test_checklist_is_manual_unverified_same_date(self):
        result = build_daily_checklist("2026-09-24", has_candidates=True)
        self.assertEqual(result["analysis_date"], "2026-09-24")
        self.assertEqual(result["timezone"], "Asia/Taipei")
        self.assertFalse(result["trading_day_confirmed"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["orders_created"])
        self.assertFalse(result["schedule_created"])
        self.assertEqual([row["time"] for row in result["items"]], ["08:30", "09:00", "09:15", "11:30", "13:20", "13:30", "15:17"])
        self.assertIn("自訂時間", result["items"][-1]["stage"])

    def test_weekend_is_not_advanced_to_a_guessed_trading_day(self):
        result = build_daily_checklist(date(2026, 9, 26))
        self.assertEqual(result["analysis_date"], "2026-09-26")
        self.assertEqual(result["calendar_status"], "weekend_unconfirmed")
        self.assertIn("不執行盤中步驟", result["warnings"][0])

    def test_no_candidates_explicitly_means_no_forced_trade(self):
        result = build_daily_checklist("2026-09-24")
        self.assertTrue(any("不為湊數放寬" in line for line in result["items"][0]["checks"]))

    def test_timezone_aware_date_is_localized_to_taipei(self):
        result = build_daily_checklist(datetime(2026, 9, 24, 23, tzinfo=timezone.utc))
        self.assertEqual(result["analysis_date"], "2026-09-25")

    def test_invalid_date_rejected(self):
        for value in ["2026-02-30", None, "tomorrow"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_daily_checklist(value)


if __name__ == "__main__":
    unittest.main()
