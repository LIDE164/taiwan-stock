from copy import deepcopy
from datetime import datetime
import unittest
from unittest.mock import Mock, patch

import scanner
from scan_completion import TPE, completed_source_rows, complete_daily_receipts
from research_delivery import report_fingerprint
from tests.test_scanner_telegram import _Database


DAY = "2026-10-05"
NOW = datetime(2026, 10, 6, 10, tzinfo=TPE)
ROWS = [{"代號": "2330", "Data_Date": DAY}]


def receipts():
    return {
        "daily_top10": {"date": DAY, "status": "sent", "message_id": 1},
        "daily_executable": {"date": DAY, "status": "sent", "message_id": 2},
        "daily_tracking_performance": {"date": DAY, "status": "sent", "page_count": 2,
                                       "sent_pages": {"1": 3, "2": 4}},
        "daily_research": {"date": DAY, "status": "sent", "parts": ["part 1", "part 2"],
                           "fingerprint": report_fingerprint(ROWS, DAY),
                           "part_count": 2, "sent_parts": {"1": 5, "2": 6}, "in_flight": ""},
    }


class ScanCompletionTests(unittest.TestCase):
    def test_safe_window_target_includes_same_day_and_weekend_recovery(self):
        for stamp, day in (("2026-10-06T21:00:00+08:00", "2026-10-06"),
                           ("2026-10-07T03:35:00+08:00", "2026-10-06"),
                           ("2026-10-09T03:35:00+08:00", "2026-10-08"),
                           ("2026-10-10T03:35:00+08:00", "2026-10-08")):
            with self.subTest(stamp=stamp):
                rows = [{"代號": "2330", "Data_Date": day}]
                self.assertEqual(completed_source_rows({"scan_date": day, "data": rows},
                                                       {"status": "completed", "trading_date": day},
                                                       datetime.fromisoformat(stamp)), rows)

    def test_next_postclose_cannot_reuse_previous_day_and_unknown_calendar_stops(self):
        for now in (NOW.replace(hour=15), NOW.replace(year=2027), NOW.replace(day=9, hour=15)):
            with self.subTest(now=now):
                self.assertIsNone(completed_source_rows({"scan_date": DAY, "data": ROWS},
                                                       {"status": "completed", "trading_date": DAY}, now))

    def test_source_requires_completed_prior_session_and_preserves_rows(self):
        manifest = {"scan_date": DAY, "data": deepcopy(ROWS)}
        lock = {"status": "completed", "trading_date": DAY}
        self.assertEqual(completed_source_rows(manifest, lock, NOW), ROWS)
        self.assertEqual(manifest["data"], ROWS)
        for invalid in ({}, {"status": "running", "trading_date": DAY},
                        {"status": "completed", "trading_date": "2026-10-02"}):
            with self.subTest(lock=invalid):
                self.assertIsNone(completed_source_rows(manifest, invalid, NOW))

    def test_stale_mixed_empty_and_unverified_source_is_not_complete(self):
        for manifest in ({}, {"scan_date": DAY, "data": []},
                         {"scan_date": DAY, "data": [{"Data_Date": "2026-10-02"}]},
                         {"scan_date": "2026-10-02", "data": [{"Data_Date": "2026-10-02"}]},
                         {"scan_date": "invalid", "data": ROWS}):
            with self.subTest(manifest=manifest):
                lock = {"status": "completed", "trading_date": manifest.get("scan_date")}
                self.assertIsNone(completed_source_rows(manifest, lock, NOW))
        self.assertIsNone(completed_source_rows({"scan_date": DAY, "data": ROWS},
                                               {"status": "completed", "trading_date": DAY},
                                               NOW.replace(tzinfo=None)))

    def test_all_four_categories_need_actual_confirmed_receipts(self):
        self.assertTrue(complete_daily_receipts(receipts(), DAY))
        for kind in receipts():
            missing = receipts()
            missing.pop(kind)
            with self.subTest(kind=kind):
                self.assertFalse(complete_daily_receipts(missing, DAY))

    def test_photo_sent_flag_without_positive_integer_id_is_not_delivery(self):
        for kind in ("daily_top10", "daily_executable"):
            for value in (None, False, True, 0, -1, "1", 1.5):
                with self.subTest(kind=kind, value=value):
                    invalid = receipts()
                    invalid[kind]["message_id"] = value
                    self.assertFalse(complete_daily_receipts(invalid, DAY))

    def test_performance_requires_all_distinct_page_receipts(self):
        for pages in ({"1": 3}, {"1": 3, "2": 3}, {"1": 3, "2": False}, {"1": 3, "2": 0}):
            with self.subTest(pages=pages):
                invalid = receipts()
                invalid["daily_tracking_performance"]["sent_pages"] = pages
                self.assertFalse(complete_daily_receipts(invalid, DAY))

    def test_impossible_page_count_and_oversized_research_are_bounded_before_expansion(self):
        for count in (1001, 10**100):
            with self.subTest(count=count):
                invalid = receipts()
                invalid["daily_tracking_performance"]["page_count"] = count
                self.assertFalse(complete_daily_receipts(invalid, DAY))
        invalid = receipts()
        invalid["daily_research"].update(parts=["part"] * 26, part_count=26,
                                         sent_parts={str(i): i for i in range(1, 27)})
        self.assertFalse(complete_daily_receipts(invalid, DAY))

    def test_missing_performance_is_only_allowed_with_verified_empty_report(self):
        missing = receipts()
        missing.pop("daily_tracking_performance")
        self.assertFalse(complete_daily_receipts(missing, DAY))
        self.assertTrue(complete_daily_receipts(missing, DAY, performance_is_empty=True))
        missing.pop("daily_research")
        self.assertFalse(complete_daily_receipts(missing, DAY, performance_is_empty=True))

    def test_research_requires_every_part_and_no_inflight_or_ambiguity(self):
        changes = ({"sent_parts": {"1": 5}}, {"sent_parts": {"1": 5, "2": 5}},
                   {"sent_parts": {"1": 5, "2": 0}}, {"part_count": True}, {"part_count": 1},
                   {"parts": []}, {"parts": ["", "part 2"]}, {"status": "uncertain"},
                   {"in_flight": "2"}, {"date": "2026-10-02"})
        for change in changes:
            with self.subTest(change=change):
                invalid = receipts()
                invalid["daily_research"].update(change)
                self.assertFalse(complete_daily_receipts(invalid, DAY))


