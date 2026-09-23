import unittest
from unittest.mock import Mock, patch

import pandas as pd

import scanner


class BenchmarkReconciliationTests(unittest.TestCase):
    def frame(self):
        index = pd.bdate_range(end="2026-09-23", periods=65, tz="Asia/Taipei")
        return pd.DataFrame({
            "Open": [100.0 + n for n in range(65)],
            "Close": [101.0 + n for n in range(65)],
            "High": [103.0 + n for n in range(65)],
            "Low": [99.0 + n for n in range(65)],
            "Volume": [10000] * 65,
        }, index=index)

    def context(self, frame, day="2026-09-22"):
        row = frame.loc[day]
        return {
            "symbol": "TAIEX", "date": day, "close": float(row["Close"]),
            "open": float(row["Open"]), "source": f"top10_tracking_history/{day}",
        }

    def test_missing_previous_session_restores_real_close_and_recalculates_ma(self):
        complete = self.frame()
        partial = complete.drop(pd.Timestamp("2026-09-22", tz="Asia/Taipei"))
        partial["MA20"] = -1.0
        partial["MA60"] = -1.0
        result = scanner.reconcile_benchmark_history(partial, [self.context(complete)])
        benchmark = scanner.build_benchmark_context(result)
        self.assertEqual(benchmark["previous_trading_date"], "2026-09-22")
        self.assertEqual(benchmark["previous_close"], 164.0)
        self.assertEqual(benchmark["daily_return_pct"], round((165 / 164 - 1) * 100, 2))
        self.assertAlmostEqual(result["MA20"].iloc[-1], complete["Close"].tail(20).mean())
        self.assertAlmostEqual(result["MA60"].iloc[-1], complete["Close"].tail(60).mean())
        self.assertEqual(benchmark["restored_observations"], [{
            "date": "2026-09-22", "source": "top10_tracking_history/2026-09-22",
        }])
        self.assertEqual(str(result.index.tz), "Asia/Taipei")
        self.assertTrue(pd.isna(result.loc["2026-09-22", "High"]))
        self.assertTrue(pd.isna(result.loc["2026-09-22", "Low"]))
        self.assertTrue(pd.isna(result.loc["2026-09-22", "Volume"]))
        self.assertEqual(len(partial), 64)
        self.assertEqual(partial["MA20"].iloc[-1], -1.0)

    def test_existing_provider_observations_are_never_overwritten(self):
        frame = self.frame()
        context = self.context(frame)
        frame.loc["2026-09-22", "Close"] += 0.004
        result = scanner.reconcile_benchmark_history(frame, [context])
        pd.testing.assert_frame_equal(result[frame.columns], frame)
        self.assertEqual(result.attrs["restored_benchmark_observations"], [])

    def test_provider_saved_conflict_is_fail_closed(self):
        frame = self.frame()
        context = self.context(frame)
        context["close"] += 1
        with self.assertRaisesRegex(ValueError, "不一致"):
            scanner.reconcile_benchmark_history(frame, [context])

    def test_no_saved_evidence_does_not_invent_a_missing_trading_day(self):
        frame = self.frame().drop(pd.Timestamp("2026-09-22", tz="Asia/Taipei"))
        result = scanner.reconcile_benchmark_history(frame, [])
        self.assertEqual(len(result), len(frame))
        self.assertEqual(scanner.build_benchmark_context(result)["previous_trading_date"], "2026-09-21")

    def test_invalid_and_conflicting_saved_evidence_is_rejected(self):
        frame = self.frame().drop(pd.Timestamp("2026-09-22", tz="Asia/Taipei"))
        valid = self.context(self.frame())
        for changes in ({"close": None}, {"open": float("nan")}, {"symbol": "2330"}, {"date": "bad"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                scanner.reconcile_benchmark_history(frame, [dict(valid, **changes)])
        with self.assertRaisesRegex(ValueError, "不一致"):
            scanner.reconcile_benchmark_history(frame, [valid, dict(valid, close=123)])

    def test_current_future_and_before_window_observations_cannot_extend_or_replace_frame(self):
        frame = self.frame()
        saved = [dict(self.context(frame), date=day) for day in (
            "2026-09-23", "2026-09-24", "2020-01-01",
        )]
        result = scanner.reconcile_benchmark_history(frame, saved)
        pd.testing.assert_frame_equal(result[frame.columns], frame)

    def test_out_of_order_or_duplicate_provider_days_are_rejected(self):
        frame = self.frame()
        for invalid in (frame.iloc[::-1], pd.concat([frame, frame.tail(1)])):
            with self.assertRaisesRegex(ValueError, "依序"):
                scanner.reconcile_benchmark_history(invalid, [])

    def test_saved_history_loader_uses_only_four_recent_actual_dates_on_same_day_rerun(self):
        complete = self.frame()
        history_dates = [stamp.date().isoformat() for stamp in complete.index[-10:]][::-1]
        documents = {
            ("market_data", "top10_tracker"): {"data": {
                "latest_date": "2026-09-23", "latest_benchmark": self.context(complete, "2026-09-23"),
                "history_dates": history_dates,
            }},
        }
        for day in history_dates[1:]:
            documents[("top10_tracking_history", day)] = {"data": {
                "date": day, "benchmark": self.context(complete, day),
            }}
        requested = []
        database = self.database(documents, requested)
        with patch.object(scanner, "db", database):
            saved = scanner._load_saved_benchmarks("2026-09-23")
        self.assertEqual([row["date"] for row in saved], history_dates[1:5])
        self.assertEqual(len(requested), 5)  # one manifest plus at most four histories
        self.assertNotIn(("top10_tracking_history", "2026-09-23"), requested)

    @staticmethod
    def database(documents, requested):
        database = Mock()

        def collection(name):
            result = Mock()

            def document(key):
                requested.append((name, key))
                doc = Mock()
                payload = documents.get((name, key))
                doc.get.return_value.exists = payload is not None
                doc.get.return_value.to_dict.return_value = payload
                return doc

            result.document.side_effect = document
            return result

        database.collection.side_effect = collection
        return database

    def test_history_document_date_mismatch_is_rejected(self):
        documents = {
            ("market_data", "top10_tracker"): {"data": {
                "latest_date": "2026-09-23", "history_dates": ["2026-09-22"],
            }},
            ("top10_tracking_history", "2026-09-22"): {"data": {
                "date": "2026-09-21", "benchmark": self.context(self.frame()),
            }},
        }
        with patch.object(scanner, "db", self.database(documents, [])):
            with self.assertRaisesRegex(RuntimeError, "日期不一致"):
                scanner._load_saved_benchmarks("2026-09-23")

    def test_scan_reconciliation_conflict_prevents_lease_or_other_writes(self):
        frame = self.frame()
        saved = [dict(self.context(frame), close=999)]
        with (
            patch.object(scanner, "db", object()),
            patch.object(scanner, "call_with_backoff", return_value=frame),
            patch.object(scanner, "_load_saved_benchmarks", return_value=saved),
            patch.object(scanner, "_acquire_scan_lease") as lease,
            patch.object(scanner, "_load_daily_scan_doc") as load_scan,
        ):
            with self.assertRaisesRegex(ValueError, "不一致"):
                scanner.run_daily_scan(force=True, allow_intraday=True)
        lease.assert_not_called()
        load_scan.assert_not_called()


if __name__ == "__main__":
    unittest.main()
