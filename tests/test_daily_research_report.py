from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from daily_research_report import (
    MAX_MESSAGE_UNITS, _candidate_correlations, _split_units,
    build_daily_research_report, format_detailed_research_messages as format_research_messages,
)
from execution_costs import estimate_stop_loss
from research_news import SOURCES


DAY = "2026-09-24"
NOW = datetime(2026, 9, 29, 10, tzinfo=timezone(timedelta(hours=8)))


def record(ticker="2330", **changes):
    return {
        "代號": ticker, "名稱": "測試用股票", "產業": "半導體", "Score": 80,
        "Data_Date": DAY, "收盤價": 100.5, "最高價": 101, "20MA": 100, "ATR": 4,
        "BB_UP": 120, "RSI": 55, "BIAS": 0.5, "漲跌幅": 1, "Confidence": 90,
        "Volume_Confirmed": True, "Est_Vol_Ratio": 1.3, "Signal_Conflict": "低",
        "Entry_Pattern": "一般觀察型", "Entry_Status": "等待觸發", "Entry_Ready": False,
        "Entry_Status_Group": "wait", "Entry_Reason": "新制策略回測樣本未達門檻",
        "Entry_Low": 100, "Entry_High": 100.5, "Entry_Stop": 96, "Entry_Target": 108.5,
        "WinRate": 56.1, "Backtest_Samples": 1, "Validation_Samples": 0, **changes,
    }


def history(through=DAY, count=330):
    close = 100 + np.sin(np.arange(count) / 11) * 4
    close += 100.5 - close[-1]
    return pd.DataFrame({"Open": close - .1, "High": close + 1, "Low": close - 1,
                         "Close": close, "Volume": 1000},
                        index=pd.bdate_range(end=through, periods=count))


def news(ticker, now):
    return {"as_of": now.isoformat(), "events": [], "source_status": {
        source: {"status": "ok", "source_url": url} for source, url in SOURCES.items()}}


def backtest(*args, **kwargs):
    def metric(count):
        return {"samples": count, "win_rate_pct": 60 if count >= 30 else None,
                "raw_win_rate_pct": 60, "profit_factor": 1.2,
                "max_drawdown_closed_trade_pct": 3.2}
    return {"status": "ok", "metrics": {"overall": metric(8), "training": metric(6), "validation": metric(2)},
            "trades": [{"lots_of_history": True}]}


