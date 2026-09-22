from copy import deepcopy
import unittest
from unittest.mock import patch

from legacy_entry_readiness import (
    LEGACY_RULE_COMMIT, LEGACY_RULE_DATE, build_legacy_entry_plan,
)
from ranking_comparison import build_comparison_rows, comparison_display_record, refresh_comparison_labels


class RankingComparisonTests(unittest.TestCase):
    @staticmethod
    def record(ticker="2330", **updates):
        result = {
            "代號": ticker, "名稱": "比較用股票", "產業": "半導體", "Score": 80,
            "收盤價": 100.5, "最高價": 101, "20MA": 100, "ATR": 4,
            "BB_UP": 120, "RSI": 55, "BIAS": 0.5, "漲跌幅": 1,
            "Confidence": 90, "Volume_Confirmed": True, "Est_Vol_Ratio": 1.3,
            "Signal_Conflict": "低", "Entry_Pattern": "一般觀察型",
            "Entry_Status": "等待觸發", "Entry_Ready": False,
            "Entry_Status_Group": "wait", "Entry_Reason": "策略回測樣本未達 15 筆",
            "Entry_Low": 100, "Entry_High": 100.5, "Entry_Stop": 96,
            "Entry_Target": 108.5, "Entry_Net_RRR": 1.35,
            "WinRate": 56.1, "Backtest_Samples": 1, "Validation_Samples": 0,
        }
        result.update(updates)
        return result

    def test_legacy_rules_are_frozen_and_do_not_apply_new_evidence_or_financial_gates(self):
        record = self.record(
            Backtest_Samples=0, Validation_Samples=0, WinRate=0,
            Financial_Risk_Level="high", Market_Regime="空頭",
            Critical_Data_Ready=False, Institutional_Sell_Streak=5, Whale_Net=-1000,
        )
        with patch("entry_readiness.build_entry_readiness", side_effect=AssertionError("new rules invoked")):
            plan = build_legacy_entry_plan(record)
        self.assertTrue(plan["Entry_Ready"])
        self.assertEqual(plan["Legacy_Rule_Date"], "2026-09-09")
        self.assertEqual(plan["Legacy_Rule_Commit"], LEGACY_RULE_COMMIT)
        self.assertEqual(plan["Entry_Schema"], 2)

    def test_legacy_keeps_score_volume_and_overheat_requirements(self):
        cases = (
            {"Score": 64}, {"Est_Vol_Ratio": 1.09}, {"Volume_Confirmed": False},
            {"Confidence": 69}, {"RSI": 75}, {"漲跌幅": 7}, {"Signal_Conflict": "高"},
        )
        for updates in cases:
            with self.subTest(updates=updates):
                self.assertFalse(build_legacy_entry_plan(self.record(**updates))["Entry_Ready"])
        self.assertTrue(build_legacy_entry_plan(self.record(Score=65))["Entry_Ready"])

    def test_legacy_does_not_use_new_cost_adjusted_zone(self):
        plan = build_legacy_entry_plan(self.record(收盤價=100, 最高價=100.5, ATR=1))
        self.assertTrue(plan["Entry_Ready"])
        self.assertEqual(plan["Entry_Low"], 100)
        self.assertEqual(plan["Entry_High"], 100.5)
        self.assertNotIn("Entry_Net_RRR", plan)

    def test_missing_or_nonfinite_legacy_inputs_never_generate_an_approval(self):
        for field in ("Score", "Confidence", "20MA", "ATR", "最高價", "RSI", "Est_Vol_Ratio"):
            for value in (None, float("nan"), float("inf")):
                with self.subTest(field=field, value=value):
                    plan = build_legacy_entry_plan(self.record(**{field: value}))
                    self.assertFalse(plan["Entry_Ready"])
                    self.assertIsNone(plan["Entry_Low"])

    def test_intraday_uses_only_the_saved_legacy_plan_and_never_moves_its_levels(self):
        baseline = build_legacy_entry_plan(self.record())
        live = self.record(
            收盤價=101.5, 最高價=122, **{
                "20MA": 120, "ATR": 10, "Legacy_Entry_Plan": baseline,
                "Intraday_Quote_Status": "realtime",
            },
        )
        original = deepcopy(live)
        plan = build_legacy_entry_plan(live, intraday=True)
        self.assertTrue(plan["Entry_Ready"])
        for key in ("Entry_Low", "Entry_High", "Entry_Stop", "Entry_Target", "No_Chase_Price"):
            self.assertEqual(plan[key], baseline[key])
        self.assertEqual(live, original)

    def test_intraday_missing_invalid_or_stale_baseline_is_not_executable(self):
        good = build_legacy_entry_plan(self.record())
        for updates in (
            {},
            {"Legacy_Entry_Plan": {"Entry_Low": 100}},
            {"Legacy_Entry_Plan": {**good, "Legacy_Rule_Commit": "different"}},
            {"Legacy_Entry_Plan": {**good, "Entry_Stop": 200}},
        ):
            with self.subTest(updates=updates):
                plan = build_legacy_entry_plan(
                    self.record(Intraday_Quote_Status="realtime", **updates), intraday=True,
                )
                self.assertFalse(plan["Entry_Ready"])
        stale = build_legacy_entry_plan(
            self.record(Legacy_Entry_Plan=good, Intraday_Quote_Status="stale"), intraday=True,
        )
        self.assertFalse(stale["Entry_Ready"])

    def test_union_deduplicates_versions_preserves_source_and_uses_current_score(self):
        records = [
            self.record("1111", Score=85, Entry_Status="現在可執行", Entry_Ready=True),
            self.record("2222", Score=75, 產業="工業"),
            self.record("3333", Score=90, RSI=80, Entry_Status="現在可執行", Entry_Ready=True),
        ]
        original = deepcopy(records)
        rows = build_comparison_rows(records)
        self.assertEqual([row["代號"] for row in rows], ["3333", "1111", "2222"])
        self.assertEqual([row["Execution_Versions"] for row in rows], [["new"], ["new", "legacy"], ["legacy"]])
        self.assertEqual(rows[1]["Execution_Version_Label"], "新制・舊制")
        self.assertEqual(rows[2]["Entry_Status"], "等待觸發")
        self.assertFalse(rows[2]["Entry_Ready"])
        self.assertEqual(records, original)
        rows[1]["Legacy_Entry_Plan"]["Entry_Low"] = 1
        self.assertEqual(records, original)

    def test_each_version_has_independent_limit_and_new_industry_concentration(self):
        records = []
        for index in range(12):
            records.append(self.record(
                str(1000 + index), Score=99 - index, RSI=80,
                產業=f"新產業{index // 2}", Entry_Status="現在可執行", Entry_Ready=True,
            ))
            records.append(self.record(str(2000 + index), Score=85 - index, 產業=f"舊產業{index // 2}"))
        rows = build_comparison_rows(records)
        self.assertEqual(len(rows), 20)
        for version in ("new", "legacy"):
            selected = [row for row in rows if version in row["Execution_Versions"]]
            self.assertEqual(len(selected), 10)
            if version == "new":
                self.assertTrue(all(sum(row["產業"] == industry for row in selected) <= 2
                                    for industry in {row["產業"] for row in selected}))
        self.assertEqual(len(build_comparison_rows(records, limit_per_version=2)), 4)

    def test_legacy_has_no_industry_cap_like_the_september9_selector(self):
        records = [
            self.record("1111", Score=95, RSI=80, Entry_Status="現在可執行", Entry_Ready=True),
            self.record("2222", Score=90, Entry_Status="現在可執行", Entry_Ready=True),
            self.record("3333", Score=85), self.record("4444", Score=80),
        ]
        rows = build_comparison_rows(records)
        self.assertEqual([row["代號"] for row in rows], ["1111", "2222", "3333", "4444"])
        self.assertEqual(rows[1]["Execution_Versions"], ["new", "legacy"])
        self.assertEqual(rows[2]["Execution_Versions"], ["legacy"])

    def test_equal_scores_keep_source_ranking_and_invalid_rows_are_ignored(self):
        records = [self.record("9999"), self.record("1111"), self.record("2222"),
                   self.record("<bad>"), self.record("8888", Score=float("nan"))]
        self.assertEqual([row["代號"] for row in build_comparison_rows(records)], ["9999", "1111", "2222"])
        self.assertEqual(len(build_comparison_rows([records[0], records[0]])), 1)

    def test_legacy_only_display_is_never_a_new_execution_or_a_legacy_backtest(self):
        row = build_comparison_rows([self.record()])[0]
        original = deepcopy(row)
        display = comparison_display_record(row)
        self.assertEqual(display["Entry_Status"], "舊制可執行（比較）")
        self.assertFalse(display["Entry_Ready"])
        self.assertEqual(display["Entry_Status_Group"], "comparison")
        self.assertEqual(display["Entry_High"], row["Legacy_Entry_Plan"]["Entry_High"])
        self.assertNotIn("Entry_Net_RRR", display)
        self.assertIn("策略回測樣本未達 15 筆", display["Entry_Reason"])
        self.assertIn("新制回測（非舊制回測）", display["Entry_Reason"])
        self.assertEqual(display["WinRate"], row["WinRate"])
        self.assertEqual(display["Backtest_Samples"], row["Backtest_Samples"])
        self.assertEqual(row, original)

    def test_overlap_display_keeps_current_entry_prices(self):
        row = build_comparison_rows([
            self.record(Entry_Status="現在可執行", Entry_Ready=True, Entry_High=100.25),
        ])[0]
        display = comparison_display_record(row)
        self.assertEqual(display["Execution_Versions"], ["new", "legacy"])
        self.assertEqual(display["Entry_High"], 100.25)
        self.assertEqual(display["Entry_Status"], "現在可執行")
        self.assertTrue(display["Entry_Ready"])

    def test_intraday_comparison_requires_live_quote_for_both_versions(self):
        record = self.record(Entry_Status="現在可執行", Entry_Ready=True)
        record["Legacy_Entry_Plan"] = build_legacy_entry_plan(record)
        self.assertEqual(build_comparison_rows([record], intraday=True), [])
        record["Intraday_Quote_Status"] = "realtime"
        self.assertEqual(build_comparison_rows([record], intraday=True)[0]["Execution_Versions"], ["new", "legacy"])

    def test_limits_reject_invalid_values(self):
        for limit in (0, -1, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                build_comparison_rows([], limit_per_version=limit)
        self.assertEqual(LEGACY_RULE_DATE, "2026-09-09")

    def test_intraday_refresh_removes_stale_badges_without_losing_saved_plan(self):
        row = build_comparison_rows([self.record(Entry_Status="現在可執行", Entry_Ready=True)])[0]
        baseline = deepcopy(row["Legacy_Entry_Plan"])
        row.update(收盤價=130, Entry_Status="等待拉回", Entry_Ready=False, Intraday_Quote_Status="realtime")
        original = deepcopy(row)
        refreshed = refresh_comparison_labels(row, intraday=True)
        self.assertNotIn("Execution_Versions", refreshed)
        self.assertEqual(refreshed["Legacy_Entry_Plan"], baseline)
        self.assertEqual(refreshed["Entry_High"], row["Entry_High"])
        self.assertEqual(row, original)
        self.assertEqual(refresh_comparison_labels(self.record()), self.record())

    def test_new_only_live_result_keeps_legacy_baseline_for_future_quotes(self):
        record = self.record(Entry_Status="現在可執行", Entry_Ready=True)
        record["Legacy_Entry_Plan"] = build_legacy_entry_plan(record)
        record.update(RSI=80, Intraday_Quote_Status="realtime")
        row = build_comparison_rows([record], intraday=True)[0]
        self.assertEqual(row["Execution_Versions"], ["new"])
        self.assertEqual(row["Legacy_Entry_Plan"], record["Legacy_Entry_Plan"])
        row.update(RSI=55, Entry_Status="等待觸發", Entry_Ready=False)
        refreshed = refresh_comparison_labels(row, intraday=True)
        self.assertEqual(refreshed["Execution_Versions"], ["legacy"])


if __name__ == "__main__":
    unittest.main()
