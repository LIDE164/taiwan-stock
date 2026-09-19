import unittest

from trade_journal import analyze_journal, derive_record_metrics, normalize_record


def closed_record(**changes):
    record = {
        "id": "trade-1",
        "status": "closed",
        "ticker": "2330",
        "entry_date": "2026-01-02",
        "entry_price": 100,
        "shares": 100,
        "exit_date": "2026-01-06",
        "exit_price": 110,
        "actual_costs": 50,
        "planned_entry_high": 101,
        "planned_stop": 90,
        "planned_target": 120,
    }
    record.update(changes)
    return record


class NormalizeRecordTests(unittest.TestCase):
    def test_closed_normalizes_fields_without_inventing_missing_costs(self):
        result = normalize_record(closed_record(actual_costs=None, ticker="00632r"))
        self.assertEqual(result["ticker"], "00632R")
        self.assertIsNone(result["actual_costs"])
        self.assertEqual(result["emotion"], [])
        self.assertIsNone(result["notes"])

    def test_id_is_required_and_safe_for_merge_and_delete(self):
        for record_id in (None, "", "bad/id", "has space", "點", "a" * 65):
            with self.subTest(record_id=record_id), self.assertRaises(ValueError):
                normalize_record(closed_record(id=record_id))
        self.assertEqual(normalize_record(closed_record(id="journal_01-A"))["id"], "journal_01-A")

    def test_rejects_invalid_ticker_status_numbers_and_dates(self):
        cases = (
            {"ticker": "TSMC"}, {"status": "simulated"},
            {"status": []},
            {"entry_price": 0}, {"entry_price": float("nan")},
            {"entry_price": float("inf")}, {"entry_price": "100"},
            {"shares": 1.5}, {"shares": True}, {"shares": 0},
            {"shares": 10**400}, {"shares": 1_000_000_000_001},
            {"entry_price": 1e300}, {"entry_price": 1e-300},
            {"actual_costs": 1e300},
            {"actual_costs": -1}, {"exit_date": "2026-01-01"},
            {"entry_date": "2026-02-30"}, {"planned_entry_low": 102},
            {"planned_stop": 120}, {"planned_stop": 100}, {"extra": "unexpected"},
        )
        for change in cases:
            with self.subTest(change=change), self.assertRaises(ValueError):
                normalize_record(closed_record(**change))

    def test_closed_requires_actual_exit_and_open_forbids_it(self):
        with self.assertRaises(ValueError):
            normalize_record(closed_record(exit_price=None))
        with self.assertRaises(ValueError):
            normalize_record(closed_record(status="open"))
        opened = normalize_record(closed_record(status="open", exit_date=None, exit_price=None, exit_reason=None))
        self.assertEqual(opened["status"], "open")

    def test_missed_requires_explicit_status_observation_and_no_actual_trade(self):
        missed = {
            "id": "missed-1", "status": "missed", "ticker": "0050",
            "decision_date": "2026-01-05", "observed_date": "2026-01-07",
            "observed_price": 105, "planned_entry_high": 100,
            "missed_reason": "沒有符合原先條件",
        }
        self.assertEqual(normalize_record(missed)["status"], "missed")
        with self.assertRaises(ValueError):
            normalize_record({**missed, "observed_price": None})
        with self.assertRaises(ValueError):
            normalize_record({**missed, "decision_date": None})
        with self.assertRaises(ValueError):
            normalize_record({**missed, "decision_date": "2026-01-08"})
        with self.assertRaises(ValueError):
            normalize_record({**missed, "entry_price": 100})
        with self.assertRaises(ValueError):
            normalize_record({**missed, "entry_date": "2026-01-05"})
        with self.assertRaises(ValueError):
            normalize_record({**missed, "actual_costs": 0})


