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


BUY = {"code": "buy", "label": "買（限價、條件式）",
       "reasons": ["新制條件通過，價位與風險可核對。", "成本後報酬風險合格。"],
       "next_step": "僅在 100–101 限價區內重新確認，不追價。"}
NO_BUY = {"code": "no_buy", "label": "不買", "reasons": ["僅舊制比較，新制尚未通過。"],
          "next_step": "等待新制通過後再看，不因舊制入榜買進。"}


class ResearchSummaryTests(unittest.TestCase):
    def setUp(self):
        self.patcher = patch("research_summary.build_trade_decision", return_value=deepcopy(BUY))
        self.decision = self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_short_buy_conclusion_with_complete_risk_plan(self):
        payload = report()
        messages = format_compact_research_messages(payload)
        self.assertEqual(len(messages), 2)
        self.assertEqual(len(messages[0].splitlines()), 6)
        self.assertLessEqual(len(messages[1].splitlines()), 9)
        self.assertTrue(messages[1].startswith("買（限價、條件式）｜2330"))
        for text in ("限價區 100–101", "停損 95", "目標 115", "777 股", "4,998.25", "依區間上限"):
            self.assertIn(text, messages[1])
        self.assertIn("分析日 2026-09-29｜適用日 2026-09-30", messages[0])
        self.assertIn("非即時", messages[0])
        self.assertIn("5,000", messages[0])
        self.assertIn("跳空", messages[0])
        self.decision.assert_called_once_with(payload["items"][0], payload)

    def test_no_buy_omits_order_prices_and_quantities(self):
        self.decision.return_value = deepcopy(NO_BUY)
        messages = format_compact_research_messages(report([item(versions=["legacy"], new_approved=False)]))
        self.assertTrue(messages[1].startswith("不買｜2330"))
        self.assertIn("舊制比較", messages[1])
        self.assertIn("等待新制通過", messages[1])
        self.assertIn("條件式買 0 檔／不買 1 檔", messages[0])
        for forbidden in ("777 股", "限價區", "停損 95", "目標 115", "4,998.25"):
            self.assertNotIn(forbidden, messages[1])

    def test_no_verbose_six_sections_or_precision_win_rate(self):
        joined = "\n".join(format_compact_research_messages(report()))
        for forbidden in ("87.654321", "1. 交易計畫", "2. 日／週線", "獲利因子", "08:30", "09:15"):
            self.assertNotIn(forbidden, joined)
        self.assertIn("候選並非實際持倉", joined)
        self.assertIn("解析（開啟為最新頁面）", joined)

    def test_twenty_candidates_remain_separate_and_bounded_unicode(self):
        self.decision.return_value = {**BUY, "reasons": ["🧪" * 10000] * 3, "next_step": "🧪" * 10000}
        items = [item(str(1100 + i), name="🧪" * 10000, analysis_url="https://example.test/" + "🧪" * 10000)
                 for i in range(20)]
        messages = format_compact_research_messages(report(items))
        self.assertEqual(len(messages), 21)
        self.assertEqual(self.decision.call_count, 20)
        for index, message in enumerate(messages):
            self.assertLessEqual(len(message.encode("utf-16-le")) // 2, MAX_MESSAGE_UNITS)
            if index:
                self.assertEqual(message.count("原因："), 2)
                self.assertIn(str(1099 + index), message)

    def test_expired_report_is_explicitly_no_buy_even_if_bad_decision_says_buy(self):
        messages = format_compact_research_messages(report(forecast_period_elapsed=True))
        self.assertIn("名單已過期", messages[0])
        self.assertIn("全部不買", messages[0])
        self.assertTrue(messages[1].startswith("不買｜"))
        self.assertIn("等待最新盤後榜單", messages[1])
        self.assertNotIn("777 股", "\n".join(messages))

    def test_missing_or_invalid_dates_do_not_create_actionable_message(self):
        for changes in ({"forecast_date": None}, {"forecast_date": "oops"}, {"analysis_date": None}):
            with self.subTest(changes=changes):
                messages = format_compact_research_messages(report(**changes))
                self.assertIn("日期未確認", messages[0])
                self.assertTrue(messages[1].startswith("不買｜"))
                self.assertNotIn("777 股", messages[1])

    def test_no_candidates_has_no_invented_stock_or_buy_call(self):
        messages = format_compact_research_messages(report([]))
        self.assertEqual(len(messages), 1)
        self.assertIn("沒有符合名單：不買", messages[0])
        self.decision.assert_not_called()

    def test_missing_or_invalid_order_fields_force_no_buy(self):
        for key, value in (("shares", 0), ("shares", 1.5), ("stop", 110),
                           ("modeled_stop_loss", 5001), ("modeled_stop_loss", float("nan")),
                           ("max_modeled_loss", None), ("target", True)):
            with self.subTest(key=key, value=value):
                payload = report()
                payload["items"][0]["plan"][key] = value
                messages = format_compact_research_messages(payload)
                self.assertTrue(messages[1].startswith("不買｜"))
                self.assertIn("資料不完整", messages[1])
                self.assertNotIn("股數上限", messages[1])

    def test_report_is_not_mutated(self):
        payload = report([item(), item("2317", versions=["legacy", "new"])])
        frozen = deepcopy(payload)
        messages = format_compact_research_messages(payload)
        self.assertEqual(payload, frozen)
        self.assertIn("新制 2、舊制 1", messages[0])
        self.assertIn("新制＋舊制", messages[2])

    def test_unknown_decision_and_missing_text_are_explicit_not_blank(self):
        self.decision.return_value = {"code": "unknown", "reasons": None}
        messages = format_compact_research_messages(report([item(analysis_url=None)]))
        self.assertTrue(messages[1].startswith("不買｜"))
        self.assertIn("資訊不足", messages[1])
        self.assertIn("連結未提供", messages[1])

    def test_missing_buy_reason_does_not_print_contradictory_buy_and_insufficient_data(self):
        self.decision.return_value = {**BUY, "reasons": []}
        messages = format_compact_research_messages(report())
        self.assertTrue(messages[1].startswith("不買｜"))
        self.assertIn("買進理由未提供", messages[1])
        self.assertNotIn("股數上限", messages[1])

    def test_missing_version_is_explicit_not_assumed_new(self):
        self.decision.return_value = deepcopy(NO_BUY)
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
