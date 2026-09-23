from copy import deepcopy
import io
import unittest
from unittest.mock import patch

import pandas as pd
from PIL import Image

import scanner
from ranking_comparison import build_comparison_rows, comparison_display_record
from top10_telegram import build_executable_display_rows, render_executable_image, send_executable_photo
from ui_components import generate_cards_html


def record(ticker="2330", **updates):
    row = {
        "代號": ticker, "名稱": "測試股票", "產業": "電子科技", "Score": 80,
        "收盤價": 100.5, "最高價": 101, "20MA": 100, "ATR": 4,
        "BB_UP": 120, "RSI": 55, "BIAS": 0.5, "漲跌幅": 1,
        "Confidence": 90, "Volume_Confirmed": True, "Est_Vol_Ratio": 1.3,
        "Signal_Conflict": "低", "Entry_Pattern": "一般觀察型",
        "Entry_Status": "等待觸發", "Entry_Ready": False,
        "Entry_Status_Group": "wait", "Entry_Reason": "樣本不足 <script>bad</script>",
        "Entry_Low": 100, "Entry_High": 100.5, "Entry_Stop": 96,
        "Entry_Target": 108.5, "Entry_Net_RRR": 1.35,
        "WinRate": 56.1, "Backtest_Samples": 1, "Validation_Samples": 0,
    }
    row.update(updates)
    return row


class ComparisonIntegrationTests(unittest.TestCase):
    def test_html_prices_badges_and_rejection_do_not_mutate_navigation_records(self):
        source = [record(), record("2454", Entry_Status="現在可執行", Entry_Ready=True)]
        rows = build_comparison_rows(source)
        before = deepcopy(rows)
        frame = pd.DataFrame(rows)
        html = generate_cards_html(frame, safe_num=lambda value, default=0: float(value or default))
        self.assertEqual(rows, before)
        self.assertEqual(frame.to_dict("records"), before)
        self.assertIn("測試股票<span", html)
        self.assertIn("【舊制】", html)
        self.assertIn("【新制・舊制】", html)
        self.assertIn("100–102", html)
        self.assertIn("100–100.5", html)
        self.assertIn("樣本不足 &lt;script&gt;bad&lt;/script&gt;", html)
        self.assertNotIn("<script>bad</script>", html)
        self.assertIn("勝率／樣本採 9/15 舊制回測", html)
        self.assertEqual(rows[0]["Entry_High"], 100.5)

    def test_new_only_badge_and_noncomparison_nan_badges(self):
        rows = build_comparison_rows([record(Entry_Status="現在可執行", Entry_Ready=True, RSI=80)])
        frame = pd.DataFrame(rows + [record("2454")])
        html = generate_cards_html(frame, safe_num=lambda value, default=0: float(value or default))
        self.assertIn("【新制】", html)
        self.assertNotIn("【nan】", html)

    def test_legacy_canonical_and_display_copies_cannot_enter_official_top10(self):
        rows = build_comparison_rows([record()])
        display_rows = [comparison_display_record(row) for row in rows]
        self.assertEqual(scanner.select_executable_top10(rows), [])
        self.assertEqual(scanner.select_executable_top10(display_rows), [])
        self.assertEqual(scanner.build_top10_history_rows(scanner.select_executable_top10(rows)), [])

    def test_telegram_comparison_shows_versions_without_approving_legacy(self):
        source = [record(), record("2454", Entry_Status="現在可執行", Entry_Ready=True)]
        union = build_comparison_rows(source)
        before = deepcopy(union)
        rows = build_executable_display_rows(union, comparison=True)
        self.assertEqual([row["version_label"] for row in rows], ["舊制", "新制・舊制"])
        self.assertEqual(rows[0]["entry_zone_text"], "100–102")
        self.assertEqual(rows[1]["entry_zone_text"], "100–100.5")
        self.assertIn("樣本不足", rows[0]["analysis_lines"][1])
        self.assertEqual(rows[0]["win_rate_text"], "--")
        self.assertEqual(rows[0]["sample_breakdown_text"], "舊制待回補")
        self.assertLessEqual(rows[0]["estimated_loss"], 5000)
        self.assertEqual(union, before)
        self.assertEqual(len(build_executable_display_rows(union)), 1)

    def test_twenty_union_rows_are_not_truncated_and_fit_in_image(self):
        source = []
        for i in range(10):
            source.append(record(str(1000 + i), RSI=80, 產業=f"新產業{i}", Entry_Status="現在可執行", Entry_Ready=True))
            source.append(record(str(2000 + i), 產業="舊產業"))
        union = build_comparison_rows(source)
        self.assertEqual(len(union), 20)
        rows = build_executable_display_rows(union, comparison=True)
        self.assertEqual(len(rows), 20)
        with Image.open(io.BytesIO(render_executable_image(source, "2026-09-21", comparison_results=union))) as image:
            self.assertGreater(image.height, 3000)
            self.assertEqual(image.width, 1080)

    def test_explicit_empty_comparison_does_not_fall_back_to_other_names(self):
        with patch("top10_telegram._send_document_bytes", return_value=42) as send:
            result = send_executable_photo([record()], "2026-09-21", "token", "chat", comparison_results=[])
        self.assertEqual(result, 42)
        self.assertIn("新制 0、舊制 0", send.call_args.args[2])
        self.assertIn("不納入新制自動追蹤", send.call_args.args[2])

    def test_industry_limited_new_approval_is_not_falsely_called_a_rule_failure(self):
        rows = build_comparison_rows([
            record(str(2000 + i), Entry_Status="現在可執行", Entry_Ready=True)
            for i in range(3)
        ])
        self.assertEqual(rows[-1]["Execution_Versions"], ["legacy"])
        display = build_executable_display_rows(rows, comparison=True)
        self.assertIn("產業限額", display[-1]["analysis_lines"][1])
        self.assertEqual(len(scanner.select_executable_top10(rows)), 2)

    def test_scanner_sends_comparison_but_keeps_official_selection_empty(self):
        from test_scanner_telegram import _Database

        database = _Database()
        source = [record()]
        with (
            patch.object(scanner, "db", database),
            patch.object(scanner, "_telegram_credentials", return_value=("token", "chat")),
            patch.object(scanner, "send_executable_photo", return_value=42) as send,
        ):
            self.assertTrue(scanner.send_daily_executable_notification(source, "2026-09-21"))
            self.assertFalse(scanner.send_daily_executable_notification(source, "2026-09-21"))
            changed = [dict(source[0], Entry_Reason="新制財報風險")]
            self.assertTrue(scanner.send_daily_executable_notification(changed, "2026-09-21"))
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args.kwargs["selected_results"], [])
        self.assertEqual(len(send.call_args.kwargs["comparison_results"]), 1)
        saved = database.collection("notifications").document("daily_executable_2026-09-21").value
        self.assertEqual(saved["executable_count"], 0)
        self.assertEqual(saved["legacy_count"], 1)
        self.assertEqual(saved["comparison_count"], 1)


if __name__ == "__main__":
    unittest.main()
