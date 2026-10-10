"""Offline delivery tests: never contact Firebase or a real Telegram bot."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from functools import wraps
import threading
import unittest
from unittest.mock import Mock, patch

import requests

from research_delivery import (
    DeliveryRejected,
    DeliveryUncertain,
    FirestoreReportStore,
    deliver_report,
    report_fingerprint,
    telegram_send_text,
)


class _Snapshot:
    def __init__(self, value):
        self.exists = value is not None
        self.value = deepcopy(value)

    def to_dict(self):
        return deepcopy(self.value)


class _Document:
    def __init__(self, db, key):
        self.db = db
        self.key = key

    def get(self, transaction=None):
        return _Snapshot(self.db.documents.get(self.key))


class _Collection:
    def __init__(self, db, name):
        self.db = db
        self.name = name

    def document(self, name):
        return _Document(self.db, (self.name, name))


class _Transaction:
    def __init__(self, db):
        self.db = db

    def set(self, ref, values, merge=False):
        saved = deepcopy(self.db.documents.get(ref.key, {})) if merge else {}
        saved.update(deepcopy(values))
        self.db.documents[ref.key] = saved


class _Database:
    def __init__(self):
        self.documents = {}
        self.lock = threading.RLock()

    def collection(self, name):
        return _Collection(self, name)

    def transaction(self):
        return _Transaction(self)


def _transactional(function):
    @wraps(function)
    def wrapped(transaction):
        with transaction.db.lock:
            return function(transaction)
    return wrapped


class ResearchDeliveryTests(unittest.TestCase):
    def test_lease_duration_defaults_remain_thirty_minutes_and_validate_overrides(self):
        store = FirestoreReportStore(_Database(), "2026-10-07")
        self.assertEqual(store.lease_minutes, 30)
        for value in (0, 31, True, "12"):
            with self.assertRaises(ValueError):
                FirestoreReportStore(_Database(), "2026-10-07", lease_minutes=value)

    def test_sent_marker_alone_never_proves_complete_same_day_receipts(self):
        fingerprint = report_fingerprint(self.records, self.date)
        valid = {"fingerprint": fingerprint, "date": self.date, "status": "sent",
                 "parts": ["first", "second"], "part_count": 2, "sent_parts": {"1": 101, "2": 102}}
        for change in ({"date": "1999-01-01"}, {"sent_parts": {}}, {"sent_parts": {"1": 101}},
                       {"sent_parts": {"1": 101, "2": 101}}, {"part_count": True},
                       {"parts": ["", "second"]}, {"sent_parts": {"1": 101, "2": 0}}):
            self.db.documents[("notifications", f"daily_research_{self.date}")] = {**valid, **change}
            before = deepcopy(self.state)
            with self.subTest(change=change), self.assertRaises(DeliveryUncertain):
                self.store.acquire(fingerprint, "backup")
            self.assertEqual(self.state, before)

    def setUp(self):
        transaction_patch = patch("firebase_admin.firestore.transactional", _transactional)
        transaction_patch.start()
        self.addCleanup(transaction_patch.stop)
        # A failed injection must never result in an actual network request.
        network_patch = patch(
            "research_delivery.requests.post",
            side_effect=AssertionError("real Telegram calls are forbidden in tests"),
        )
        network_patch.start()
        self.addCleanup(network_patch.stop)
        self.db = _Database()
        self.date = "2026-09-24"
        self.records = [{"代號": "2330", "Score": 80}]
        self.store = FirestoreReportStore(self.db, self.date)

    @property
    def state(self):
        return self.db.documents[("notifications", f"daily_research_{self.date}")]

    def deliver(self, builder, sender, **kwargs):
        return deliver_report(
            self.store, self.records, self.date,
            build_messages=builder, send_text=sender, **kwargs,
        )

    def test_success_is_deduplicated_without_rebuilding_or_resending(self):
        builder = Mock(return_value=(["第一段", "第二段"], {"date": self.date}))
        sender = Mock(side_effect=[101, 102])
        self.assertTrue(self.deliver(builder, sender))
        self.assertFalse(self.deliver(builder, sender))
        builder.assert_called_once_with()
        self.assertEqual([call.args[0] for call in sender.call_args_list], ["第一段", "第二段"])
        self.assertEqual(self.state["status"], "sent")
        self.assertEqual(self.state["sent_parts"], {"1": 101, "2": 102})
        self.assertEqual(self.state["part_count"], 2)
        self.assertEqual(self.state["owner"], "")
        self.assertIsNone(self.state["lease_until"])
        self.assertEqual(list(self.db.documents), [("notifications", f"daily_research_{self.date}")])

    def test_explicit_rejection_resumes_frozen_parts_without_recalculation(self):
        builder = Mock(return_value=(["舊第一段", "舊第二段", "舊第三段"], {"snapshot": 1}))
        sender = Mock(side_effect=[101, DeliveryRejected("rejected")])
        with self.assertRaises(DeliveryRejected):
            self.deliver(builder, sender)
        self.assertEqual(self.state["status"], "failed")
        self.assertEqual(self.state["in_flight"], "")
        self.assertEqual(self.state["sent_parts"], {"1": 101})
        self.assertEqual(self.state["parts"], ["舊第一段", "舊第二段", "舊第三段"])

        recalculation = Mock(side_effect=AssertionError("frozen reports must not be rebuilt"))
        resumed_sender = Mock(side_effect=[102, 103])
        self.assertTrue(self.deliver(recalculation, resumed_sender))
        recalculation.assert_not_called()
        self.assertEqual([call.args[0] for call in resumed_sender.call_args_list], ["舊第二段", "舊第三段"])
        self.assertEqual(self.state["metadata"], {"snapshot": 1})
        self.assertEqual(self.state["sent_parts"], {"1": 101, "2": 102, "3": 103})
        self.assertEqual(self.state["attempt_count"], 2)

    def test_builder_failure_is_safe_to_retry_before_any_post(self):
        sender = Mock(return_value=101)
        with self.assertRaises(ValueError):
            self.deliver(Mock(side_effect=ValueError("bad input")), sender)
        sender.assert_not_called()
        self.assertEqual(self.state["status"], "failed")
        self.assertEqual(self.state["in_flight"], "")
        self.assertTrue(self.deliver(Mock(return_value=(["修復後內容"], {})), sender))
        sender.assert_called_once_with("修復後內容")

    def test_timeout_or_unexpected_post_failure_prevents_automatic_retry(self):
        for exception in (requests.Timeout("private payload"), DeliveryUncertain("uncertain"), RuntimeError("post")):
            with self.subTest(exception=type(exception).__name__):
                self.db.documents.clear()
                builder = Mock(return_value=(["第一段", "第二段"], {}))
                sender = Mock(side_effect=[101, exception])
                with self.assertRaises(type(exception)):
                    self.deliver(builder, sender)
                self.assertEqual(self.state["status"], "uncertain")
                self.assertEqual(self.state["in_flight"], "2")
                self.assertEqual(self.state["sent_parts"], {"1": 101})
                self.assertNotIn("private payload", str(self.state))
                blocked_sender = Mock(return_value=999)
                blocked_builder = Mock(return_value=(["不可重算"], {}))
                with self.assertRaises(DeliveryUncertain):
                    self.deliver(blocked_builder, blocked_sender)
                blocked_sender.assert_not_called()
                blocked_builder.assert_not_called()

    def test_unknown_delivery_blocks_even_if_new_records_have_changed(self):
        with self.assertRaises(DeliveryUncertain):
            self.deliver(Mock(return_value=(["報告"], {})), Mock(side_effect=DeliveryUncertain("unknown")))
        self.records[0]["Score"] = 81
        sender = Mock(return_value=100)
        with self.assertRaises(DeliveryUncertain):
            self.deliver(Mock(return_value=(["新報告"], {})), sender)
        sender.assert_not_called()

    def test_in_flight_crash_blocks_after_lease_expires(self):
        with self.assertRaises(KeyboardInterrupt):
            self.deliver(Mock(return_value=(["已開始傳送"], {})), Mock(side_effect=KeyboardInterrupt))
        self.assertEqual(self.state["status"], "sending")
        self.assertEqual(self.state["in_flight"], "1")
        blocked_sender = Mock(return_value=123)
        builder = Mock(return_value=(["不得重發"], {}))
        self.assertFalse(self.deliver(builder, blocked_sender))  # Active owner's lease.
        self.state["lease_until"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        with self.assertRaises(DeliveryUncertain):
            self.deliver(builder, blocked_sender)
        blocked_sender.assert_not_called()
        builder.assert_not_called()

    def test_invalid_receipt_ids_are_uncertain_not_success(self):
        for value in (None, 0, -1, True, False, "123", 1.5):
            with self.subTest(value=value):
                self.db.documents.clear()
                with self.assertRaises(DeliveryUncertain):
                    self.deliver(Mock(return_value=(["報告"], {})), Mock(return_value=value))
                self.assertEqual(self.state["status"], "uncertain")
                self.assertEqual(self.state["sent_parts"], {})

    def test_utf16_limit_counts_astral_characters_as_two_units(self):
        accepted = "😀" * 1750
        sender = Mock(return_value=101)
        self.assertTrue(self.deliver(Mock(return_value=([accepted], {})), sender))
        sender.assert_called_once_with(accepted)
        self.db.documents.clear()
        sender.reset_mock()
        with self.assertRaises(ValueError):
            self.deliver(Mock(return_value=([accepted + "😀"], {})), sender)
        sender.assert_not_called()
        self.assertEqual(self.state["status"], "failed")

    def test_invalid_or_excessive_parts_are_never_sent(self):
        for parts in ([], [""], [" "], [None], ("tuple",), ["a"] * 26, ["字" * 3501]):
            with self.subTest(parts_type=type(parts).__name__, size=len(parts)):
                self.db.documents.clear()
                sender = Mock(return_value=101)
                with self.assertRaises(ValueError):
                    self.deliver(Mock(return_value=(parts, {})), sender)
                sender.assert_not_called()

    def test_transaction_lease_allows_only_one_concurrent_owner(self):
        barrier = threading.Barrier(2)
        fingerprint = report_fingerprint(self.records, self.date)

        def acquire(owner):
            barrier.wait(timeout=5)
            return self.store.acquire(fingerprint, owner)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(acquire, ["first", "second"]))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertIn(self.state["owner"], {"first", "second"})
        self.assertEqual(self.state["attempt_count"], 1)
        self.assertFalse(self.deliver(Mock(side_effect=AssertionError), Mock(side_effect=AssertionError)))

    def test_expired_lease_can_be_claimed_but_old_owner_cannot_save(self):
        fingerprint = report_fingerprint(self.records, self.date)
        self.store.acquire(fingerprint, "old")
        self.store.save("old", {"parts": ["凍結內容"], "sent_parts": {"1": 101}})
        self.state["lease_until"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        acquired = self.store.acquire(fingerprint, "new")
        self.assertEqual(acquired["parts"], ["凍結內容"])
        self.assertEqual(acquired["sent_parts"], {"1": 101})
        snapshot = deepcopy(self.state)
        with self.assertRaises(RuntimeError):
            self.store.save("old", {"status": "sent"}, release=True)
        self.assertEqual(self.state, snapshot)
        self.store.save("new", {"status": "ready"}, release=True)
        self.assertEqual(self.state["owner"], "")

    def test_resend_does_not_bypass_an_active_owner(self):
        self.store.acquire(report_fingerprint(self.records, self.date), "other")
        builder = Mock(return_value=(["不得並行"], {}))
        sender = Mock(return_value=101)
        self.assertFalse(self.deliver(builder, sender, resend=True))
        builder.assert_not_called()
        sender.assert_not_called()

    def test_explicit_resend_rebuilds_and_repeats_the_complete_report(self):
        self.assertTrue(self.deliver(Mock(return_value=(["舊一", "舊二"], {})), Mock(side_effect=[101, 102])))
        builder = Mock(return_value=(["新一", "新二"], {"revision": 2}))
        sender = Mock(side_effect=[201, 202])
        self.assertTrue(self.deliver(builder, sender, resend=True))
        builder.assert_called_once_with()
        self.assertEqual([call.args[0] for call in sender.call_args_list], ["新一", "新二"])
        self.assertEqual(self.state["sent_parts"], {"1": 201, "2": 202})
        self.assertEqual(self.state["metadata"], {"revision": 2})

    def test_explicit_resend_can_override_an_uncertain_released_attempt(self):
        with self.assertRaises(DeliveryUncertain):
            self.deliver(Mock(return_value=(["狀態不明"], {})), Mock(side_effect=DeliveryUncertain("unknown")))
        self.assertTrue(self.deliver(Mock(return_value=(["人工確認後重發"], {})), Mock(return_value=201), resend=True))
        self.assertEqual(self.state["status"], "sent")
        self.assertEqual(self.state["in_flight"], "")

    def test_fingerprint_is_stable_for_key_order_but_changes_with_date_or_content(self):
        baseline = report_fingerprint([{"a": 1, "b": 2}], self.date)
        self.assertEqual(baseline, report_fingerprint([{"b": 2, "a": 1}], self.date))
        self.assertNotEqual(baseline, report_fingerprint([{"a": 1, "b": 3}], self.date))
        self.assertNotEqual(baseline, report_fingerprint([{"a": 1, "b": 2}], "2026-09-29"))

    def test_three_facet_revision_replaces_sent_decision_text_once(self):
        with patch("research_delivery.FORMAT_VERSION", "daily_research_compact_decision_v2"):
            self.assertTrue(self.deliver(Mock(return_value=(["舊買賣判斷"], {})), Mock(return_value=101)))
        sender = Mock(return_value=201)
        builder = Mock(return_value=(["技術面／籌碼面／基本面優缺點"], {}))
        self.assertTrue(self.deliver(builder, sender))
        self.assertEqual(self.state["format"], "daily_research_three_facets_v3")
        self.assertEqual(self.state["sent_parts"], {"1": 201})
        self.assertFalse(self.deliver(builder, sender))
        sender.assert_called_once()


class TelegramTextTransportTests(unittest.TestCase):
    def response(self, payload, *, ok=True):
        return Mock(ok=ok, json=Mock(return_value=payload))

    def test_success_posts_plain_text_once_without_parse_mode(self):
        with patch("research_delivery.requests.post", return_value=self.response({
            "ok": True, "result": {"message_id": 123},
        })) as post:
            self.assertEqual(telegram_send_text("test-token", "test-chat", "<script>literal</script>"), 123)
        post.assert_called_once()
        options = post.call_args.kwargs
        self.assertEqual(options["json"], {
            "chat_id": "test-chat", "text": "<script>literal</script>", "disable_web_page_preview": True,
        })
        self.assertEqual(options["timeout"], (10, 35))

    def test_missing_credentials_do_not_make_a_request(self):
        for token, chat in (("", "chat"), ("token", ""), (None, None)):
            with self.subTest(token=token, chat=chat), patch("research_delivery.requests.post") as post:
                with self.assertRaises(DeliveryRejected):
                    telegram_send_text(token, chat, "message")
                post.assert_not_called()

    def test_explicit_rejection_is_retryable_and_does_not_leak_response(self):
        with patch("research_delivery.requests.post", return_value=self.response({
            "ok": False, "description": "secret-token private report",
        }, ok=False)):
            with self.assertRaises(DeliveryRejected) as raised:
                telegram_send_text("secret-token", "secret-chat", "private report")
        self.assertNotIn("secret", str(raised.exception))
        self.assertNotIn("private report", str(raised.exception))

    def test_timeout_or_unparseable_response_is_uncertain_and_not_retried(self):
        for exception in (requests.Timeout("secret-token"), ValueError("private response")):
            with self.subTest(exception=type(exception).__name__):
                response = self.response({})
                response.json.side_effect = exception
                with patch("research_delivery.requests.post", return_value=response) as post:
                    with self.assertRaises(DeliveryUncertain) as raised:
                        telegram_send_text("secret-token", "chat", "private report")
                post.assert_called_once()
                self.assertNotIn("secret-token", str(raised.exception))
                self.assertNotIn("private response", str(raised.exception))
        with patch("research_delivery.requests.post", side_effect=requests.Timeout("secret-token")) as post:
            with self.assertRaises(DeliveryUncertain):
                telegram_send_text("secret-token", "chat", "message")
        post.assert_called_once()

    def test_telegram_requires_explicit_success_and_a_valid_integer_receipt(self):
        for payload in (
            None, [], "ok", {}, {"ok": True},
            {"result": {"message_id": 123}},
            {"ok": None, "result": {"message_id": 123}},
            {"ok": "true", "result": {"message_id": 123}},
            *({"ok": True, "result": {"message_id": value}} for value in (None, 0, -1, True, "123", 1.5)),
        ):
            with self.subTest(payload=payload), patch(
                "research_delivery.requests.post", return_value=self.response(payload),
            ):
                with self.assertRaises(DeliveryUncertain):
                    telegram_send_text("token", "chat", "message")
        with patch("research_delivery.requests.post", return_value=self.response({
            "ok": True, "result": {"message_id": 123},
        }, ok=False)):
            with self.assertRaises(DeliveryUncertain):
                telegram_send_text("token", "chat", "message")


if __name__ == "__main__":
    unittest.main()
