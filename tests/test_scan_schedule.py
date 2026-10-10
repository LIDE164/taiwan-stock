import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pandas as pd

import scanner
from scan_schedule import TPE, delayed_publication_note, ensure_publish_allowed, execution_metadata, scan_window


def local(value):
    return datetime.fromisoformat(value).replace(tzinfo=TPE)


class ScanScheduleTests(unittest.TestCase):
    def test_midnight_and_early_morning_recover_previous_completed_day(self):
        for moment in ("2026-10-06T00:00:00", "2026-10-06T05:30:00", "2026-10-06T08:29:59"):
            with self.subTest(moment=moment):
                window = scan_window(local(moment))
                self.assertEqual(window.analysis_date.isoformat(), "2026-10-05")
                self.assertEqual(window.publish_before, local("2026-10-06T09:00:00"))

    def test_no_new_recovery_at_0830_and_no_ordinary_intraday_scan(self):
        for moment in ("2026-10-06T08:30:00", "2026-10-06T09:00:00", "2026-10-06T14:29:59"):
            with self.subTest(moment=moment):
                self.assertIsNone(scan_window(local(moment)))

    def test_postclose_expected_date_is_today_not_provider_latest_date(self):
        for moment in ("2026-10-06T14:30:00", "2026-10-06T23:59:59"):
            with self.subTest(moment=moment):
                window = scan_window(local(moment))
                self.assertEqual(window.analysis_date.isoformat(), "2026-10-06")
                self.assertEqual(window.publish_before, local("2026-10-07T09:00:00"))

    def test_recovery_skips_verified_weekends_and_market_holidays(self):
        cases = {"2026-10-05T05:30:00": "2026-10-02", "2026-09-29T01:00:00": "2026-09-24"}
        for moment, expected in cases.items():
            with self.subTest(moment=moment):
                self.assertEqual(scan_window(local(moment)).analysis_date.isoformat(), expected)

    def test_closed_day_daytime_is_a_safe_noop(self):
        for moment in ("2026-10-09T15:00:00", "2026-10-10T16:00:00", "2026-10-11T09:00:00"):
            with self.subTest(moment=moment):
                self.assertIsNone(scan_window(local(moment)))

    def test_unknown_year_or_unknown_previous_year_cannot_guess_a_session(self):
        for moment in ("2027-01-05T16:00:00", "2026-01-01T05:30:00"):
            with self.subTest(moment=moment), self.assertRaisesRegex(ValueError, "已驗證"):
                scan_window(local(moment))

    def test_clock_must_be_aware_and_is_normalized_to_taipei(self):
        with self.assertRaisesRegex(ValueError, "時區"):
            scan_window(datetime(2026, 10, 6, 5))
        window = scan_window(datetime(2026, 10, 5, 17, tzinfo=timezone.utc))
        self.assertEqual(window.analysis_date.isoformat(), "2026-10-05")
        self.assertEqual(window.started_at, local("2026-10-06T01:00:00"))

    def test_postclose_job_can_cross_midnight_but_must_stop_before_next_0900(self):
        window = scan_window(local("2026-10-05T23:59:00"))
        self.assertEqual(ensure_publish_allowed(window, local("2026-10-06T08:59:59")),
                         local("2026-10-06T08:59:59"))
        for moment in ("2026-10-06T09:00:00", "2026-10-07T01:00:00"):
            with self.subTest(moment=moment), self.assertRaisesRegex(RuntimeError, "09:00"):
                ensure_publish_allowed(window, local(moment))

    def test_recovery_started_0829_cannot_publish_after_open(self):
        window = scan_window(local("2026-10-06T08:29:59"))
        with self.assertRaisesRegex(RuntimeError, "09:00"):
            ensure_publish_allowed(window, local("2026-10-06T09:00:00"))

    def test_clock_rollback_is_not_treated_as_valid_publication(self):
        window = scan_window(local("2026-10-06T05:30:00"))
        with self.assertRaisesRegex(RuntimeError, "倒退"):
            ensure_publish_allowed(window, local("2026-10-06T05:00:00"))

    def test_recovery_metadata_preserves_real_generated_time_and_analysis_date(self):
        window = scan_window(local("2026-10-05T23:59:00"))
        result = execution_metadata(window, local("2026-10-06T00:05:00"))
        self.assertEqual(result["data_as_of_date"], "2026-10-05")
        self.assertEqual(result["generated_at"], "2026-10-06T00:05:00+08:00")
        self.assertEqual(result["scan_started_at"], "2026-10-05T23:59:00+08:00")
        self.assertTrue(result["delayed_recovery"])
        self.assertEqual(result["scan_mode"], "delayed_preopen_recovery")
        self.assertIn("不代表分析日當晚已存在", result["availability_note"])

    def test_normal_postclose_metadata_is_not_marked_as_recovery(self):
        result = execution_metadata(scan_window(local("2026-10-06T15:00:00")), local("2026-10-06T15:10:00"))
        self.assertFalse(result["delayed_recovery"])
        self.assertEqual(result["scan_mode"], "postclose")

    def test_saved_recovery_note_uses_actual_timestamp_and_not_analysis_midnight(self):
        rows = [{"Data_Date": "2026-10-05", "Scan_Delayed_Recovery": True,
                 "Scan_Generated_At": "2026-10-06T00:40:00+08:00"}] * 2
        note = delayed_publication_note(rows, "2026-10-05", now=local("2026-10-06T01:00:00"))
        self.assertIn("分析日 2026-10-05", note)
        self.assertIn("實際生成 2026-10-06 00:40:00", note)
        self.assertIn("非分析日當晚已發布", note)

    def test_note_cannot_invent_a_timestamp_or_silently_hide_inconsistent_recovery(self):
        original = {"Data_Date": "2026-10-05", "Scan_Delayed_Recovery": True,
                    "Scan_Generated_At": "2026-10-06T00:40:00+08:00"}
        for changes in ({"Scan_Generated_At": None}, {"Scan_Generated_At": "2026-10-06T00:40:00"},
                        {"Scan_Generated_At": "2026-10-06T00:45:00+08:00"},
                        {"Data_Date": "2026-10-02"}, {"Scan_Delayed_Recovery": False}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                delayed_publication_note([original, dict(original, **changes)], "2026-10-05",
                                         now=local("2026-10-06T01:00:00"))
        for stamp in ("2026-10-06T09:00:00+08:00", "2026-10-06T02:00:00+08:00",
                      "2026-10-05T00:40:00+08:00"):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                delayed_publication_note([dict(original, Scan_Generated_At=stamp)], "2026-10-05",
                                         now=local("2026-10-06T01:00:00"))

    def test_old_rows_without_recorded_recovery_evidence_do_not_get_a_false_label(self):
        self.assertEqual(delayed_publication_note([{"Data_Date": "2026-10-05"}], "2026-10-05",
                                                 now=local("2026-10-06T01:00:00")), "")


class ScannerScheduleIntegrationTests(unittest.TestCase):
    @staticmethod
    def benchmark(day):
        return pd.DataFrame({"Close": [22000.0 + i for i in range(70)]},
                            index=pd.bdate_range(end=day, periods=70))

    def test_stale_postclose_benchmark_cannot_reuse_completed_previous_ranking(self):
        with (
            patch.object(scanner, "db", object()),
            patch.object(scanner, "call_with_backoff", return_value=self.benchmark("2026-10-02")),
            patch.object(scanner, "_load_daily_scan_doc") as load,
            patch.object(scanner, "_acquire_scan_lease") as lease,
            patch.object(scanner, "send_daily_notifications") as send,
        ):
            with self.assertRaisesRegex(RuntimeError, "預期 2026-10-05，取得 2026-10-02"):
                scanner.run_daily_scan(clock=lambda: local("2026-10-05T15:30:00"))
        # Read-only completion preflight may inspect saved rows first, but an
        # old benchmark still cannot acquire a lease or publish a new ranking.
        load.assert_called_once_with()
        lease.assert_not_called()
        send.assert_not_called()

    def test_recovery_rejects_older_or_future_benchmark(self):
        for day in ("2026-10-02", "2026-10-06"):
            with (
                self.subTest(day=day),
                patch.object(scanner, "db", object()),
                patch.object(scanner, "call_with_backoff", return_value=self.benchmark(day)),
                patch.object(scanner, "_acquire_scan_lease") as lease,
            ):
                with self.assertRaisesRegex(RuntimeError, "行情日期不符"):
                    scanner.run_daily_scan(clock=lambda: local("2026-10-06T00:00:40"))
                lease.assert_not_called()

    def test_midnight_delay_can_retry_completed_prior_day_notifications(self):
        rows = [{"代號": "2330", "Data_Date": "2026-10-05"}]
        database = Mock()
        snapshot = database.collection.return_value.document.return_value.get.return_value
        snapshot.exists = True
        snapshot.to_dict.return_value = {"status": "completed", "trading_date": "2026-10-05"}
        with (
            patch.object(scanner, "db", database),
            patch.object(scanner, "_completed_late_backup_rows", return_value=None),
            patch.object(scanner, "call_with_backoff", return_value=self.benchmark("2026-10-05")),
            patch.object(scanner, "_load_saved_benchmarks", return_value=[]),
            patch.object(scanner, "_load_daily_scan_doc", return_value={"scan_date": "2026-10-05", "data": rows}),
            patch.object(scanner, "_acquire_scan_lease", return_value=False) as lease,
            patch.object(scanner, "send_daily_notifications") as send,
        ):
            result = scanner.run_daily_scan(clock=lambda: local("2026-10-06T00:00:40"))
        self.assertEqual(result, rows)
        lease.assert_called_once_with("2026-10-05", force=False)
        send.assert_called_once_with(rows, "2026-10-05", resend=False)

    def test_competing_runner_never_sends_manifest_before_tracker_and_scan_complete(self):
        database = Mock()
        snapshot = database.collection.return_value.document.return_value.get.return_value
        snapshot.exists = True
        snapshot.to_dict.return_value = {"status": "running", "trading_date": "2026-10-05"}
        with patch.object(scanner, "db", database), \
                patch.object(scanner, "_completed_late_backup_rows", return_value=None), \
                patch.object(scanner, "call_with_backoff", return_value=self.benchmark("2026-10-05")), \
                patch.object(scanner, "_load_saved_benchmarks", return_value=[]), \
                patch.object(scanner, "_load_daily_scan_doc", return_value={"scan_date": "2026-10-05", "data": []}), \
                patch.object(scanner, "_acquire_scan_lease", return_value=False), \
                patch.object(scanner, "send_daily_notifications") as send:
            self.assertEqual(scanner.run_daily_scan(clock=lambda: local("2026-10-06T00:00:40")), [])
        send.assert_not_called()

    def test_unconfirmed_completed_marker_stops_before_notifications(self):
        database = Mock()
        database.collection.return_value.document.return_value.set.side_effect = OSError("unavailable")
        with patch.object(scanner, "db", database), self.assertRaisesRegex(RuntimeError, "標記完成"):
            scanner._finish_scan_lease("2026-10-05", "completed", 300)

    def test_force_cannot_bypass_intraday_and_closed_day_daytime_gates(self):
        for moment in ("2026-10-06T10:00:00", "2026-10-09T15:00:00"):
            with (
                self.subTest(moment=moment),
                patch.object(scanner, "db", object()),
                patch.object(scanner, "call_with_backoff") as load,
            ):
                with self.assertRaisesRegex(RuntimeError, "盤中禁止"):
                    scanner.run_daily_scan(force=True, clock=lambda: local(moment))
                load.assert_not_called()

    def test_nonforced_intraday_run_does_not_fetch_or_write(self):
        with (
            patch.dict(scanner.os.environ, {"GITHUB_EVENT_NAME": "workflow_dispatch"}),
            patch.object(scanner, "db", object()),
            patch.object(scanner, "call_with_backoff") as load,
        ):
            self.assertEqual(scanner.run_daily_scan(clock=lambda: local("2026-10-06T10:00:00")), [])
        load.assert_not_called()

    def test_scheduled_job_that_missed_preopen_window_fails_instead_of_false_green(self):
        for moment in ("2026-10-06T08:30:00", "2026-10-06T09:00:00", "2026-10-06T10:00:00"):
            with (
                self.subTest(moment=moment),
                patch.dict(scanner.os.environ, {"GITHUB_EVENT_NAME": "schedule"}),
                patch.object(scanner, "db", object()),
                patch.object(scanner, "call_with_backoff") as load,
            ):
                with self.assertRaisesRegex(RuntimeError, "已錯過安全補掃時段"):
                    scanner.run_daily_scan(clock=lambda: local(moment))
                load.assert_not_called()

    def test_scheduled_market_holiday_remains_safe_skip_not_delay_alarm(self):
        with (
            patch.dict(scanner.os.environ, {"GITHUB_EVENT_NAME": "schedule"}),
            patch.object(scanner, "db", object()),
            patch.object(scanner, "call_with_backoff") as load,
        ):
            self.assertEqual(scanner.run_daily_scan(clock=lambda: local("2026-10-09T15:00:00")), [])
        load.assert_not_called()

    def test_local_schedule_has_same_missed_window_guard_without_github_env(self):
        with patch.dict(scanner.os.environ, {"GITHUB_EVENT_NAME": ""}), \
                patch.object(scanner, "db", object()), \
                patch.object(scanner, "call_with_backoff") as load, \
                patch.object(scanner, "_completed_late_backup_rows", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "已錯過安全補掃時段"):
                scanner.run_daily_scan(scheduled=True, clock=lambda: local("2026-10-06T10:00:00"))
        load.assert_not_called()

    def test_publish_deadline_is_rechecked_after_processing_before_first_ranking_write(self):
        clock = Mock(side_effect=[local("2026-10-06T08:20:00"), local("2026-10-06T08:21:00"),
                                  local("2026-10-06T09:00:00")])
        with (
            patch.object(scanner, "db", Mock()),
            patch.object(scanner, "call_with_backoff", return_value=self.benchmark("2026-10-05")),
            patch.object(scanner, "_load_saved_benchmarks", return_value=[]),
            patch.object(scanner, "_load_daily_scan_doc", return_value={}),
            patch.object(scanner, "_acquire_scan_lease", return_value=True),
            patch.object(scanner, "_finish_scan_lease") as finish,
            patch.object(scanner, "scan_universe_limit", return_value=1),
            patch.object(scanner, "fetch_top_stocks", return_value=["2330"]),
            patch.object(scanner, "build_scan_pool", return_value=["2330"]),
            patch.object(scanner, "fetch_stock_data_batch", return_value={}),
            patch.object(scanner.concurrent.futures, "ThreadPoolExecutor") as executor,
            patch.object(scanner, "build_comparison_rows", return_value=[]),
            patch.object(scanner, "_write_daily_scan_doc") as write,
            patch.object(scanner, "update_top10_tracker") as tracker,
            patch.object(scanner, "send_daily_notifications") as send,
        ):
            executor.return_value.__enter__.return_value.map.return_value = [
                {"代號": "2330", "Data_Date": "2026-10-05", "Score": 80, "Prev_Rank": 999},
            ]
            with self.assertRaisesRegex(RuntimeError, "09:00"):
                scanner.run_daily_scan(clock=clock)
        write.assert_not_called()
        tracker.assert_not_called()
        send.assert_not_called()
        self.assertEqual(finish.call_args.args[:3], ("2026-10-05", "failed", 1))

    def test_manifest_commit_guard_blocks_writes_after_deadline(self):
        database = Mock()
        guard = Mock(side_effect=RuntimeError("09:00"))
        with patch.object(scanner, "db", database), self.assertRaisesRegex(RuntimeError, "09:00"):
            scanner._write_daily_scan_doc([], scan_date="2026-10-05", scan_limit=300,
                                          universe_size=300, scan_profile="daily_300", publish_guard=guard)
        database.batch.return_value.commit.assert_not_called()

    def test_successful_recovery_keeps_source_date_and_actual_creation_time(self):
        database = Mock()
        with (
            patch.object(scanner, "db", database),
            patch.object(scanner, "call_with_backoff", return_value=self.benchmark("2026-10-05")),
            patch.object(scanner, "_load_saved_benchmarks", return_value=[]),
            patch.object(scanner, "_load_daily_scan_doc", return_value={}),
            patch.object(scanner, "_acquire_scan_lease", return_value=True),
            patch.object(scanner, "_finish_scan_lease") as finish,
            patch.object(scanner, "scan_universe_limit", return_value=1),
            patch.object(scanner, "fetch_top_stocks", return_value=["2330"]),
            patch.object(scanner, "build_scan_pool", return_value=["2330"]),
            patch.object(scanner, "fetch_stock_data_batch", return_value={}),
            patch.object(scanner.concurrent.futures, "ThreadPoolExecutor") as executor,
            patch.object(scanner, "build_comparison_rows", return_value=[]),
            patch.object(scanner, "select_executable_top10", return_value=[]),
            patch.object(scanner, "_write_daily_scan_doc") as write,
            patch.object(scanner, "update_top10_tracker") as tracker,
            patch.object(scanner, "send_daily_notifications") as send,
        ):
            executor.return_value.__enter__.return_value.map.return_value = [
                {"代號": "2330", "Data_Date": "2026-10-05", "Score": 80, "Prev_Rank": 999},
            ]
            rows = scanner.run_daily_scan(clock=lambda: local("2026-10-06T01:12:00"))
        self.assertEqual(rows[0]["Data_Date"], "2026-10-05")
        self.assertEqual(rows[0]["Scan_Generated_At"], "2026-10-06T01:12:00+08:00")
        self.assertTrue(rows[0]["Scan_Delayed_Recovery"])
        self.assertEqual(write.call_args.kwargs["scan_date"], "2026-10-05")
        self.assertEqual(write.call_args.kwargs["execution"]["data_as_of_date"], "2026-10-05")
        self.assertTrue(callable(write.call_args.kwargs["publish_guard"]))
        self.assertEqual(tracker.call_args.kwargs["execution"], write.call_args.kwargs["execution"])
        self.assertTrue(callable(tracker.call_args.kwargs["publish_guard"]))
        finish.assert_called_once_with("2026-10-05", "completed", 1)
        send.assert_called_once_with(rows, "2026-10-05", resend=False)

    def test_tracker_commit_guard_rechecks_deadline_after_loading_performance(self):
        database = Mock()
        database.collection.return_value.document.return_value.get.return_value.exists = False
        guard = Mock(side_effect=RuntimeError("09:00"))
        with patch.object(scanner, "db", database), self.assertRaisesRegex(RuntimeError, "09:00"):
            scanner.update_top10_tracker([], "2026-10-05", publish_guard=guard)
        guard.assert_called_once_with()
        database.batch.return_value.commit.assert_not_called()

    def test_next_prediction_scan_persists_complete_date_bound_artifacts_and_calls_all_four_senders(self):
        # Only the external inputs, Firestore transport and final Telegram
        # transports are mocked. Publication, tracker updates, completion and
        # the four-notification orchestration run through the real functions.
        cases = (
            ("2026-10-07T15:17:00", "2026-10-07", False),
            ("2026-10-08T01:12:00", "2026-10-07", True),
        )
        for moment, analysis_day, delayed in cases:
            database = Mock()
            database.collection.return_value.document.return_value.get.return_value.exists = False
            notifications = Mock()
            with (
                self.subTest(moment=moment),
                patch.dict(scanner.os.environ, {"GITHUB_EVENT_NAME": "schedule", "FORCE_SCAN": "0"}),
                patch.object(scanner, "db", database),
                patch.object(scanner, "call_with_backoff", return_value=self.benchmark(analysis_day)),
                patch.object(scanner, "_load_saved_benchmarks", return_value=[]),
                patch.object(scanner, "_load_daily_scan_doc", return_value={}),
                patch.object(scanner, "_acquire_scan_lease", return_value=True) as lease,
                patch.object(scanner, "scan_universe_limit", return_value=1),
                patch.object(scanner, "fetch_top_stocks", return_value=["2330"]),
                patch.object(scanner, "build_scan_pool", return_value=["2330"]),
                patch.object(scanner, "fetch_stock_data_batch", return_value={}),
                patch.object(scanner, "get_stock_data", side_effect=AssertionError("offline test must not fetch")),
                patch.object(scanner.concurrent.futures, "ThreadPoolExecutor") as executor,
                patch.object(scanner, "build_comparison_rows", return_value=[]),
                patch.object(scanner, "select_executable_top10", return_value=[]),
                patch.object(scanner, "send_daily_top10_notification", notifications.top10),
                patch.object(scanner, "send_daily_executable_notification", notifications.prediction),
                patch.object(scanner, "send_daily_tracking_performance_notification", notifications.performance),
                patch.object(scanner, "send_daily_research_notification", notifications.research),
            ):
                executor.return_value.__enter__.return_value.map.return_value = [
                    {"代號": "2330", "Data_Date": analysis_day, "Score": 80, "Prev_Rank": 999},
                ]
                rows = scanner.run_daily_scan(clock=lambda: local(moment))

            lease.assert_called_once_with(analysis_day, force=False)
            self.assertEqual(rows[0]["Data_Date"], analysis_day)
            self.assertEqual(rows[0]["Scan_Generated_At"], local(moment).isoformat())
            self.assertEqual(rows[0]["Scan_Delayed_Recovery"], delayed)
            batch = database.batch.return_value
            self.assertEqual(batch.commit.call_count, 2)  # scan and tracker atomic batches
            manifests = [call.args[1] for call in batch.set.call_args_list
                         if call.args[1].get("scan_date") == analysis_day]
            self.assertEqual(len(manifests), 2)  # latest manifest and dated scan history
            for manifest in manifests:
                self.assertEqual(manifest["record_count"], 1)
                self.assertEqual(manifest["content_hash"], scanner._records_content_hash(rows))
                self.assertEqual(manifest["data_as_of_date"], analysis_day)
                self.assertEqual(manifest["generated_at"], local(moment).isoformat())
                self.assertEqual(manifest["delayed_recovery"], delayed)
            performance = [call.args[1]["data"] for call in batch.set.call_args_list
                           if isinstance(call.args[1].get("data"), dict)
                           and call.args[1]["data"].get("date") == analysis_day]
            self.assertEqual(len(performance), 1)
            self.assertEqual(performance[0]["ranking_status"], "ok")
            self.assertEqual(performance[0]["generated_at"], local(moment).isoformat())
            direct_writes = database.collection.return_value.document.return_value.set.call_args_list
            completed = [call.args[0] for call in direct_writes if call.args[0].get("status") == "completed"]
            self.assertEqual(len(completed), 1)
            self.assertEqual(completed[0]["trading_date"], analysis_day)
            self.assertEqual(completed[0]["result_count"], 1)
            self.assertEqual([call[0] for call in notifications.mock_calls],
                             ["top10", "prediction", "performance", "research"])
            for sender in (notifications.top10, notifications.prediction, notifications.research):
                sender.assert_called_once_with(rows, analysis_day, resend=False)
            notifications.performance.assert_called_once_with(analysis_day, resend=False)

    def test_manifest_retains_delayed_generation_provenance(self):
        database = Mock()
        metadata = execution_metadata(scan_window(local("2026-10-06T01:00:00")), local("2026-10-06T01:10:00"))
        with patch.object(scanner, "db", database):
            scanner._write_daily_scan_doc([], scan_date="2026-10-05", scan_limit=300,
                                          universe_size=300, scan_profile="daily_300", execution=metadata)
        manifests = [call.args[1] for call in database.batch.return_value.set.call_args_list
                     if call.args[1].get("scan_date") == "2026-10-05"]
        self.assertEqual(len(manifests), 2)
        for manifest in manifests:
            self.assertEqual(manifest["generated_at"], "2026-10-06T01:10:00+08:00")
            self.assertEqual(manifest["data_as_of_date"], "2026-10-05")
            self.assertTrue(manifest["delayed_recovery"])

    def test_both_prediction_senders_pass_verified_recovery_note_even_when_new_top10_empty(self):
        from tests.test_scanner_telegram import _Database, _image_sender, _transactional

        rows = [{"代號": "2330", "Data_Date": "2026-10-05", "Scan_Delayed_Recovery": True,
                 "Scan_Generated_At": "2026-10-06T00:40:00+08:00"}]
        with (
            patch("firebase_admin.firestore.transactional", _transactional),
            patch.object(scanner, "db", _Database()),
            patch.object(scanner, "datetime") as clock,
            patch.object(scanner, "select_executable_top10", return_value=[]),
            patch.object(scanner, "build_comparison_rows", return_value=[]),
            patch.object(scanner, "_telegram_credentials", return_value=("test-token", "test-chat")),
            patch.object(scanner, "send_top10_photo", side_effect=_image_sender(1)) as top10,
            patch.object(scanner, "send_executable_photo", side_effect=_image_sender(2)) as comparison,
        ):
            clock.now.return_value = local("2026-10-06T01:00:00")
            scanner.send_daily_top10_notification(rows, "2026-10-05")
            scanner.send_daily_executable_notification(rows, "2026-10-05")
        self.assertEqual(top10.call_args.kwargs["publication_note"], comparison.call_args.kwargs["publication_note"])
        self.assertIn("實際生成 2026-10-06 00:40:00", top10.call_args.kwargs["publication_note"])


if __name__ == "__main__":
    unittest.main()
