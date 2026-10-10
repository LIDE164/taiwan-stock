"""Offline cross-runner image lease/intent/receipt regression tests."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import threading
import unittest
from unittest.mock import Mock, patch

import requests

from image_delivery import SCHEMA, FirestoreImageStore, deliver_images
from research_delivery import DeliveryRejected, DeliveryUncertain
from tests.test_research_delivery import _Database, _transactional


class ImageDeliveryTests(unittest.TestCase):
    def setUp(self):
        for target, replacement in (
            ("firebase_admin.firestore.transactional", _transactional),
            ("requests.sessions.Session.request", Mock(side_effect=AssertionError("network forbidden"))),
        ):
            patched = patch(target, replacement)
            patched.start()
            self.addCleanup(patched.stop)
        self.db = _Database()
        self.date = "2026-10-06"
        self.now = datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)
        self.store = self.make_store()

    def make_store(self, namespace="daily_tracking_performance"):
        return FirestoreImageStore(self.db, namespace, self.date, clock=lambda: self.now)

    @property
    def state(self):
        return self.db.documents[self.store.ref.key]

    def sender(self, count=2, *, posted=None, failure=None):
        def send(skip, before, after):
            for page in range(1, count + 1):
                if page in skip:
                    continue
                before(page, count)
                if posted is not None:
                    posted.append(page)
                if failure is not None and page == count:
                    raise failure
                after(page, 100 + page, count)
        return Mock(side_effect=send)

    def deliver(self, sender, *, count=2, fingerprint="fixed", **kwargs):
        return deliver_images(self.store, fingerprint, page_count=count, send_pages=sender, **kwargs)

    def test_success_metadata_and_confirmed_receipts_deduplicate(self):
        posted = []
        send = self.sender(posted=posted)
        self.assertTrue(self.deliver(send, metadata={"data_count": 192, "description": "真實行情"}))
        self.assertFalse(self.deliver(send))
        self.assertEqual(posted, [1, 2])
        send.assert_called_once()
        self.assertEqual(self.state["sent_pages"], {"1": 101, "2": 102})
        self.assertEqual(self.state["message_id"], 101)
        self.assertEqual(self.state["data_count"], 192)
        self.assertEqual(self.state["image_delivery_schema"], SCHEMA)
        self.assertEqual(self.state["status"], "sent")
        self.assertEqual(self.state["owner"], "")
        self.assertIsNone(self.state["lease_until"])
        self.assertEqual(list(self.db.documents), [self.store.ref.key])

    def test_concurrent_runners_allow_exactly_one_sender(self):
        start = threading.Barrier(2)
        posted = []
        send = self.sender(posted=posted)

        def run(_):
            start.wait(timeout=5)
            return self.deliver(send)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, range(2)))
        self.assertEqual(sorted(results), [False, True])
        send.assert_called_once()
        self.assertEqual(posted, [1, 2])
        self.assertEqual(self.state["attempt_count"], 1)

    def test_render_failure_before_intent_is_safe_to_retry(self):
        with self.assertRaisesRegex(ValueError, "render failed"):
            self.deliver(Mock(side_effect=ValueError("render failed")))
        self.assertEqual(self.state["status"], "failed")
        self.assertEqual(self.state["in_flight"], "")
        self.assertEqual(self.state["sent_pages"], {})
        self.assertTrue(self.deliver(self.sender()))
        self.assertEqual(self.state["attempt_count"], 2)

    def test_rejected_second_page_resumes_only_unconfirmed_page(self):
        posted = []
        with self.assertRaises(DeliveryRejected):
            self.deliver(self.sender(posted=posted, failure=DeliveryRejected("private-token")))
        self.assertEqual(self.state["sent_pages"], {"1": 101})
        self.assertEqual(self.state["status"], "failed")
        self.assertEqual(self.state["in_flight"], "")
        self.assertNotIn("private-token", str(self.state))
        resumed = self.sender(posted=posted)
        self.assertTrue(self.deliver(resumed))
        self.assertEqual(resumed.call_args.args[0], {1})
        self.assertEqual(posted, [1, 2, 2])

    def test_timeout_and_unexpected_post_failure_block_even_changed_fingerprint(self):
        for error in (requests.Timeout("private-token"), DeliveryUncertain("uncertain"), RuntimeError("secret URL")):
            with self.subTest(error=type(error).__name__):
                self.db.documents.clear()
                with self.assertRaises(type(error)):
                    self.deliver(self.sender(failure=error))
                self.assertEqual(self.state["status"], "uncertain")
                self.assertEqual(self.state["in_flight"], "2")
                self.assertEqual(self.state["sent_pages"], {"1": 101})
                self.assertNotIn("private-token", str(self.state))
                self.assertNotIn("secret URL", str(self.state))
                blocked = self.sender()
                with self.assertRaises(DeliveryUncertain):
                    self.deliver(blocked, fingerprint="changed")
                blocked.assert_not_called()

    def test_crash_in_flight_is_blocked_after_lease_expiry(self):
        with self.assertRaises(KeyboardInterrupt):
            self.deliver(self.sender(failure=KeyboardInterrupt()))
        self.assertEqual(self.state["in_flight"], "2")
        blocked = self.sender()
        self.assertFalse(self.deliver(blocked))
        self.now += timedelta(minutes=31)
        with self.assertRaises(DeliveryUncertain):
            self.deliver(blocked)
        blocked.assert_not_called()

    def test_intent_save_failure_never_posts_and_remains_blocked(self):
        for applied in (False, True):
            with self.subTest(write_applied=applied):
                self.db.documents.clear()
                save = self.store.save

                def fail_intent(owner, values, **kwargs):
                    if values.get("status") == "sending":
                        if applied:
                            save(owner, values, **kwargs)
                        raise RuntimeError("intent write failed")
                    return save(owner, values, **kwargs)

                posted = []
                with patch.object(self.store, "save", side_effect=fail_intent):
                    with self.assertRaisesRegex(RuntimeError, "intent write failed"):
                        self.deliver(self.sender(posted=posted))
                self.assertEqual(posted, [])
                self.assertEqual(self.state["status"], "uncertain")
                with self.assertRaises(DeliveryUncertain):
                    self.deliver(self.sender())

    def test_receipt_save_failure_stops_later_pages_and_blocks_retry(self):
        for applied in (False, True):
            with self.subTest(write_applied=applied):
                self.db.documents.clear()
                save = self.store.save

                def fail_receipt(owner, values, **kwargs):
                    if "last_sent_page" in values:
                        if applied:
                            save(owner, values, **kwargs)
                        raise RuntimeError("receipt write failed")
                    return save(owner, values, **kwargs)

                posted = []
                with patch.object(self.store, "save", side_effect=fail_receipt):
                    with self.assertRaisesRegex(RuntimeError, "receipt write failed"):
                        self.deliver(self.sender(posted=posted))
                self.assertEqual(posted, [1])
                self.assertEqual(self.state["status"], "uncertain")
                blocked = self.sender()
                with self.assertRaises(DeliveryUncertain):
                    self.deliver(blocked)
                blocked.assert_not_called()

    def test_legacy_valid_sent_receipts_skip_without_mutating_state(self):
        for namespace in ("daily_top10", "daily_executable", "daily_tracking_performance"):
            with self.subTest(namespace=namespace):
                self.store = self.make_store(namespace)
                self.db.documents[self.store.ref.key] = {
                    "status": "sent", "date": self.date, "fingerprint": "fixed",
                    "message_id": 101, "sent_pages": {"1": 101, "2": 102}, "page_count": 2,
                }
                before = deepcopy(self.state)
                sender = self.sender()
                self.assertFalse(self.deliver(sender))
                sender.assert_not_called()
                self.assertEqual(self.state, before)

    def test_legacy_sent_wrong_date_or_invalid_receipt_blocks(self):
        for namespace in ("daily_top10", "daily_executable", "daily_tracking_performance"):
            for wrong_date in (True, False):
                with self.subTest(namespace=namespace, wrong_date=wrong_date):
                    self.store = self.make_store(namespace)
                    self.db.documents[self.store.ref.key] = {
                        "status": "sent", "date": "2026-10-05" if wrong_date else self.date,
                        "fingerprint": "fixed", "message_id": True,
                        "sent_pages": {"1": 101}, "page_count": 2,
                    }
                    sender = self.sender()
                    with self.assertRaises(DeliveryUncertain):
                        self.deliver(sender)
                    sender.assert_not_called()

    def test_legacy_failed_or_pending_is_not_proof_of_non_delivery(self):
        for status in ("pending", "failed", "sending", "uncertain"):
            with self.subTest(status=status):
                self.db.documents[self.store.ref.key] = {"status": status, "fingerprint": "fixed"}
                sender = self.sender()
                with self.assertRaises(DeliveryUncertain):
                    self.deliver(sender)
                sender.assert_not_called()

    def test_invalid_stored_pages_fail_closed(self):
        for pages in ([], {"0": 101}, {"3": 103}, {1: 101}, {"1": True},
                      {"1": "101"}, {"1": 0}, {"1": 101, "2": 101}):
            with self.subTest(pages=pages):
                self.db.documents[self.store.ref.key] = {
                    "status": "failed", "image_delivery_schema": SCHEMA,
                    "fingerprint": "fixed", "sent_pages": pages, "page_count": 2,
                }
                sender = self.sender()
                with self.assertRaises(DeliveryUncertain):
                    self.deliver(sender)
                sender.assert_not_called()

    def test_invalid_receipt_never_completes_or_allows_retry(self):
        for receipt in (None, True, False, 0, -1, 1.5, "101"):
            with self.subTest(receipt=receipt):
                self.db.documents.clear()

                def send(skip, before, after):
                    before(1, 1)
                    after(1, receipt, 1)

                with self.assertRaises(DeliveryUncertain):
                    self.deliver(send, count=1)
                self.assertEqual(self.state["status"], "uncertain")
                self.assertEqual(self.state["sent_pages"], {})

    def test_duplicate_receipt_for_distinct_pages_is_uncertain(self):
        def send(skip, before, after):
            before(1, 2)
            after(1, 101, 2)
            before(2, 2)
            after(2, 101, 2)

        with self.assertRaises(DeliveryUncertain):
            self.deliver(send)
        self.assertEqual(self.state["sent_pages"], {"1": 101})
        self.assertEqual(self.state["in_flight"], "2")

    def test_page_count_bounds_prevent_sender_and_state_writes(self):
        for count in (0, -1, True, False, 1.0, "2", 1001):
            with self.subTest(count=count):
                sender = self.sender()
                with self.assertRaises(ValueError):
                    self.deliver(sender, count=count)
                sender.assert_not_called()
                self.assertEqual(self.db.documents, {})

    def test_invalid_intent_page_or_total_prevents_post(self):
        for page, total in ((0, 2), (3, 2), (True, 2), (1.0, 2), ("1", 2), (1, 3)):
            with self.subTest(page=page, total=total):
                self.db.documents.clear()
                posted = []

                def send(skip, before, after):
                    before(page, total)
                    posted.append(page)

                with self.assertRaises(ValueError):
                    self.deliver(send)
                self.assertEqual(posted, [])

    def test_callback_total_requires_integer_not_bool_or_float(self):
        for total in (True, 1.0, "1"):
            for invalid_before in (True, False):
                with self.subTest(total=total, before=invalid_before):
                    self.db.documents.clear()
                    posted = []

                    def send(skip, before, after):
                        before(1, total if invalid_before else 1)
                        posted.append(1)
                        after(1, 101, 1 if invalid_before else total)

                    with self.assertRaises(ValueError if invalid_before else DeliveryUncertain):
                        self.deliver(send, count=1)
                    self.assertEqual(posted, [] if invalid_before else [1])

    def test_same_fingerprint_changed_page_count_blocks_without_sender(self):
        self.db.documents[self.store.ref.key] = {
            "status": "failed", "image_delivery_schema": SCHEMA,
            "fingerprint": "fixed", "sent_pages": {"1": 101}, "page_count": 3,
        }
        sender = self.sender()
        with self.assertRaises(DeliveryUncertain):
            self.deliver(sender)
        sender.assert_not_called()

    def test_maximum_page_count_is_accepted_by_acquisition(self):
        state = self.store.acquire("fixed", "owner", page_count=1000)
        self.assertEqual(state["page_count"], 1000)

    def test_opaque_return_or_receipt_without_intent_is_not_success(self):
        def no_intent(skip, before, after):
            after(1, 101, 1)

        for sender in (Mock(return_value=101), no_intent):
            with self.subTest(sender=sender):
                self.db.documents.clear()
                with self.assertRaises(DeliveryUncertain):
                    self.deliver(sender, count=1)
                self.assertEqual(self.state["status"], "uncertain")

    def test_acquire_failure_never_calls_sender(self):
        sender = self.sender()
        with patch.object(self.store, "acquire", side_effect=RuntimeError("database unavailable")):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                self.deliver(sender)
        sender.assert_not_called()
        self.assertEqual(self.db.documents, {})

    def test_active_lease_cannot_be_bypassed_by_resend_and_expired_owner_cannot_save(self):
        self.store.acquire("fixed", "first", page_count=2)
        sender = self.sender()
        self.assertFalse(self.deliver(sender, resend=True))
        sender.assert_not_called()
        self.now += timedelta(minutes=31)
        self.store.acquire("fixed", "second", page_count=2)
        snapshot = deepcopy(self.state)
        with self.assertRaises(RuntimeError):
            self.store.save("first", {"status": "sent"})
        self.assertEqual(self.state, snapshot)

    def test_explicit_resend_restarts_all_pages_after_uncertainty(self):
        with self.assertRaises(DeliveryUncertain):
            self.deliver(self.sender(failure=DeliveryUncertain("uncertain")))
        posted = []
        self.assertTrue(self.deliver(self.sender(posted=posted), resend=True))
        self.assertEqual(posted, [1, 2])
        self.assertEqual(self.state["status"], "sent")

    def test_missing_database_and_unknown_namespace_are_rejected(self):
        with self.assertRaises(RuntimeError):
            FirestoreImageStore(None, "daily_top10", self.date)
        with self.assertRaises(ValueError):
            self.make_store("other")


if __name__ == "__main__":
    unittest.main()
