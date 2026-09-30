from copy import deepcopy
import unittest

from research_decision import build_trade_decision
from research_news import SOURCES
from execution_costs import calculate_max_odd_lot_position


def fixture():
    sizing = calculate_max_odd_lot_position(100, 95, 5000)
    report = {"analysis_date": "2026-09-30", "forecast_date": "2026-10-01",
              "generated_at": "2026-09-30T16:00:00+08:00", "forecast_period_elapsed": False}
    item = {
        "price_date": "2026-09-30", "new_approved": True, "versions": ["new"],
        "original_new_reason": "符合原有條件", "price_alignment": "matched",
        "execution_evidence": {"schema": 3, "ready": True, "status": "現在可執行",
                               "critical_ready": True, "critical_issues": [],
                               "rechecked_status": "現在可執行", "rechecked_reason": "條件通過"},
        "plan": {"status": "ok", "entry_low": 99, "entry_high": 100, "stop": 95,
                 "target": 110, "shares": sizing.shares, "modeled_stop_loss": sizing.estimated_net_loss,
                 "max_modeled_loss": 5000, "net_reward_risk": 999},
        "technical": {"status": "ok", "data_date": "2026-09-30",
                      "daily": {"status": "ok", "trend": "多頭排列"},
                      "weekly": {"status": "ok", "trend": "多頭排列"}},
        "fundamentals": {"Revenue_Status": "ok", "Financial_Status": "ok", "Financial_Risk_Level": "low"},
        "institutional": {"Institutional_Status": "ok"},
        "backtest": {"status": "ok", "metrics": {
            "training": {"samples": 15, "net_profit": 100},
            "validation": {"samples": 5, "net_profit": 50}}},
        "news": {"source_status": {source: {"status": "ok"} for source in SOURCES}, "warnings": [], "events": []},
    }
    return item, report


class ResearchDecisionTests(unittest.TestCase):
    def test_complete_current_approval_is_conditional_not_market_order(self):
        item, report = fixture()
        original = deepcopy((item, report))
        result = build_trade_decision(item, report)
        self.assertEqual(result["code"], "buy")
        self.assertIn("條件式", result["label"])
        self.assertIn("超價不追", result["next_step"])
        self.assertEqual(original, (item, report))

    def test_old_score_and_old_win_rate_can_never_create_buy(self):
        item, report = fixture()
        item.update(new_approved=False, versions=["legacy"], score=99,
                    legacy_evidence={"samples": 1000, "win_rate": 99},
                    original_new_reason="策略回測樣本未達 15 筆，僅列觀察。")
        result = build_trade_decision(item, report)
        self.assertEqual(result["code"], "no_buy")
        self.assertIn("樣本", result["reasons"][0])
        self.assertIn("訓練至少 15", result["next_step"])

    def test_each_missing_or_failed_saved_evidence_blocks_positive_label(self):
        for key, value in (("schema", 2), ("ready", None), ("status", "等待拉回"),
                           ("critical_ready", False), ("critical_issues", ["缺籌碼"]),
                           ("rechecked_status", "等待觸發")):
            item, report = fixture()
            item["execution_evidence"][key] = value
            self.assertEqual(build_trade_decision(item, report)["code"], "no_buy", key)
        item, report = fixture()
        item.pop("execution_evidence")
        self.assertEqual(build_trade_decision(item, report)["code"], "no_buy")

    def test_undated_stale_or_wrong_session_is_not_actionable(self):
        for change in ({"forecast_date": None}, {"forecast_period_elapsed": True},
                       {"forecast_date": "2026-10-02"}, {"generated_at": "2026-10-01T13:30:00+08:00"},
                       {"generated_at": "2026-10-02T08:00:00+08:00"},
                       {"generated_at": "2026-09-30T16:00:00"},
                       {"generated_at": "2026-09-29T16:00:00+08:00"}):
            item, report = fixture()
            report.update(change)
            self.assertEqual(build_trade_decision(item, report)["code"], "no_buy", change)
        item, report = fixture()
        item["price_date"] = "2026-09-29"
        self.assertEqual(build_trade_decision(item, report)["code"], "no_buy")

    def test_valid_session_before_close_still_requires_live_recheck(self):
        item, report = fixture()
        report["generated_at"] = "2026-10-01T04:00:00+00:00"  # Taiwan noon.
        result = build_trade_decision(item, report)
        self.assertEqual(result["code"], "buy")
        self.assertIn("開盤價量", result["next_step"])

    def test_risk_is_recomputed_not_trusted_from_text_or_cached_ratio(self):
        for key, value in (("target", 103), ("stop", 101), ("shares", 999999),
                           ("shares", 1.2), ("entry_low", None), ("entry_high", float("nan"))):
            item, report = fixture()
            item["plan"][key] = value
            self.assertEqual(build_trade_decision(item, report)["code"], "no_buy", key)

    def test_missing_mismatched_or_weak_technicals_block(self):
        for alignment in ("unavailable", "price_adjustment_mismatch", "stale_history"):
            item, report = fixture()
            item["price_alignment"] = alignment
            self.assertEqual(build_trade_decision(item, report)["code"], "no_buy")
        for unit in ("daily", "weekly"):
            item, report = fixture()
            item["technical"][unit]["trend"] = "短線轉弱"
            self.assertEqual(build_trade_decision(item, report)["code"], "no_buy")
            item["technical"][unit] = {}
            self.assertEqual(build_trade_decision(item, report)["code"], "no_buy")

    def test_missing_high_risk_fundamentals_or_chips_block(self):
        for key, value in (("Financial_Status", "stale"), ("Revenue_Status", None),
                           ("Financial_Risk_Level", "high"), ("Financial_Risk_Level", "unknown")):
            item, report = fixture()
            item["fundamentals"][key] = value
            self.assertEqual(build_trade_decision(item, report)["code"], "no_buy", key)
        item, report = fixture()
        item["institutional"]["Institutional_Status"] = "partial"
        self.assertEqual(build_trade_decision(item, report)["code"], "no_buy")

    def test_current_research_without_evidence_cannot_signal_buy(self):
        item, report = fixture()
        item["backtest"]["status"] = "unavailable"
        self.assertEqual(build_trade_decision(item, report)["code"], "no_buy")
        for phase in ("training", "validation"):
            for key, value in (("samples", 0), ("net_profit", None), ("net_profit", -1)):
                item, report = fixture()
                item["backtest"]["metrics"][phase][key] = value
                self.assertEqual(build_trade_decision(item, report)["code"], "no_buy")

    def test_news_failure_and_unreviewed_event_are_not_good_news(self):
        for changes in ({"warnings": ["來源失敗"]}, {"source_status": {}},
                        {"events": [{"title": "擴大投資"}]}):
            item, report = fixture()
            item["news"].update(changes)
            result = build_trade_decision(item, report)
            self.assertEqual(result["code"], "no_buy")
            self.assertTrue(any("公告" in reason for reason in result["reasons"]))

    def test_only_two_reasons_with_full_underlying_research_preserved(self):
        item, report = fixture()
        item.update(new_approved=False, versions=["legacy"], price_alignment="unavailable")
        item["plan"]["status"] = "unavailable"
        item["backtest"]["status"] = "unavailable"
        result = build_trade_decision(item, report)
        self.assertEqual(len(result["reasons"]), 2)


if __name__ == "__main__":
    unittest.main()