class DailyResearchReportTests(unittest.TestCase):
    def setUp(self):
        self.patcher = patch("daily_research_report.run_research_backtest", side_effect=backtest)
        self.backtest = self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.calendar = patch("daily_research_report._checklist", return_value={
            "date": "2026-09-29", "items": [], "notice": "測試日曆"}).start()
        self.addCleanup(patch.stopall)

    def build(self, rows=None, **kwargs):
        return build_daily_research_report(rows if rows is not None else [record()], DAY,
            load_history=kwargs.pop("load_history", lambda ticker: history()),
            load_news=kwargs.pop("load_news", news), now=kwargs.pop("now", NOW), **kwargs)

    def test_all_legacy_candidates_are_researched_without_promotion_or_mutation(self):
        rows = [record("2330"), record("2317", Score=78), record("2454", Score=76)]
        frozen = deepcopy(rows)
        report = self.build(rows)
        self.assertEqual((report["stock_count"], report["new_count"], report["legacy_count"]), (3, 0, 3))
        self.assertTrue(all(item["versions"] == ["legacy"] and not item["new_approved"] for item in report["items"]))
        self.assertEqual(rows, frozen)
        self.assertEqual(self.backtest.call_count, 3)
        self.assertNotIn("trades", report["items"][0]["backtest"])

    def test_union_all_twenty_not_limited_to_five_research_ideas(self):
        rows = []
        for i in range(12):
            rows.append(record(str(1100+i), RSI=80, Entry_Status="現在可執行", Entry_Ready=True,
                               Score=99-i, 產業=f"新產業{i}"))
            rows.append(record(str(2100+i), Score=85-i))
        report = self.build(rows)
        self.assertEqual((report["stock_count"], report["new_count"], report["legacy_count"]), (20, 10, 10))
        self.assertEqual(len({item["ticker"] for item in report["items"]}), 20)

    def test_both_versions_share_one_analysis(self):
        report = self.build([record(Entry_Status="現在可執行", Entry_Ready=True)])
        self.assertEqual(report["stock_count"], 1)
        self.assertEqual(report["items"][0]["versions"], ["new", "legacy"])
        self.assertEqual((report["new_count"], report["legacy_count"]), (1, 1))
        self.assertEqual(self.backtest.call_count, 1)

    def test_default_telegram_view_is_compact_and_uses_three_facets(self):
        from daily_research_report import format_research_messages as default_format
        report = self.build()
        frozen = deepcopy(report)
        messages = default_format(report)
        self.assertIn("精簡版", messages[0])
        self.assertTrue(messages[1].startswith("不買｜2330"))
        for facet in ("技術面", "籌碼面", "基本面"):
            self.assertIn(facet + "｜優點：", messages[1])
        self.assertIn("缺點／限制：", messages[1])
        self.assertNotIn("1. 交易計畫", "\n".join(messages))
        self.assertEqual(report, frozen)

    def test_financial_facet_metadata_is_copied_from_snapshot_without_invention(self):
        row = record(Financial_Expected_Period="2026-Q2", Financial_Risk_Flags=["營業損失"])
        funds = self.build([row])["items"][0]["fundamentals"]
        self.assertEqual(funds["Financial_Expected_Period"], "2026-Q2")
        self.assertEqual(funds["Financial_Risk_Flags"], ["營業損失"])
        missing = self.build()["items"][0]["fundamentals"]
        self.assertIsNone(missing["Financial_Expected_Period"])
        self.assertIsNone(missing["Financial_Risk_Flags"])

    def test_recheck_cannot_promote_incomplete_saved_approval(self):
        from research_decision import build_trade_decision
        report = self.build([record(Entry_Status="現在可執行", Entry_Ready=True,
                                    Entry_Schema=3, Critical_Data_Ready=True)])
        item = report["items"][0]
        self.assertTrue(item["new_approved"])
        self.assertNotEqual(item["execution_evidence"]["rechecked_status"], "現在可執行")
        self.assertEqual(build_trade_decision(item, report)["code"], "no_buy")

    def test_only_same_date_rows_and_no_fabricated_empty_candidates(self):
        loader = Mock()
        report = self.build([record(Data_Date="2026-09-23"), record("2317", Score=10)], load_history=loader)
        self.assertEqual(report["stock_count"], 0)
        self.assertEqual(report["excluded_date_rows"], 1)
        loader.assert_not_called()
        self.assertIn("不硬湊交易機會", format_research_messages(report)[0])

    def test_risk_uses_entry_upper_bound_costs_and_frozen_prices(self):
        report = self.build([record(Entry_Status="現在可執行", Entry_Ready=True)])
        plan = report["items"][0]["plan"]
        self.assertEqual(plan["sizing_entry_price"], 100.5)
        self.assertLessEqual(plan["modeled_stop_loss"], 5000)
        self.assertGreater(estimate_stop_loss(100.5, 96, plan["shares"] + 1).estimated_net_loss, 5000)
        self.assertLess(plan["net_reward_risk"], plan["gross_reward_risk"])

    def test_malformed_plan_explicit_unavailable(self):
        report = self.build([record(Entry_Status="現在可執行", Entry_Ready=True, Entry_Stop=105)])
        self.assertEqual(report["items"][0]["plan"]["status"], "unavailable")
        self.assertIn("不完整或矛盾", "\n".join(format_research_messages(report)))

    def test_no_future_bars_and_preserves_input_history(self):
        frame = history()
        frame.loc[pd.Timestamp("2026-09-29")] = [1000, 1100, 900, 1000, 2000]
        original = frame.copy(deep=True)
        report = self.build(load_history=lambda ticker: frame)
        pd.testing.assert_frame_equal(original, frame)
        passed = self.backtest.call_args.args[0]
        self.assertEqual(passed.index[-1].date().isoformat(), DAY)
        self.assertEqual(report["items"][0]["price_alignment"], "matched")
        self.assertEqual(report["items"][0]["technical"]["weekly"]["date"], "2026-09-18")

    def test_close_revision_is_not_joined_to_saved_plan(self):
        frame = history()
        frame[["Open", "High", "Low", "Close"]] *= .9
        item = self.build(load_history=lambda ticker: frame)["items"][0]
        self.assertEqual(item["price_alignment"], "price_adjustment_mismatch")
        self.assertEqual(item["saved_close"], 100.5)
        self.assertAlmostEqual(item["history_close"], 90.45)
        self.assertTrue(any("不套用到原價格計畫" in warning for warning in item["warnings"]))

    def test_stale_and_invalid_history_never_masked_with_saved_close(self):
        stale = self.build(load_history=lambda ticker: history("2026-09-23"))["items"][0]
        self.assertEqual(stale["price_alignment"], "stale_history")
        frame = history()
        frame.iloc[-1, frame.columns.get_loc("Close")] = float("nan")
        item = self.build(load_history=lambda ticker: frame)["items"][0]
        self.assertEqual(item["price_alignment"], "unavailable")
        self.assertEqual(item["backtest"]["status"], "unavailable")

    def test_loader_failure_never_leaks_secrets_and_continues_other_stocks(self):
        def fail_one(ticker):
            if ticker == "2330":
                raise RuntimeError("https://example.com?token=SECRET")
            return history()
        report = self.build([record(), record("2317")], load_history=fail_one)
        self.assertEqual(report["items"][0]["technical"]["status"], "unavailable")
        self.assertEqual(report["items"][1]["technical"]["status"], "ok")
        self.assertNotIn("SECRET", json.dumps(report))

    def test_bad_candle_reason_and_date_survive_without_deleting_or_repairing_bar(self):
        frame = history()
        bad_day = frame.index[10]
        frame.loc[bad_day, "Close"] = frame.loc[bad_day, "Low"] - .1
        original = frame.copy(deep=True)
        item = self.build(load_history=lambda ticker: frame)["items"][0]
        reason = item["technical"]["reason"]
        self.assertEqual(item["technical"]["status"], "unavailable")
        self.assertIn("OHLC 高低價格矛盾", reason)
        self.assertIn(bad_day.date().isoformat(), reason)
        self.assertEqual(item["backtest"]["reason"], reason)
        self.assertTrue(any(reason in warning for warning in item["warnings"]))
        self.assertFalse(any("缺漏" in warning for warning in item["warnings"]))
        pd.testing.assert_frame_equal(frame, original)
        self.backtest.assert_not_called()

    def test_legacy_small_samples_hide_precision_only_in_new_research(self):
        legacy = {"schema": "legacy_2026_09_15", "source_commit": "027f459fdea2a79d04c77979f74c0b936cdbe370",
                  "status": "complete", "as_of_date": DAY, "data_through": DAY,
                  "samples": 20, "wins": 10, "losses": 10, "win_rate": 50}
        original = record(Legacy_Backtest=legacy)
        item = self.build([original])["items"][0]
        self.assertEqual(item["legacy_evidence"]["samples"], 20)
        self.assertIsNone(item["legacy_evidence"]["win_rate"])
        self.assertEqual(original["Legacy_Backtest"]["win_rate"], 50)
        self.assertEqual(item["saved_current_samples"]["validation"], 0)

    def test_backtest_train_validation_explicit_and_no_small_precise_win_rate_in_messages(self):
        content = "\n".join(format_research_messages(self.build()))
        self.assertIn("訓練：樣本 6", content)
        self.assertIn("驗證：樣本 2", content)
        self.assertIn("不足 30 不顯示精確勝率", content)
        self.assertNotIn("60%", content)
        self.assertIn("不含持倉內浮虧", content)

    def test_news_is_current_separate_from_historical_prices_and_not_backtested(self):
        loader = Mock(side_effect=news)
        item = self.build(load_news=loader)["items"][0]
        loader.assert_called_once_with("2330", NOW)
        self.assertEqual(item["price_date"], DAY)
        self.assertEqual(item["news"]["queried_at"], NOW.isoformat())
        self.assertEqual(self.backtest.call_args.args[2], DAY)

    def test_news_requires_official_source_ticker_and_aware_nonfuture_time(self):
        def mixed(ticker, now):
            result = news(ticker, now)
            event = {"ticker": ticker, "title": "公告財務報告", "source": "TWSE",
                     "source_url": SOURCES["TWSE"], "published_at": "2026-09-29T09:00:00+08:00"}
            result["events"] = [event, {**event, "source_url": "https://evil.example/"},
                                {**event, "published_at": "2026-09-30T09:00:00+08:00"},
                                {**event, "published_at": "2026-09-29T09:00:00"},
                                {**event, "ticker": "2317"}]
            return result
        item = self.build(load_news=mixed)["items"][0]
        self.assertEqual(len(item["news"]["events"]), 1)
        self.assertIsNone(item["news"]["events"][0]["estimated_price_range"])
        self.assertTrue(any("排除 4" in value for value in item["news"]["warnings"]))
        self.assertNotIn("evil.example", json.dumps(item))

    def test_news_outage_not_no_risk_and_reject_untrusted_status_source(self):
        def outage(ticker, now):
            return {"as_of": now.isoformat(), "events": [], "source_status": {
                "TWSE": {"status": "ok", "source_url": "https://evil.example/"}}}
        content = "\n".join(format_research_messages(self.build(load_news=outage)))
        self.assertIn("無法核實", content)
        self.assertIn("不等同沒有新聞或風險", content)
        self.assertNotIn("evil.example", content)

    def test_unknown_portfolio_never_invented_weights_or_personal_stress(self):
        risk = self.build([record(), record("2317")])["candidate_risk"]
        self.assertFalse(risk["actual_holdings_known"])
        self.assertIsNone(risk["portfolio_stress_return_pct"])
        self.assertEqual(risk["industry_counts"], {"半導體": 2})
        self.assertNotIn("holdings", risk)
        self.assertIn("不假設等權", risk["notice"])

    def test_correlations_require_common_returns_and_dont_fill_missing_sessions(self):
        close = history(count=34).Close
        pairs = _candidate_correlations({"a": close, "b": close})
        self.assertEqual(pairs[0]["samples"], 33)
        self.assertEqual(pairs[0]["correlation"], 1)
        missing = close.drop(close.index[[2, 5, 8]])
        pairs = _candidate_correlations({"a": close, "b": missing})
        self.assertEqual(pairs[0]["samples"], 27)
        self.assertIsNone(pairs[0]["correlation"])

    def test_correlation_coverage_denominator_includes_missing_stock(self):
        close = history().Close
        pairs = _candidate_correlations({"a": close, "b": close}, tickers=["a", "b", "missing"])
        self.assertEqual(len(pairs), 3)
        self.assertEqual(sum(pair["correlation"] is not None for pair in pairs), 1)
        self.assertTrue(all(pair["samples"] == 0 for pair in pairs if "missing" in (pair["left"], pair["right"])))

    def test_financial_and_institutional_statuses_periods_are_not_omitted(self):
        row = record(EPS=2.3, EPS_Period="2024-Q1", Revenue_Status="stale", Revenue_Period="2026-07",
                     Financial_Period="2025-Q4", Financial_Status="stale",
                     Institutional_Latest_Date="2026-09-21", Institutional_Status="stale")
        content = "\n".join(format_research_messages(self.build([row])))
        for text in ("2024-Q1", "期間 2026-07／狀態 stale", "財報期間 2025-Q4／狀態 stale",
                     "截至 2026-09-21／狀態 stale", "過期／未知不當作已驗證"):
            self.assertIn(text, content)

    def test_zero_completed_trades_never_implies_zero_risk(self):
        empty = {"samples": 0, "profit_factor": 0, "max_drawdown_closed_trade_pct": 0,
                 "win_rate_pct": 0}
        self.backtest.side_effect = None
        self.backtest.return_value = {"status": "ok", "metrics": {
            "overall": dict(empty), "training": dict(empty), "validation": dict(empty)}}
        content = "\n".join(format_research_messages(self.build()))
        self.assertIn("獲利因子 未提供；已平倉曲線最大回撤 未提供", content)
        self.assertIn("目前沒有已結束樣本", content)
        self.assertNotIn("最大回撤 0%", content)

    def test_new_rule_rejection_is_separate_and_cost_disadvantage_is_explicit(self):
        row = record(Entry_Reason="關鍵拒絕原因" * 30, ATR=1, 收盤價=100, 最高價=100.5)
        content = "\n".join(format_research_messages(self.build([row])))
        self.assertIn("\n原新制未入榜原因：關鍵拒絕原因", content)
        self.assertIn("含成本目標淨利小於模型停損損失", content)

    def test_forecast_date_in_summary_not_intraday_approval(self):
        report = self.build()
        self.assertEqual(report["forecast_date"], "2026-09-29")
        self.assertIn("預排預測日：2026-09-29", format_research_messages(report)[0])
        self.assertIn("尚非盤中即時再確認", format_research_messages(report)[0])

    def test_expired_forecast_is_historical_supplement_not_new_next_session_list(self):
        for when in (NOW.replace(hour=13, minute=30), NOW.replace(hour=21), NOW.replace(day=30)):
            with self.subTest(when=when):
                report = self.build(now=when)
                self.assertEqual(report["forecast_date"], "2026-09-29")
                self.assertTrue(report["forecast_period_elapsed"])
                self.assertIn("指定榜單回顧／補充分析，非下一交易日新名單", format_research_messages(report)[0])
        early = self.build(now=NOW.replace(hour=13, minute=29))
        self.assertFalse(early["forecast_period_elapsed"])
        self.assertNotIn("預排交易時段已過", format_research_messages(early)[0])

    def test_actual_validation_weakness_drives_research_improvement(self):
        from daily_research_report import _improvement

        report = {"status": "ok", "metrics": {"overall": {"samples": 120},
                   "validation": {"samples": 36, "profit_factor": 0.8}}}
        self.assertIn("驗證獲利因子未大於 1", _improvement(report))
        self.assertIn("勿放寬門檻", _improvement(report))

    def test_messages_preserve_analysis_links_plain_text_and_utf16_limit(self):
        def verbose(ticker, now):
            result = news(ticker, now)
            result["events"] = [{"ticker": ticker, "title": "重大公告😀" * 100,
                "source": "TWSE", "source_url": SOURCES["TWSE"],
                "published_at": "2026-09-29T09:00:00+08:00"} for i in range(2)]
            return result
        messages = format_research_messages(self.build(load_news=verbose))
        self.assertTrue(all(0 < len(text.encode("utf-16-le")) // 2 <= MAX_MESSAGE_UNITS for text in messages))
        joined = "\n".join(messages)
        self.assertIn("?stock=2330", joined)
        self.assertIn("來源資料集", joined)
        self.assertIn("舊制僅供比較", messages[0])
        self.assertNotIn("<b>", joined)
        self.assertIn("保留原每日榜單與績效圖", joined)

    def test_large_unicode_split_never_splits_surrogate(self):
        chunks = _split_units("😀" * 4000)
        self.assertEqual("".join(chunks), "😀" * 4000)
        self.assertTrue(all(len(chunk.encode("utf-16-le")) // 2 <= MAX_MESSAGE_UNITS for chunk in chunks))

    def test_naive_time_future_analysis_or_bad_schema_rejected(self):
        with self.assertRaises(ValueError):
            self.build(now=NOW.replace(tzinfo=None))
        with self.assertRaises(ValueError):
            self.build(now=datetime(2026, 9, 23, tzinfo=timezone.utc))
        with self.assertRaises(ValueError):
            format_research_messages({"schema": "invalid"})


class ReportCalendarTests(unittest.TestCase):
    def test_known_holiday_calendar_dates_checklist_and_unknown_year_fails_closed(self):
        from daily_research_report import _checklist

        known = _checklist("2026-09-24", True)
        self.assertEqual(known["date"], "2026-09-29")
        self.assertIn("twse.com.tw", known["calendar_source"])
        self.assertTrue(known["items"])
        unknown = _checklist("2027-01-01", True)
        self.assertIsNone(unknown["date"])
        self.assertEqual(unknown["items"], [])


if __name__ == "__main__":
    unittest.main()
