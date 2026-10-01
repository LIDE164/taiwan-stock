from copy import deepcopy
import unittest
from unittest.mock import patch

from research_summary import MAX_MESSAGE_UNITS, format_compact_research_messages


def item(ticker="2330", **changes):
    return {
        "ticker": ticker, "name": "測試股票", "versions": ["new"], "new_approved": True,
        "plan": {"status": "ok", "entry_low": 100, "entry_high": 101, "stop": 95, "target": 115,
                 "shares": 777, "modeled_stop_loss": 4998.25, "max_modeled_loss": 5000},
        "analysis_url": f"https://example.test/?stock={ticker}",
        "legacy_evidence": {"win_rate": 87.654321, "samples": 51},
        **changes,
    }


def report(items=None, **changes):
    return {"schema": "daily_executable_research_v1", "analysis_date": "2026-09-29",
            "forecast_date": "2026-09-30", "forecast_period_elapsed": False,
            "items": [item()] if items is None else items, **changes}


FACETS = {
    "technical": {"advantages": ["收盤站上 20MA"], "risks": ["週線動能仍弱"]},
    "institutional": {"advantages": ["近 3 日法人合計買超 100 張"], "risks": ["合計值不代表每日連買"]},
    "fundamental": {"advantages": ["8 月營收年增 20%"], "risks": ["8 月營收月減 2%"]},
}


