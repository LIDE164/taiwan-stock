"""Offline tests: no Firebase connections or real Telegram posts."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock, patch

from prediction_notifications import run_prediction_notifications, WATCH_SCHEMA
from research_delivery import DeliveryRejected, DeliveryUncertain
from tests.test_research_delivery import _Database, _transactional

TPE = timezone(timedelta(hours=8))


def moment(hour=10, minute=5):
    return datetime(2026, 10, 5, hour, minute, tzinfo=TPE)


def rows():
    return [{"代號": "2330", "名稱": "台積電", "Data_Date": "2026-10-02",
             "Revenue_Source": "TWSE", "Execution_Versions": ["legacy"], "Execution_Version_Label": "舊制"}]


def quotes(now=None, final=False):
    now = now or moment()
    return {"2330": {"open": 100, "price": 101, "previous_close": 99,
                     "observed_at": now.isoformat(), "date": "2026-10-05", "source": "測試來源",
                     "status": "ok", "final": final}}


class PredictionNotificationTests(unittest.TestCase):
    def setUp(self):
        self.db = _Database()
        self.manifest = Mock(return_value={"scan_date": "2026-10-02", "data": [{"original": True}]})
        self.lock = Mock(return_value={"status": "completed", "trading_date": "2026-10-02"})
        self.clock = Mock(return_value=moment())
        self.quotes = Mock(return_value=quotes())
        self.sender = Mock(return_value=101)
        self.selector = patch("prediction_notifications.select_prediction_rows", return_value=rows()).start()
        patch("firebase_admin.firestore.transactional", _transactional).start()
        patch("research_delivery.requests.post", side_effect=AssertionError("No real posts")).start()
        self.addCleanup(patch.stopall)

    def run_task(self, **kwargs):
        return run_prediction_notifications(db=self.db, load_manifest=self.manifest, load_lock=self.lock,
                                            send_text=self.sender, load_quotes=self.quotes, clock=self.clock, **kwargs)

    @property
    def state(self):
        return self.db.documents[("notifications", "prediction_prices_2026-10-05_1000")]

    def test_default_dry_run_has_no_writes_or_telegram_posts(self):
        result = self.run_task()
        self.assertEqual(result["status"], "preview")
        self.assertIn("2330", "\n".join(result["messages"]))
        self.assertEqual(self.db.documents, {})
        self.sender.assert_not_called()

    def test_success_freezes_only_watchlist_and_deduplicates_same_hour(self):
        self.assertEqual(self.run_task(dry_run=False)["status"], "sent")
        self.assertEqual(self.state["format"], "prediction_hourly_prices_v1")
        self.assertEqual(self.state["sent_parts"], {"1": 101})
        self.assertEqual(set(key[0] for key in self.db.documents), {"notifications", "prediction_watchlists"})
        self.manifest.side_effect = AssertionError("do not replace a frozen list")
        self.quotes.side_effect = AssertionError("do not fetch quotes for a sent slot")
        self.assertEqual(self.run_task(dry_run=False)["status"], "already_sent_or_busy")
        self.sender.assert_called_once()

    def test_next_hour_uses_same_stocks_without_reranking(self):
        self.run_task(dry_run=False)
        frozen = deepcopy(self.db.documents[("prediction_watchlists", "2026-10-05")])
        self.clock.return_value = moment(11)
        self.quotes.return_value = quotes(moment(11))
        self.sender.return_value = 102
        self.manifest.side_effect = AssertionError("latest scan must not replace the day's list")
        self.assertEqual(self.run_task(dry_run=False)["status"], "sent")
        self.selector.assert_called_once()
        self.assertEqual(self.quotes.call_args.args[0], frozen["rows"])
        self.assertEqual(self.db.documents[("prediction_watchlists", "2026-10-05")], frozen)
        self.assertEqual(self.sender.call_count, 2)

    def test_partial_rejection_resumes_only_unsent_frozen_parts(self):
        with patch("prediction_notifications.format_prediction_messages", return_value=["first", "second"]):
            self.sender.side_effect = [101, DeliveryRejected("rejected")]
            with self.assertRaises(DeliveryRejected):
                self.run_task(dry_run=False)
        self.quotes.side_effect = AssertionError("do not rebuild partial delivery")
        self.sender.side_effect = [102]
        self.assertEqual(self.run_task(dry_run=False)["status"], "sent")
        self.assertEqual(self.state["sent_parts"], {"1": 101, "2": 102})
        self.assertEqual(self.sender.call_args.args[0], "second")

    def test_uncertain_delivery_never_blindly_repeats(self):
        self.sender.side_effect = DeliveryUncertain("timeout")
        with self.assertRaises(DeliveryUncertain):
            self.run_task(dry_run=False)
        self.assertEqual(self.state["status"], "uncertain")
        with self.assertRaises(DeliveryUncertain):
            self.run_task(dry_run=False)
        self.sender.assert_called_once()

    def test_outside_session_skips_before_loading_cloud(self):
        for now in (moment(8), moment(15), moment().replace(day=9), moment().replace(year=2027)):
            self.clock.return_value = now
            self.assertEqual(self.run_task(dry_run=False)["status"], "outside_session")
        self.manifest.assert_not_called()
        self.quotes.assert_not_called()
        self.sender.assert_not_called()
        self.assertFalse(self.db.documents)

    def test_scan_must_be_completed_before_freezing(self):
        self.lock.return_value["status"] = "running"
        with self.assertRaises(ValueError):
            self.run_task(dry_run=False)
        self.assertFalse(self.db.documents)
        self.sender.assert_not_called()

    def test_empty_list_stays_empty_without_hourly_spam(self):
        self.selector.return_value = []
        self.assertEqual(self.run_task(dry_run=False)["status"], "empty_prediction")
        self.assertEqual(self.run_task(dry_run=False)["status"], "empty_prediction")
        self.quotes.assert_not_called()
        self.sender.assert_not_called()

    def test_frozen_stale_corrupt_or_duplicate_data_is_not_used(self):
        valid = {"schema": WATCH_SCHEMA, "trading_date": "2026-10-05", "analysis_date": "2026-10-02", "rows": rows()}
        invalids = [{**valid, "schema": "unknown"}, {**valid, "analysis_date": "2026-10-01"},
                    {**valid, "rows": [*rows(), *rows()]}, {**valid, "rows": [None]}]
        for value in invalids:
            self.db.documents[("prediction_watchlists", "2026-10-05")] = value
            with self.assertRaises(ValueError):
                self.run_task(dry_run=False)
        self.sender.assert_not_called()

    def test_no_valid_open_quote_waits_for_backup_then_reports_missing(self):
        self.clock.return_value = moment(9)
        self.quotes.return_value = {}
        self.assertEqual(self.run_task(dry_run=False)["status"], "quotes_pending")
        self.sender.assert_not_called()
        self.clock.return_value = moment(9, 20)
        self.assertEqual(self.run_task(dry_run=False)["status"], "quotes_pending")
        self.clock.return_value = moment(9, 35)
        self.assertEqual(self.run_task(dry_run=False)["status"], "sent")

    def test_close_waits_for_confirmation_and_late_retry_shows_unavailable(self):
        self.clock.return_value = moment(13, 35)
        self.quotes.return_value = quotes(moment(13, 20))
        self.assertEqual(self.run_task(dry_run=False)["status"], "quotes_pending")
        self.sender.assert_not_called()
        self.clock.return_value = moment(14, 5)
        self.assertEqual(self.run_task(dry_run=False)["status"], "sent")
        self.assertIn("收盤待確認", self.sender.call_args.args[0])

    def test_confirmed_close_sent_once(self):
        self.clock.return_value = moment(13, 35)
        self.quotes.return_value = quotes(moment(13, 30), final=True)
        self.assertEqual(self.run_task(dry_run=False)["status"], "sent")
        self.assertEqual(self.run_task(dry_run=False)["status"], "already_sent_or_busy")
        self.sender.assert_called_once()

    def test_clock_leaving_slot_during_fetch_never_sends_previous_hour(self):
        self.clock.side_effect = [moment(10, 59), moment(10, 59), moment(11, 0)]
        self.assertEqual(self.run_task(dry_run=False)["status"], "slot_expired")
        self.sender.assert_not_called()
        self.assertEqual(self.state["in_flight"], "")

    def test_no_firestore_cannot_send_without_deduplication(self):
        self.db = None
        with self.assertRaises(RuntimeError):
            self.run_task(dry_run=False)
        self.sender.assert_not_called()


if __name__ == "__main__":
    unittest.main()
