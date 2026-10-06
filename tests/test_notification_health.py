"""Operational alerts must never bypass transactional Telegram deduplication."""
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock, patch

from notification_health import build_health_message, send_health_alert
from research_delivery import DeliveryRejected, DeliveryUncertain
from tests.test_research_delivery import _Database, _transactional

TPE = timezone(timedelta(hours=8))


class HealthAlertTests(unittest.TestCase):
    def setUp(self):
        self.db = _Database()
        self.sender = Mock(return_value=77)
        self.now = datetime(2026, 10, 6, 10, 20, tzinfo=TPE)
        patch("firebase_admin.firestore.transactional", _transactional).start()
        patch("research_delivery.requests.post", side_effect=AssertionError("No real Telegram posts")).start()
        self.addCleanup(patch.stopall)

    def send(self, **kwargs):
        return send_health_alert(db=self.db, send_text=self.sender, now=self.now, **kwargs)

    def test_one_daily_notice_independent_of_run_id(self):
        self.assertTrue(self.send(analysis_date="2026-10-02", repository="owner/repo", run_id="123"))
        self.assertFalse(self.send(run_id="456"))
        self.sender.assert_called_once()
        self.assertIn("2026-10-02", self.sender.call_args.args[0])
        self.assertEqual(set(self.db.documents), {("notifications", "prediction_health_2026-10-06")})
        self.now += timedelta(days=1)
        self.assertTrue(self.send())

    def test_failure_can_retry_but_ambiguous_delivery_cannot(self):
        self.sender.side_effect = [DeliveryRejected("rejected"), 77]
        with self.assertRaises(DeliveryRejected):
            self.send()
        self.assertTrue(self.send())
        self.now += timedelta(days=1)
        self.sender.side_effect = DeliveryUncertain("unknown")
        with self.assertRaises(DeliveryUncertain):
            self.send()
        calls = self.sender.call_count
        with self.assertRaises(DeliveryUncertain):
            self.send()
        self.assertEqual(self.sender.call_count, calls)

    def test_unavailable_ledger_never_sends_bare_alert(self):
        self.db = None
        with self.assertRaises(RuntimeError):
            self.send()
        self.sender.assert_not_called()

    def test_message_accepts_only_strict_dates_and_run_links(self):
        message = build_health_message(now=self.now, analysis_date="token-secret",
                                       repository="secret invalid", run_id="bad-id")
        self.assertNotIn("token-secret", message)
        self.assertNotIn("secret invalid", message)
        self.assertNotIn("bad-id", message)
        self.assertIn("不會用現在價格補造", message)

    def test_naive_clock_fails_before_any_post(self):
        self.now = self.now.replace(tzinfo=None)
        with self.assertRaises(ValueError):
            self.send()
        self.sender.assert_not_called()


if __name__ == "__main__":
    unittest.main()