class ResearchSummaryTests(unittest.TestCase):
    def setUp(self):
        self.patcher = patch("research_summary.build_research_facets", return_value=deepcopy(FACETS))
        self.facets = self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_three_facets_are_short_dated_and_self_contained(self):
        payload = report()
        messages = format_compact_research_messages(payload)
        self.assertEqual(len(messages), 2)
        self.assertEqual(len(messages[0].splitlines()), 6)
        self.assertEqual(len(messages[1].splitlines()), 6)
        self.assertTrue(messages[1].startswith("不買｜2330 測試股票｜新制"))
        for label, text in (("技術面", "收盤站上 20MA"), ("籌碼面", "買超 100 張"),
                            ("基本面", "8 月營收月減 2%")):
            self.assertIn(label + "｜優點：", messages[1])
            self.assertIn(text, messages[1])
        self.assertEqual(messages[1].count("缺點／限制："), 3)
        self.assertIn("分析 2026-09-29｜適用 2026-09-30", messages[1])
        self.assertIn("非即時", messages[1])
        self.assertIn("5,000", messages[0])
        self.assertIn("跳空", messages[0])
        self.facets.assert_called_once_with(payload["items"][0], payload)

    def test_old_generic_reasons_replaced_without_repeating_order_instructions(self):
        content = "\n".join(format_compact_research_messages(report()))
        for forbidden in ("原因：", "下一步：", "777 股",
                          "限價區", "停損 95", "目標 115", "4,998.25"):
            self.assertNotIn(forbidden, content)

    def test_existing_buy_conclusion_remains_with_three_facet_reasons(self):
        with patch("research_summary.build_trade_decision", return_value={"code": "buy"}):
            messages = format_compact_research_messages(report())
            self.assertTrue(messages[1].startswith("買（限價、條件式）｜"))
            self.assertIn("條件式買 1／不買 0", messages[0])
            self.assertEqual(messages[1].count("缺點／限制："), 3)

    def test_expired_and_undated_input_cannot_keep_buy_conclusion(self):
        with patch("research_summary.build_trade_decision", return_value={"code": "buy"}):
            for changes in ({"forecast_period_elapsed": True}, {"forecast_date": None}):
                self.assertTrue(format_compact_research_messages(report(**changes))[1].startswith("不買｜"))

    def test_invalid_or_failed_decision_does_not_create_buy(self):
        for value in (None, {}, {"code": "unknown"}):
            with patch("research_summary.build_trade_decision", return_value=value):
                self.assertTrue(format_compact_research_messages(report())[1].startswith("不買｜"))
        with patch("research_summary.build_trade_decision", side_effect=ValueError("bad observation")):
            self.assertTrue(format_compact_research_messages(report())[1].startswith("不買｜"))

    def test_invalid_price_or_risk_plan_cannot_keep_buy_label(self):
        with patch("research_summary.build_trade_decision", return_value={"code": "buy"}):
            for key, value in (("shares", 0), ("shares", 1.5), ("stop", 110), ("target", True),
                               ("modeled_stop_loss", 5001), ("modeled_stop_loss", float("nan")),
                               ("max_modeled_loss", None)):
                payload = report()
                payload["items"][0]["plan"][key] = value
                self.assertTrue(format_compact_research_messages(payload)[1].startswith("不買｜"))

    def test_no_verbose_six_sections_or_precision_win_rate(self):
        joined = "\n".join(format_compact_research_messages(report()))
        for forbidden in ("87.654321", "1. 交易計畫", "2. 日／週線", "獲利因子", "08:30", "09:15"):
            self.assertNotIn(forbidden, joined)
        self.assertIn("候選並非實際持倉", joined)
        self.assertIn("解析（開啟為最新頁面）", joined)

    def test_twenty_candidates_remain_separate_and_bounded_unicode(self):
        self.facets.return_value = {key: {"advantages": ["🧪" * 10000] * 3, "risks": ["🧪" * 10000] * 3}
                                    for key in FACETS}
        items = [item(str(1100 + i), name="🧪" * 10000, analysis_url="https://example.test/" + "🧪" * 10000)
                 for i in range(20)]
        messages = format_compact_research_messages(report(items))
        self.assertEqual(len(messages), 21)
        self.assertEqual(self.facets.call_count, 20)
        for index, message in enumerate(messages):
            self.assertLessEqual(len(message.encode("utf-16-le")) // 2, MAX_MESSAGE_UNITS)
            if index:
                self.assertEqual(message.count("優點："), 3)
                self.assertEqual(message.count("缺點／限制："), 3)
                self.assertIn(str(1099 + index), message)

    def test_expired_reports_are_marked_in_every_stock_message(self):
        messages = format_compact_research_messages(report(forecast_period_elapsed=True))
        self.assertIn("名單已過期", messages[0])
        self.assertIn("已過期，僅供回顧", messages[1])
        self.assertNotIn("777 股", "\n".join(messages))

    def test_missing_or_invalid_dates_are_explicit(self):
        for changes in ({"forecast_date": None}, {"forecast_date": "oops"}, {"analysis_date": None}):
            with self.subTest(changes=changes):
                messages = format_compact_research_messages(report(**changes))
                self.assertIn("日期未確認", messages[0])
                self.assertIn("日期未確認，不作進場依據", messages[1])

    def test_no_candidates_has_no_invented_stock(self):
        messages = format_compact_research_messages(report([]))
        self.assertEqual(len(messages), 1)
        self.assertIn("沒有符合名單；不另補股票", messages[0])
        self.facets.assert_not_called()

    def test_report_and_original_plan_are_not_mutated(self):
        payload = report([item(), item("2317", versions=["legacy", "new"])])
        frozen = deepcopy(payload)
        messages = format_compact_research_messages(payload)
        self.assertEqual(payload, frozen)
        self.assertIn("新制 2、舊制 1", messages[0])
        self.assertIn("新制＋舊制", messages[2])

    def test_legacy_is_not_promoted_by_positive_facts(self):
        messages = format_compact_research_messages(report([item(versions=["legacy"], new_approved=False)]))
        self.assertIn("舊制比較", messages[1])
        self.assertIn("新制 0、舊制 1", messages[0])
        self.assertIn("原榜單資格與風控不變", messages[0])

    def test_empty_advantages_are_explicit_not_invented(self):
        self.facets.return_value = {key: {"advantages": [], "risks": ["資料不足"]} for key in FACETS}
        messages = format_compact_research_messages(report([item(analysis_url=None)]))
        self.assertEqual(messages[1].count("暫無明確優點"), 3)
        self.assertEqual(messages[1].count("資料不足"), 3)
        self.assertIn("連結未提供", messages[1])

    def test_missing_version_is_not_assumed_new(self):
        messages = format_compact_research_messages(report([item(versions=None)]))
        self.assertIn("制度未確認", messages[1])
        self.assertIn("新制 0、舊制 0", messages[0])

    def test_unsupported_schema_and_broken_items_rejected(self):
        for payload in (report(schema="future"), report(items="invalid"), report(items=[None])):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    format_compact_research_messages(payload)


if __name__ == "__main__":
    unittest.main()