class LateBackupIntegrationTests(unittest.TestCase):
    def database(self, notification_state):
        database = _Database()
        database.collection("system_locks").document("daily_scan").set({"status": "completed", "trading_date": DAY})
        database.collection("top10_tracking_history").document(DAY).set({"data": {"date": DAY, "records": []}})
        database.collection("market_data").document("top10_tracker").set({"data": {"latest_date": DAY, "positions": []}})
        for kind, value in notification_state.items():
            database.collection("notifications").document(f"{kind}_{DAY}").set(value)
        for collection in database.collections.values():
            for document in collection.documents.values():
                document.set = Mock(side_effect=AssertionError("read-only check must not write"))
        return database

    def test_fully_sent_late_backup_is_readonly_noop_without_market_fetch_or_send(self):
        database = self.database(receipts())
        with (
            patch.dict(scanner.os.environ, {"GITHUB_EVENT_NAME": "schedule", "FORCE_SCAN": "0"}),
            patch.object(scanner, "db", database),
            patch.object(scanner, "_load_daily_scan_doc", return_value={"scan_date": DAY, "data": ROWS}),
            patch.object(scanner, "_load_tracking_performance_data") as performance,
            patch.object(scanner, "call_with_backoff") as fetch,
            patch.object(scanner, "_acquire_scan_lease") as lease,
            patch.object(scanner, "_write_daily_scan_doc") as write,
            patch.object(scanner, "update_top10_tracker") as tracker,
            patch.object(scanner, "send_daily_notifications") as send,
        ):
            self.assertEqual(scanner.run_daily_scan(clock=lambda: NOW), ROWS)
        for operation in (performance, fetch, lease, write, tracker, send):
            operation.assert_not_called()
        for collection in database.collections.values():
            for document in collection.documents.values():
                document.set.assert_not_called()

    def test_one_missing_receipt_still_fails_without_write_or_resend(self):
        incomplete = receipts()
        incomplete["daily_research"]["sent_parts"] = {"1": 5}
        with (
            patch.dict(scanner.os.environ, {"GITHUB_EVENT_NAME": "schedule", "FORCE_SCAN": "0"}),
            patch.object(scanner, "db", self.database(incomplete)),
            patch.object(scanner, "_load_daily_scan_doc", return_value={"scan_date": DAY, "data": ROWS}),
            patch.object(scanner, "call_with_backoff") as fetch,
            patch.object(scanner, "send_daily_notifications") as send,
        ):
            with self.assertRaisesRegex(RuntimeError, "已錯過安全補掃時段"):
                scanner.run_daily_scan(clock=lambda: NOW)
        fetch.assert_not_called()
        send.assert_not_called()

    def test_no_performance_image_requires_loading_and_calculating_zero_tracked_count(self):
        state = receipts()
        state.pop("daily_tracking_performance")
        with (
            patch.dict(scanner.os.environ, {"GITHUB_EVENT_NAME": "schedule", "FORCE_SCAN": "0"}),
            patch.object(scanner, "db", self.database(state)),
            patch.object(scanner, "_load_daily_scan_doc", return_value={"scan_date": DAY, "data": ROWS}),
            patch.object(scanner, "_load_tracking_performance_data", return_value=([], [], {})) as load,
            patch.object(scanner, "build_tracking_performance_report", wraps=scanner.build_tracking_performance_report) as report,
            patch.object(scanner, "send_daily_notifications") as send,
        ):
            self.assertEqual(scanner.run_daily_scan(clock=lambda: NOW), ROWS)
        load.assert_called_once_with(DAY)
        report.assert_called_once_with([], [], DAY, cumulative_summary={})
        send.assert_not_called()

    def test_unavailable_performance_evidence_does_not_silently_pass(self):
        state = receipts()
        state.pop("daily_tracking_performance")
        with (
            patch.dict(scanner.os.environ, {"GITHUB_EVENT_NAME": "schedule", "FORCE_SCAN": "0"}),
            patch.object(scanner, "db", self.database(state)),
            patch.object(scanner, "_load_daily_scan_doc", return_value={"scan_date": DAY, "data": ROWS}),
            patch.object(scanner, "_load_tracking_performance_data", side_effect=RuntimeError("offline read failure")),
        ):
            with self.assertRaisesRegex(RuntimeError, "已錯過安全補掃時段"):
                scanner.run_daily_scan(clock=lambda: NOW)

    def test_absent_performance_history_never_becomes_verified_empty(self):
        state = receipts()
        state.pop("daily_tracking_performance")
        database = self.database(state)
        database.collections["top10_tracking_history"].documents.pop(DAY)
        with (
            patch.object(scanner, "db", database),
            patch.object(scanner, "_load_daily_scan_doc", return_value={"scan_date": DAY, "data": ROWS}),
            patch.object(scanner, "_load_tracking_performance_data", return_value=([], [], {})) as load,
            patch.object(scanner, "build_tracking_performance_report", return_value={"tracked_count": 0}) as report,
        ):
            self.assertIsNone(scanner._completed_late_backup_rows(NOW))
        load.assert_not_called()
        report.assert_not_called()

    def test_mismatched_history_or_stale_tracker_never_certifies_no_performance(self):
        for collection, key, payload in (
            ("top10_tracking_history", DAY, {"data": {"date": "2026-10-02", "records": []}}),
            ("top10_tracking_history", DAY, {"data": {"date": DAY}}),
            ("market_data", "top10_tracker", {"data": {"latest_date": "2026-10-02"}}),
        ):
            with self.subTest(payload=payload):
                state = receipts()
                state.pop("daily_tracking_performance")
                database = self.database(state)
                database.collections[collection].documents[key].value = payload
                with (
                    patch.object(scanner, "db", database),
                    patch.object(scanner, "_load_daily_scan_doc", return_value={"scan_date": DAY, "data": ROWS}),
                    patch.object(scanner, "_load_tracking_performance_data", return_value=([], [], {})) as load,
                ):
                    self.assertIsNone(scanner._completed_late_backup_rows(NOW))
                load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
