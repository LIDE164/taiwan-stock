import ast
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pandas as pd

from app_security import build_stock_url, escape_html, normalize_ticker
from backtest_reporting import (
    backtest_record_fields,
    primary_backtest_display,
    reconcile_intraday_evidence,
    replace_backtest_snapshot,
)
from scan_state import latest_trading_date
from ui_components import generate_cards_html

SOURCE_PATH = Path(__file__).resolve().parents[1] / "test.py"
SOURCE_TREE = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))


def legacy_record(date="2026-09-21"):
    return {
        "代號": "2330", "名稱": "台積電", "Data_Date": date, "Score": 78,
        "收盤價": 100, "WinRate": 56.1, "Backtest_Samples": 1,
        "Validation_Samples": 1, "Backtest_Overall_Samples": 2,
        "Backtest_Scope": "新制成交規則",
        "Legacy_Backtest": {
            "schema": "legacy_2026_09_15",
            "source_commit": "027f459fdea2a79d04c77979f74c0b936cdbe370",
            "status": "complete", "as_of_date": date, "data_through": date,
            "samples": 24, "wins": 15, "losses": 9, "win_rate": 59.3,
        },
    }


class LegacyIntradayTests(unittest.TestCase):
    def test_live_price_date_preserves_validated_prior_close_evidence(self):
        baseline = legacy_record()
        original = deepcopy(baseline)
        result = reconcile_intraday_evidence(baseline, {
            "Data_Date": "2026-09-22", "Score_Mode_Raw": "realtime",
            "WinRate": 90, "Backtest_Samples": 100,
        })
        self.assertEqual(result["Data_Date"], "2026-09-22")
        self.assertEqual(result["Legacy_Backtest_As_Of_Date"], "2026-09-21")
        self.assertEqual((result["WinRate"], result["Backtest_Samples"]), (56.1, 1))
        self.assertEqual(primary_backtest_display(result)["win_rate"], 59.3)
        result["Legacy_Backtest"]["wins"] = 0
        self.assertEqual(baseline, original)

    def test_repeated_live_reconciliation_keeps_original_evidence_date(self):
        first = reconcile_intraday_evidence(legacy_record(), {"Data_Date": "2026-09-22"})
        result = reconcile_intraday_evidence(first, {"Data_Date": "2026-09-22", "Score": 81})
        self.assertEqual(result["Legacy_Backtest_As_Of_Date"], "2026-09-21")
        self.assertTrue(primary_backtest_display(result)["available"])

    def test_stale_snapshot_without_provenance_is_unavailable(self):
        row = legacy_record()
        row["Data_Date"] = "2026-09-22"
        self.assertFalse(primary_backtest_display(row)["available"])
        replacement = legacy_record("2026-09-22")
        replacement["Legacy_Backtest_As_Of_Date"] = "2026-09-22"
        replace_backtest_snapshot(replacement, row)
        self.assertNotIn("Legacy_Backtest", replacement)
        self.assertNotIn("Legacy_Backtest_As_Of_Date", replacement)
        self.assertEqual(replacement["WinRate"], 56.1)

    def test_invalid_source_cannot_become_valid_by_copying_to_its_snapshot_date(self):
        source = legacy_record("2026-09-22")
        source["Data_Date"] = "2026-09-21"
        destination = {"Data_Date": "2026-09-22"}
        replace_backtest_snapshot(destination, source)
        self.assertFalse(primary_backtest_display(destination)["available"])

    def test_mismatched_future_and_invalid_dates_remain_unavailable(self):
        cases = (
            {"Data_Date": "2026-09-22", "Legacy_Backtest_As_Of_Date": "2026-09-20"},
            {"Data_Date": "2026-09-20", "Legacy_Backtest_As_Of_Date": "2026-09-21"},
            {"Data_Date": "2026-02-31"},
            {"Legacy_Backtest_As_Of_Date": ""},
        )
        for updates in cases:
            with self.subTest(updates=updates):
                row = legacy_record()
                row.update(updates)
                self.assertFalse(primary_backtest_display(row)["available"])
        for bad_date in ("2026-02-31", "20260921", "2026-9-21"):
            row = legacy_record(bad_date)
            self.assertFalse(primary_backtest_display(row)["available"])

    @staticmethod
    def run_analysis_backtest_branch(intraday, cached_doc=None):
        """Run the actual analysis evidence branch without importing the Streamlit app."""
        analysis = next(node for node in SOURCE_TREE.body
                        if isinstance(node, ast.FunctionDef) and node.name == "analyze_today")
        branch = next(node for node in analysis.body if isinstance(node, ast.If)
                      and ast.unparse(node.test) == "effective_intraday and cached_doc")
        frame = pd.DataFrame({"Close": [100.0, 101.0, 150.0]},
                             index=pd.to_datetime(["2026-09-18", "2026-09-21", "2026-09-22"]))
        current_calculation = Mock(return_value={"win_rate": 56.1, "closed_signals": 1})
        legacy_calculation = Mock(return_value=deepcopy(legacy_record()["Legacy_Backtest"]))
        namespace = {
            "effective_intraday": intraday, "cached_doc": cached_doc, "df": frame,
            "data": {"Data_Date": "2026-09-22"}, "pd": pd,
            "BACKTEST_LOOKBACK_DAYS": 380,
            "calculate_historical_performance": current_calculation,
            "calculate_legacy_backtest": legacy_calculation,
            "latest_trading_date": latest_trading_date,
            "backtest_record_fields": backtest_record_fields,
            "replace_backtest_snapshot": replace_backtest_snapshot,
        }
        module = ast.Module(body=[branch], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), str(SOURCE_PATH), "exec"), namespace)  # noqa: S102 - trusted local function
        return namespace, current_calculation, legacy_calculation

    def test_no_intraday_baseline_excludes_live_bar_only_from_legacy_calculation(self):
        namespace, current, legacy = self.run_analysis_backtest_branch(True)
        self.assertIs(current.call_args.args[0], namespace["df"])
        self.assertEqual(len(current.call_args.args[0]), 3)
        self.assertEqual(list(legacy.call_args.args[0]["Close"]), [100.0, 101.0])
        self.assertEqual(legacy.call_args.kwargs, {"as_of_date": "2026-09-21"})
        self.assertEqual(namespace["data"]["Data_Date"], "2026-09-22")
        self.assertEqual(namespace["data"]["Legacy_Backtest_As_Of_Date"], "2026-09-21")
        self.assertTrue(primary_backtest_display(namespace["data"])["available"])

    def test_postclose_calculation_keeps_latest_completed_bar(self):
        namespace, current, legacy = self.run_analysis_backtest_branch(False)
        self.assertIs(current.call_args.args[0], namespace["df"])
        self.assertIs(legacy.call_args.args[0], namespace["df"])
        self.assertEqual(legacy.call_args.kwargs, {"as_of_date": "2026-09-22"})

    def test_intraday_baseline_is_copied_without_recalculation(self):
        namespace, current, legacy = self.run_analysis_backtest_branch(True, legacy_record())
        current.assert_not_called()
        legacy.assert_not_called()
        self.assertEqual(namespace["data"]["Legacy_Backtest_As_Of_Date"], "2026-09-21")
        self.assertTrue(primary_backtest_display(namespace["data"])["available"])

    def test_fallback_renderer_uses_legacy_evidence_and_keeps_new_samples_labeled(self):
        fallback = next(node for node in ast.walk(SOURCE_TREE)
                        if isinstance(node, ast.FunctionDef) and node.name == "build_cards_html")
        namespace = {
            "primary_backtest_display": primary_backtest_display,
            "normalize_ticker": normalize_ticker, "escape_html": escape_html,
            "build_stock_url": build_stock_url,
        }
        module = ast.Module(body=[fallback], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), str(SOURCE_PATH), "exec"), namespace)  # noqa: S102 - trusted local function
        renderer = namespace["build_cards_html"]
        html = renderer(pd.DataFrame([legacy_record()]))
        self.assertIn("舊制技術回測 59.3%", html)
        self.assertIn("舊樣本 24", html)
        self.assertIn("新制：全期 2｜訓練 1｜驗證 1", html)
        self.assertNotIn("56.1%", html)
        row = legacy_record()
        row.pop("Legacy_Backtest")
        html = renderer(pd.DataFrame([row]))
        self.assertIn("舊制技術回測 --", html)
        self.assertIn("舊制待回補", html)
        self.assertNotIn("56.1%", html)

    def test_current_scope_is_labeled_separately_from_legacy_headline(self):
        html = generate_cards_html(pd.DataFrame([legacy_record()]),
                                   safe_num=lambda value, default=0: float(value or default))
        self.assertIn("59.3%", html)
        self.assertIn("新制回測：新制成交規則", html)

    def test_legacy_sort_matches_display_without_overwriting_current_evidence(self):
        lower, higher, missing = (legacy_record() for _ in range(3))
        lower.update({"代號": "1111", "WinRate": 99, "漲跌幅": 0})
        higher.update({"代號": "2222", "WinRate": 10, "漲跌幅": 0})
        higher["Legacy_Backtest"]["win_rate"] = 70
        missing.update({"代號": "3333", "WinRate": 100, "漲跌幅": 0})
        missing.pop("Legacy_Backtest")
        frame = pd.DataFrame([lower, higher, missing])
        original = frame.copy(deep=True)
        sort_block = None
        for node in ast.walk(SOURCE_TREE):
            body = getattr(node, "body", None)
            if not isinstance(body, list):
                continue
            for index, statement in enumerate(body):
                if isinstance(statement, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "sort_map"
                    for target in statement.targets
                ):
                    end = next(end for end in range(index, len(body))
                               if isinstance(body[end], ast.Assign) and any(
                                   isinstance(target, ast.Name) and target.id == "df_disp"
                                   for target in body[end].targets))
                    sort_block = body[index:end + 1]
                    break
        self.assertIsNotNone(sort_block)
        namespace = {
            "df_results": frame, "sort_mode": "舊制技術勝率", "is_comparison_view": False,
            "primary_backtest_display": primary_backtest_display,
        }
        module = ast.Module(body=sort_block, type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), str(SOURCE_PATH), "exec"), namespace)  # noqa: S102 - trusted local sorting block
        result = namespace["df_disp"]
        self.assertEqual(list(result["代號"]), ["2222", "1111", "3333"])
        self.assertEqual(list(result["WinRate"]), [10, 99, 100])
        self.assertNotIn("_legacy_display_winrate", result.columns)
        pd.testing.assert_frame_equal(frame, original)


if __name__ == "__main__":
    unittest.main()