class MetricsTests(unittest.TestCase):
    def test_closed_net_uses_only_actual_costs(self):
        metrics = derive_record_metrics(closed_record())
        self.assertEqual(metrics["gross_pnl"], 1000)
        self.assertEqual(metrics["net_pnl"], 950)
        self.assertGreater(metrics["planned_risk"], 1000)
        self.assertTrue(metrics["early_profitable_exit_review"])

    def test_risk_limit_includes_modeled_costs_not_just_price_distance(self):
        metrics = derive_record_metrics(closed_record(
            entry_price=100,
            planned_stop=95,
            shares=996,
            exit_price=98,
        ))
        self.assertEqual((100 - 95) * 996, 4980)
        self.assertGreater(metrics["planned_risk"], 5000)
        self.assertTrue(metrics["excessive_planned_risk"])

    def test_missing_costs_and_open_do_not_report_net_or_unrealized_profit(self):
        missing = derive_record_metrics(closed_record(actual_costs=None))
        self.assertEqual(missing["gross_pnl"], 1000)
        self.assertIsNone(missing["net_pnl"])
        self.assertFalse(missing["early_profitable_exit_review"])
        opened = derive_record_metrics(closed_record(status="open", exit_date=None, exit_price=None))
        self.assertIsNone(opened["gross_pnl"])
        self.assertIsNone(opened["net_pnl"])

    def test_missed_observation_is_only_a_price_gap(self):
        metrics = derive_record_metrics({
            "id": "missed-1", "status": "missed", "ticker": "0050",
            "decision_date": "2026-01-05", "observed_date": "2026-01-07",
            "observed_price": 105, "planned_entry_high": 100,
        })
        self.assertEqual(metrics["observed_price_gap"], 5)
        self.assertEqual(metrics["observed_price_gap_pct"], 5)
        self.assertIsNone(metrics["gross_pnl"])
        self.assertIsNone(metrics["net_pnl"])

    def test_emotion_evidence_only_from_explicit_tags(self):
        no_tag = derive_record_metrics(closed_record(exit_price=80, emotion=None, entry_reason="因恐懼而買入"))
        self.assertFalse(no_tag["possible_emotion_bias"])
        calm = derive_record_metrics(closed_record(emotion="冷靜"))
        self.assertFalse(calm["possible_emotion_bias"])
        tagged = derive_record_metrics(closed_record(emotion=["fomo", "冷靜", "衝動"]))
        self.assertTrue(tagged["possible_emotion_bias"])
        self.assertEqual(tagged["emotion_evidence_tags"], ["fomo", "衝動"])
        for tag in ("怕錯過", "急於回本", "不願認賠", "怕獲利回吐", "臨時改單"):
            with self.subTest(tag=tag):
                self.assertEqual(derive_record_metrics(closed_record(emotion=tag))["emotion_evidence_tags"], [tag])


