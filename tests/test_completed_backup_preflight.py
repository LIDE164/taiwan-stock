"""Offline regression for a completed backup failing on a stale Yahoo index.

The 2026-10-07 03:35 incident must short-circuit on verified durable evidence
before any fresh provider lookup. No fixture is written to an external service.
"""

from copy import deepcopy
from datetime import datetime
import unittest
from unittest.mock import Mock, patch

import pandas as pd

import scanner
from scan_schedule import TPE
from research_delivery import report_fingerprint
from tests.test_scan_completion import receipts
from tests.test_scanner_telegram import _Database


def local(value):
    return datetime.fromisoformat(value).replace(tzinfo=TPE)


class CompletedBackupPreflightTests(unittest.TestCase):
    def database(self, day="2026-10-06", *, missing_receipt=None, uncertain=False):
        database = _Database()
        rows = [{"代號": "2330", "Data_Date": day}, {"代號": "2317", "Data_Date": day}]
        manifest = {"scan_date": day, "data": deepcopy(rows), "content_hash": scanner._records_content_hash(rows)}
        database.collection("market_data").document("daily_scan").set(manifest)
        database.collection("system_locks").document("daily_scan").set({
            "status": "completed", "trading_date": day,
        })
        for kind, value in receipts().items():
            if kind == missing_receipt:
                continue
            value["date"] = day
            if kind == "daily_research":
                value["fingerprint"] = report_fingerprint(rows, day)
            if uncertain and kind == "daily_research":
                value.update(status="uncertain", in_flight="2")
            database.collection("notifications").document(f"{kind}_{day}").set(value)

        # Real scanner loaders and receipt validators read this fake store;
        # all durable operations are forbidden after fixture construction.
        database.batch = Mock(side_effect=AssertionError("backup preflight must not create a batch"))
        database.transaction = Mock(side_effect=AssertionError("backup preflight must not acquire a transaction"))
        for collection in database.collections.values():
            for document in collection.documents.values():
                document.get = Mock(wraps=document.get)
                document.set = Mock(side_effect=AssertionError("backup preflight must not write"))
        return database, rows

    def assert_read_only_completed(self, moment, day):
        database, rows = self.database(day)
        manifest = database.collection("market_data").document("daily_scan")
        with (
            patch.dict(scanner.os.environ, {"GITHUB_EVENT_NAME": "schedule", "FORCE_SCAN": "0"}),
            patch.object(scanner, "db", database),
            patch.object(scanner.yf, "Ticker", side_effect=AssertionError("Yahoo must not run for a completed backup")) as yahoo,
            patch.object(scanner, "_acquire_scan_lease", side_effect=AssertionError("must not acquire")) as lease,
            patch.object(scanner, "_write_daily_scan_doc", side_effect=AssertionError("must not write")) as write,
            patch.object(scanner, "update_top10_tracker", side_effect=AssertionError("must not update tracker")) as tracker,
            patch.object(scanner, "_finish_scan_lease", side_effect=AssertionError("must not change completed lock")) as finish,
            patch.object(scanner, "send_daily_notifications", side_effect=AssertionError("must not resend")) as send,
        ):
            result = scanner.run_daily_scan(clock=lambda: local(moment))
        self.assertEqual(result, rows)
        self.assertEqual(manifest.value["data"], rows)
        self.assertGreaterEqual(manifest.get.call_count, 1)
        for kind in receipts():
            document = database.collection("notifications").document(f"{kind}_{day}")
            self.assertGreaterEqual(document.get.call_count, 1)
        for operation in (yahoo, lease, write, tracker, finish, send, database.batch, database.transaction):
            operation.assert_not_called()
        for collection in database.collections.values():
            for document in collection.documents.values():
                document.set.assert_not_called()

    def test_0335_completed_prior_day_returns_before_yahoo_history(self):
        self.assert_read_only_completed("2026-10-07T03:35:00", "2026-10-06")

    def test_same_day_postclose_completed_backup_does_not_revalidate_provider(self):
        self.assert_read_only_completed("2026-10-07T17:17:00", "2026-10-07")

    def test_readonly_completion_is_not_a_new_publication_when_receipts_arrive_after_open(self):
        database, rows = self.database()
        clock = Mock(side_effect=[local("2026-10-07T08:29:59"), local("2026-10-07T09:00:01")])
        with (
            patch.dict(scanner.os.environ, {"GITHUB_EVENT_NAME": "schedule", "FORCE_SCAN": "0"}),
            patch.object(scanner, "db", database),
            patch.object(scanner.yf, "Ticker", side_effect=AssertionError("no quote lookup")) as yahoo,
            patch.object(scanner, "_acquire_scan_lease", side_effect=AssertionError("no lease")) as lease,
            patch.object(scanner, "_write_daily_scan_doc", side_effect=AssertionError("no write")) as write,
            patch.object(scanner, "send_daily_notifications", side_effect=AssertionError("no resend")) as send,
        ):
            self.assertEqual(scanner.run_daily_scan(clock=clock), rows)
        yahoo.assert_not_called()
        lease.assert_not_called()
        write.assert_not_called()
        send.assert_not_called()
        clock.assert_called_once_with()
        database.batch.assert_not_called()
        database.transaction.assert_not_called()

    def test_weekend_and_holiday_preopen_use_the_verified_scan_window_target(self):
        for moment in ("2026-10-09T03:35:00", "2026-10-10T03:35:00", "2026-10-11T03:35:00"):
            with self.subTest(moment=moment):
                self.assert_read_only_completed(moment, "2026-10-08")

    def assert_stale_provider_still_rejected(self, database, moment, expected_day, provider_day):
        provider = Mock()
        provider.history.return_value = pd.DataFrame({"Close": [23000.0]}, index=pd.to_datetime([provider_day]))
        with (
            patch.dict(scanner.os.environ, {"GITHUB_EVENT_NAME": "schedule", "FORCE_SCAN": "0"}),
            patch.object(scanner, "db", database),
            patch.object(scanner.yf, "Ticker", return_value=provider) as yahoo,
            patch.object(scanner, "_acquire_scan_lease", side_effect=AssertionError("stale source cannot acquire")) as lease,
            patch.object(scanner, "_write_daily_scan_doc", side_effect=AssertionError("must not write")) as write,
            patch.object(scanner, "send_daily_notifications", side_effect=AssertionError("must not send")) as send,
        ):
            with self.assertRaisesRegex(RuntimeError, f"預期 {expected_day}，取得 {provider_day}"):
                scanner.run_daily_scan(clock=lambda: local(moment))
        yahoo.assert_called_once_with("^TWII")
        provider.history.assert_called_once_with(period="4mo")
        for operation in (lease, write, send, database.batch, database.transaction):
            operation.assert_not_called()

    def test_earlier_completed_scan_does_not_satisfy_current_preopen_target(self):
        database, _ = self.database("2026-10-05")
        self.assert_stale_provider_still_rejected(database, "2026-10-07T03:35:00", "2026-10-06", "2026-10-05")

    def test_new_postclose_session_cannot_short_circuit_using_prior_day_completion(self):
        database, _ = self.database("2026-10-06")
        self.assert_stale_provider_still_rejected(database, "2026-10-07T15:17:00", "2026-10-07", "2026-10-06")

    def test_each_missing_receipt_keeps_normal_provider_freshness_gate(self):
        for kind in receipts():
            with self.subTest(missing_receipt=kind):
                database, _ = self.database(missing_receipt=kind)
                self.assert_stale_provider_still_rejected(database, "2026-10-07T03:35:00", "2026-10-06", "2026-10-05")

    def test_ambiguous_research_receipt_does_not_certify_completed_backup(self):
        database, _ = self.database(uncertain=True)
        self.assert_stale_provider_still_rejected(database, "2026-10-07T03:35:00", "2026-10-06", "2026-10-05")

    def test_changed_ranking_cannot_borrow_same_day_old_delivery_receipts(self):
        database, _ = self.database()
        manifest = database.collection("market_data").document("daily_scan").value
        manifest["data"][0]["Score"] = 99  # offline replacement snapshot, same analysis date
        manifest["content_hash"] = scanner._records_content_hash(manifest["data"])
        self.assert_stale_provider_still_rejected(database, "2026-10-07T03:35:00", "2026-10-06", "2026-10-05")

    def test_missing_or_old_research_fingerprint_does_not_certify_this_ranking(self):
        for fingerprint in (None, "old-ranking-fingerprint"):
            with self.subTest(fingerprint=fingerprint):
                database, _ = self.database()
                research = database.collection("notifications").document("daily_research_2026-10-06").value
                if fingerprint is None:
                    research.pop("fingerprint")
                else:
                    research["fingerprint"] = fingerprint
                self.assert_stale_provider_still_rejected(database, "2026-10-07T03:35:00", "2026-10-06", "2026-10-05")

    def test_inconsistent_manifest_content_hash_is_not_verified_completion(self):
        database, _ = self.database()
        database.collection("market_data").document("daily_scan").value["content_hash"] = "wrong-content-hash"
        self.assert_stale_provider_still_rejected(database, "2026-10-07T03:35:00", "2026-10-06", "2026-10-05")

    def test_force_resend_and_offline_intraday_override_are_not_swallowed_by_shortcut(self):
        for options, environment in (({"force": True}, {}), ({"resend_telegram": True}, {}),
                                     ({"allow_intraday": True}, {}), ({}, {"FORCE_SCAN": "1"})):
            with self.subTest(options=options, environment=environment):
                database, _ = self.database()
                provider = Mock()
                provider.history.return_value = pd.DataFrame()
                with (
                    patch.dict(scanner.os.environ, {"GITHUB_EVENT_NAME": "schedule", "FORCE_SCAN": "0", **environment}),
                    patch.object(scanner, "db", database),
                    patch.object(scanner.yf, "Ticker", return_value=provider) as yahoo,
                    patch.object(scanner, "_acquire_scan_lease", side_effect=AssertionError("empty provider cannot acquire")) as lease,
                    patch.object(scanner, "send_daily_notifications", side_effect=AssertionError("must not send")) as send,
                ):
                    with self.assertRaisesRegex(RuntimeError, "無法確認最新實際交易日"):
                        scanner.run_daily_scan(clock=lambda: local("2026-10-07T03:35:00"), **options)
                yahoo.assert_called_once_with("^TWII")
                provider.history.assert_called_once_with(period="4mo")
                lease.assert_not_called()
                send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
