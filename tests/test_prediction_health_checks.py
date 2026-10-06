"""Offline health checks: receipts are read but never modified or sent."""

from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import Mock

from prediction_health_checks import has_completed_due_notifications, SLOT_DUE_TIMES
from tests.test_research_delivery import _Database


def moment(clock="18:18:00", day="2026-10-06"):
    return datetime.fromisoformat(f"{day}T{clock}+08:00")


def receipt(slot, day="2026-10-06"):
    return {"status": "sent", "format": "prediction_hourly_prices_v1", "date": day,
            "metadata": {"trading_date": day, "slot": slot},
            "parts": ["第一段", "第二段"], "part_count": 2,
            "sent_parts": {"1": 100, "2": 101}, "in_flight": ""}


class PredictionHealthCheckTests(unittest.TestCase):
    def setUp(self):
        self.db = _Database()
        for _, slot in SLOT_DUE_TIMES:
            self.db.documents[("notifications", f"prediction_prices_2026-10-06_{slot}")] = receipt(slot)

    def test_all_due_confirmed_notices_make_late_backup_harmless(self):
        self.assertTrue(has_completed_due_notifications(self.db, moment()))

    def test_only_due_slots_are_required_at_each_boundary(self):
        db = _Database()
        for threshold, slot in SLOT_DUE_TIMES:
            now = moment(threshold.isoformat())
            self.assertFalse(has_completed_due_notifications(db, now))
            db.documents[("notifications", f"prediction_prices_2026-10-06_{slot}")] = receipt(slot)
            self.assertTrue(has_completed_due_notifications(db, now))

    def test_a_gap_before_next_hour_still_checks_previous_due_slot(self):
        db = _Database()
        db.documents[("notifications", "prediction_prices_2026-10-06_0900")] = receipt("0900")
        self.assertTrue(has_completed_due_notifications(db, moment("10:04:59")))
        self.assertFalse(has_completed_due_notifications(db, moment("10:05:00")))

    def test_missing_any_due_slot_is_not_complete(self):
        for _, slot in SLOT_DUE_TIMES:
            with self.subTest(slot=slot):
                saved = self.db.documents.pop(("notifications", f"prediction_prices_2026-10-06_{slot}"))
                self.assertFalse(has_completed_due_notifications(self.db, moment()))
                self.db.documents[("notifications", f"prediction_prices_2026-10-06_{slot}")] = saved

    def test_pending_failed_uncertain_or_missing_status_never_counts_as_sent(self):
        key = ("notifications", "prediction_prices_2026-10-06_0900")
        for status in (None, "preparing", "ready", "sending", "failed", "uncertain"):
            with self.subTest(status=status):
                self.db.documents[key] = {**receipt("0900"), "status": status}
                self.assertFalse(has_completed_due_notifications(self.db, moment()))

    def test_corrupt_or_incomplete_receipts_are_rejected(self):
        key = ("notifications", "prediction_prices_2026-10-06_0900")
        invalids = (
            {"format": "daily_research_three_facets_v3"}, {"date": "2026-10-05"},
            {"in_flight": "2"}, {"metadata": None},
            {"metadata": {"trading_date": "2026-10-05", "slot": "0900"}},
            {"metadata": {"trading_date": "2026-10-06", "slot": "1000"}},
            {"parts": []}, {"parts": [""]}, {"parts": [None, "second"]},
            {"parts": "not a list"}, {"parts": ["part"] * 26, "part_count": 26},
            {"part_count": True}, {"part_count": 1}, {"part_count": "2"},
            {"sent_parts": None}, {"sent_parts": {"1": 100}},
            {"sent_parts": {"1": 100, "2": 101, "3": 102}},
            {"sent_parts": {1: 100, 2: 101}},
            {"sent_parts": {"1": 100, "2": 100}},
        )
        for changes in invalids:
            with self.subTest(changes=changes):
                self.db.documents[key] = {**receipt("0900"), **changes}
                self.assertFalse(has_completed_due_notifications(self.db, moment()))

    def test_message_ids_must_be_confirmed_positive_integers(self):
        key = ("notifications", "prediction_prices_2026-10-06_0900")
        for message_id in (None, 0, -1, True, "101", 101.0, [], {}):
            with self.subTest(message_id=message_id):
                self.db.documents[key] = {**receipt("0900"), "sent_parts": {"1": 100, "2": message_id}}
                self.assertFalse(has_completed_due_notifications(self.db, moment()))

    def test_timezone_conversion_uses_taipei_date(self):
        self.assertTrue(has_completed_due_notifications(self.db, moment().astimezone(timezone.utc)))
        # UTC 16:01 is already the next Taipei day; never borrow yesterday's receipts.
        self.assertFalse(has_completed_due_notifications(self.db, datetime(2026, 10, 6, 16, 1, tzinfo=timezone.utc)))
        self.assertFalse(has_completed_due_notifications(self.db, moment(day="2026-10-07")))

    def test_no_due_slots_naive_clock_and_closed_sessions_do_not_read_database(self):
        db = Mock()
        for now in (moment("09:04:59"), moment("00:01:00"), moment().replace(tzinfo=None),
                    moment(day="2026-10-09"), moment(day="2026-10-10"), moment(day="2027-10-06"), None):
            with self.subTest(now=now):
                self.assertFalse(has_completed_due_notifications(db, now))
        db.collection.assert_not_called()

    def test_database_unavailable_or_read_failure_is_not_confirmation(self):
        self.assertFalse(has_completed_due_notifications(None, moment()))
        db = Mock()
        db.collection.side_effect = RuntimeError("private client details")
        self.assertFalse(has_completed_due_notifications(db, moment()))

    def test_empty_watchlist_is_not_assumed_to_be_a_completed_delivery(self):
        db = _Database()
        db.documents[("prediction_watchlists", "2026-10-06")] = {
            "schema": "prediction_watchlist_v1", "trading_date": "2026-10-06", "rows": [],
        }
        self.assertFalse(has_completed_due_notifications(db, moment()))

    def test_verified_empty_watchlist_has_no_required_notices(self):
        db = _Database()
        db.documents[("prediction_watchlists", "2026-10-06")] = {
            "schema": "prediction_watchlist_v1", "trading_date": "2026-10-06", "rows": [],
            "analysis_date": "2026-10-05", "frozen_at": "2026-10-06T09:05:00+08:00",
        }
        original = deepcopy(db.documents)
        self.assertTrue(has_completed_due_notifications(db, moment()))
        self.assertEqual(db.documents, original)

    def test_invalid_empty_watchlist_never_hides_missing_receipts(self):
        db = _Database()
        valid = {"schema": "prediction_watchlist_v1", "trading_date": "2026-10-06", "rows": [],
                 "analysis_date": "2026-10-05", "frozen_at": "2026-10-06T09:05:00+08:00"}
        invalids = (
            {"schema": "unknown"}, {"trading_date": "2026-10-05"},
            {"rows": None}, {"rows": ()}, {"rows": ""}, {"rows": [{"代號": "2330"}]},
            {"analysis_date": None}, {"analysis_date": "bad"}, {"analysis_date": "20261005"},
            {"analysis_date": "2026-10-05T00:00:00"}, {"analysis_date": "2026-10-02"},
            {"analysis_date": "2026-10-06"}, {"analysis_date": "2026-10-04"},
            {"frozen_at": None}, {"frozen_at": "bad"}, {"frozen_at": "2026-10-06T09:05:00"},
            {"frozen_at": "2026-10-05T09:05:00+08:00"}, {"frozen_at": "2026-10-07T09:05:00+08:00"},
            {"frozen_at": "2026-10-06T18:18:01+08:00"},
        )
        for changes in invalids:
            with self.subTest(changes=changes):
                db.documents[("prediction_watchlists", "2026-10-06")] = {**valid, **changes}
                self.assertFalse(has_completed_due_notifications(db, moment()))

    def test_empty_watchlist_freeze_time_uses_taipei_timezone(self):
        db = _Database()
        db.documents[("prediction_watchlists", "2026-10-06")] = {
            "schema": "prediction_watchlist_v1", "trading_date": "2026-10-06", "rows": [],
            "analysis_date": "2026-10-05", "frozen_at": "2026-10-06T01:05:00Z",
        }
        self.assertTrue(has_completed_due_notifications(db, moment()))
        self.assertFalse(has_completed_due_notifications(db, moment("09:04:59")))

    def test_read_only_does_not_modify_receipts_or_other_collections(self):
        self.db.documents[("market_data", "daily_scan")] = {"keep": "unchanged"}
        original = deepcopy(self.db.documents)
        self.assertTrue(has_completed_due_notifications(self.db, moment()))
        self.assertEqual(self.db.documents, original)


if __name__ == "__main__":
    unittest.main()