class AnalyzeJournalTests(unittest.TestCase):
    def test_empty_journal_has_three_nonpersonalized_rules(self):
        result = analyze_journal([])
        self.assertEqual(result["summary"]["total_records"], 0)
        self.assertIsNone(result["summary"]["realized_net_pnl"])
        self.assertEqual(len(result["rules"]), 3)
        self.assertTrue(all(not rule["personalized"] for rule in result["rules"]))
        self.assertTrue(all("基礎規則" in rule["basis"] for rule in result["rules"]))

    def test_repeated_findings_have_auditable_ids_and_sorted_rules(self):
        records = [
            closed_record(
                id="a", entry_price=105, planned_entry_high=100, shares=1000,
                planned_stop=90, exit_price=85, emotion="FOMO",
            ),
            closed_record(
                id="b", entry_price=104, planned_entry_high=100, shares=1000,
                planned_stop=90, exit_price=85, emotion="焦慮",
            ),
            closed_record(
                id="c", entry_price=102, planned_entry_high=100, shares=100,
                planned_stop=90, exit_price=115, emotion="冷靜",
            ),
        ]
        result = analyze_journal(records)
        by_key = {finding["key"]: finding for finding in result["findings"]}
        self.assertEqual(by_key["entry_above_plan"]["count"], 3)
        self.assertEqual(by_key["entry_above_plan"]["evidence_ids"], ["a", "b", "c"])
        self.assertEqual(by_key["stop_execution_deviation"]["count"], 2)
        self.assertEqual(by_key["excessive_planned_risk"]["count"], 2)
        self.assertEqual(by_key["possible_emotion_bias"]["count"], 2)
        self.assertEqual(result["rules"][0]["finding_key"], "entry_above_plan")
        self.assertTrue(result["rules"][0]["personalized"])
        self.assertEqual(len(result["rules"]), 3)
        self.assertIn("actual_entry", by_key["entry_above_plan"]["evidence"][0])

    def test_partial_fee_coverage_never_masquerades_as_total_net(self):
        result = analyze_journal([
            closed_record(id="known"),
            closed_record(id="unknown", actual_costs=None),
            {
                "id": "open", "status": "open", "ticker": "0050",
                "entry_date": "2026-01-07", "entry_price": 90, "shares": 10,
            },
            {
                "id": "missed", "status": "missed", "ticker": "0050",
                "decision_date": "2026-01-07", "observed_date": "2026-01-08", "observed_price": 95,
            },
        ])
        summary = result["summary"]
        self.assertEqual(summary["closed_count"], 2)
        self.assertEqual(summary["open_count"], 1)
        self.assertEqual(summary["missed_count"], 1)
        self.assertEqual(summary["known_net_pnl"], 950)
        self.assertEqual(summary["unknown_cost_count"], 1)
        self.assertIsNone(summary["realized_net_pnl"])

    def test_recent_limit_applies_to_evidence_not_all_time_summary(self):
        records = [
            closed_record(id="old", entry_date="2025-01-01", exit_date="2025-01-02", entry_price=105),
            closed_record(id="new", entry_date="2026-01-01", exit_date="2026-01-02", entry_price=105),
        ]
        result = analyze_journal(records, recent_limit=1)
        self.assertEqual(result["summary"]["total_records"], 2)
        self.assertEqual(result["summary"]["reviewed_count"], 1)
        self.assertEqual([row["id"] for row in result["rows"]], ["new"])
        finding = next(item for item in result["findings"] if item["key"] == "entry_above_plan")
        self.assertEqual(finding["evidence_ids"], ["new"])
        self.assertFalse(any(rule["personalized"] for rule in result["rules"]))

    def test_repeated_manual_missed_reason_has_evidence_but_no_foregone_profit(self):
        records = [
            {
                "id": "miss-1", "status": "missed", "ticker": "2330",
                "decision_date": "2026-01-02", "observed_date": "2026-01-04",
                "observed_price": 105, "planned_entry_high": 100,
                "missed_reason": "條件未確認",
            },
            {
                "id": "miss-2", "status": "missed", "ticker": "0050",
                "decision_date": "2026-01-05", "observed_date": "2026-01-06",
                "observed_price": 102, "planned_entry_high": 99,
                "missed_reason": "條件未確認",
            },
        ]
        result = analyze_journal(records)
        finding = next(item for item in result["findings"] if item["key"] == "missed_opportunity")
        self.assertEqual(finding["count"], 2)
        self.assertEqual(finding["denominator"], 2)
        self.assertEqual(finding["repeated_reason"], "條件未確認")
        self.assertEqual(finding["repeated_reason_evidence_ids"], ["miss-2", "miss-1"])
        self.assertIn("observed_date", finding["evidence"][0])
        self.assertIn("missed_reason", finding["evidence"][0])
        self.assertEqual(result["rules"][0]["finding_key"], "missed_opportunity")
        self.assertTrue(result["rules"][0]["personalized"])
        self.assertIsNone(result["summary"]["realized_gross_pnl"])
        self.assertIsNone(result["summary"]["realized_net_pnl"])
        self.assertTrue(all(row["metrics"]["net_pnl"] is None for row in result["rows"]))

    def test_missed_without_repeated_reason_only_gets_base_rule(self):
        records = [
            {
                "id": "a", "status": "missed", "ticker": "2330",
                "decision_date": "2026-01-02", "observed_date": "2026-01-03",
                "observed_price": 100, "missed_reason": "等待",
            },
            {
                "id": "b", "status": "missed", "ticker": "2330",
                "decision_date": "2026-01-04", "observed_date": "2026-01-05",
                "observed_price": 101, "missed_reason": "忙碌",
            },
        ]
        result = analyze_journal(records)
        finding = next(item for item in result["findings"] if item["key"] == "missed_opportunity")
        self.assertIsNone(finding["repeated_reason"])
        self.assertFalse(any(rule["personalized"] for rule in result["rules"]))

    def test_duplicate_id_and_invalid_limit_fail(self):
        with self.assertRaises(ValueError):
            analyze_journal([closed_record(), closed_record()])
        with self.assertRaises(ValueError):
            analyze_journal([], recent_limit=0)


if __name__ == "__main__":
    unittest.main()
