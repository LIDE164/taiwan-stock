import unittest
from copy import deepcopy
from functools import wraps
import threading
from unittest.mock import ANY, patch

import pandas as pd

import scanner
from research_delivery import DeliveryRejected, DeliveryUncertain


class _Snapshot:
    def __init__(self, value):
        self._value = value
        self.exists = value is not None

    def to_dict(self):
        return deepcopy(self._value or {})


class _Document:
    def __init__(self):
        self.value = None

    def get(self, transaction=None):
        return _Snapshot(self.value)

    def set(self, value, merge=False):
        value = deepcopy(value)
        if merge and self.value:
            self.value.update(value)
        else:
            self.value = dict(value)


class _Collection:
    def __init__(self):
        self.documents = {}

    def document(self, name):
        return self.documents.setdefault(name, _Document())


class _Database:
    def __init__(self):
        self.collections = {}
        self.lock = threading.RLock()

    def collection(self, name):
        return self.collections.setdefault(name, _Collection())

    def transaction(self):
        return _Transaction(self)


class _Transaction:
    def __init__(self, database):
        self.db = database

    def set(self, ref, values, merge=False):
        ref.set(values, merge=merge)


def _transactional(function):
    @wraps(function)
    def wrapped(transaction):
        with transaction.db.lock:
            return function(transaction)
    return wrapped


def _image_sender(message_id):
    def send(*args, **kwargs):
        kwargs["before_send"]()
        return message_id
    return send


def _performance_sender(message_id):
    def send(*args, **kwargs):
        kwargs["on_page_sending"](1, 1)
        kwargs["on_page_sent"](1, message_id, 1)
        return message_id
    return send


class ScannerTelegramTests(unittest.TestCase):
    def setUp(self):
        transaction_patch = patch("firebase_admin.firestore.transactional", _transactional)
        transaction_patch.start()
        self.addCleanup(transaction_patch.stop)
        self.db = _Database()
        self.rows = [{
            "Rank": 1, "代號": "2330", "名稱": "台積電", "Score": 78,
            "收盤價": 1234, "漲跌幅": 1.2, "WinRate": 55, "Backtest_Samples": 40,
            "Entry_Status": "現在可執行",
        }]

    def test_mini_kbars_use_only_authentic_complete_ohlc(self):
        frame = pd.DataFrame(
            [
                {"Open": 100, "High": 105, "Low": 99, "Close": 103},
                {"Open": 103, "High": 102, "Low": 101, "Close": 104},
                {"Open": 104, "High": 108, "Low": 103, "Close": 107},
            ],
            index=pd.to_datetime(["2026-08-27", "2026-08-28", "2026-08-31"]),
        )
        bars = scanner.build_mini_kbars(frame)
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0]["date"], "2026-08-27")
        self.assertEqual(bars[-1]["close"], 107)

    def test_executable_top10_excludes_waiting_and_uses_actionable_rank(self):
        rows = [
            dict(self.rows[0], Rank=1, 代號="1111", Entry_Status="等待拉回"),
            dict(self.rows[0], Rank=5, 代號="2222"),
            dict(self.rows[0], Rank=9, 代號="3333"),
        ]
        selected = scanner.select_executable_top10(rows)
        self.assertEqual([row["代號"] for row in selected], ["2222", "3333"])
        self.assertEqual([row["Rank"] for row in selected], [1, 2])
        self.assertEqual([row["Overall_Rank"] for row in selected], [5, 9])

    def test_executable_top10_caps_each_known_industry_at_two_names(self):
        rows = [
            dict(self.rows[0], Rank=1, 代號="1111", 產業="半導體"),
            dict(self.rows[0], Rank=2, 代號="2222", 產業="半導體"),
            dict(self.rows[0], Rank=3, 代號="3333", 產業="半導體"),
            dict(self.rows[0], Rank=4, 代號="4444", 產業="電子零組件"),
        ]
        selected = scanner.select_executable_top10(rows)
        self.assertEqual([row["代號"] for row in selected], ["1111", "2222", "4444"])

    def test_same_ranking_is_sent_only_once(self):
        with (
            patch.object(scanner, "db", self.db),
            patch.object(scanner, "_telegram_credentials", return_value=("token", "chat")),
            patch.object(scanner, "send_top10_photo", side_effect=_image_sender(99)) as send,
        ):
            self.assertTrue(scanner.send_daily_top10_notification(self.rows, "2026-08-27"))
            self.assertFalse(scanner.send_daily_top10_notification(self.rows, "2026-08-27"))
        send.assert_called_once()
        saved = self.db.collection("notifications").document("daily_top10_2026-08-27").value
        self.assertEqual(saved["status"], "sent")
        self.assertEqual(saved["message_id"], 99)

    def test_changed_ranking_is_sent_again(self):
        changed = [dict(self.rows[0], Score=79)]
        with (
            patch.object(scanner, "db", self.db),
            patch.object(scanner, "_telegram_credentials", return_value=("token", "chat")),
            patch.object(scanner, "send_top10_photo", side_effect=_image_sender(100)) as send,
        ):
            scanner.send_daily_top10_notification(self.rows, "2026-08-27")
            scanner.send_daily_top10_notification(changed, "2026-08-27")
        self.assertEqual(send.call_count, 2)

    def test_waiting_names_do_not_fill_the_daily_top10(self):
        waiting = [dict(self.rows[0], Entry_Status="等待拉回")]
        with (
            patch.object(scanner, "db", self.db),
            patch.object(scanner, "_telegram_credentials", return_value=("token", "chat")),
            patch.object(scanner, "send_top10_photo", side_effect=_image_sender(103)) as send,
        ):
            self.assertTrue(scanner.send_daily_top10_notification(waiting, "2026-08-27"))
        send.assert_called_once_with([], "2026-08-27", "token", "chat", before_send=ANY)
        saved = self.db.collection("notifications").document("daily_top10_2026-08-27").value
        self.assertEqual(saved["ranking_count"], 0)
        self.assertEqual(saved["ranking_type"], "executable")

    def test_executable_image_has_independent_deduplication(self):
        ready = [dict(
            self.rows[0], Entry_Status="現在可執行",
            Entry_Low=1200, Entry_High=1235, Entry_Stop=1170, Entry_Target=1330, Entry_RRR=1.5,
        )]
        with (
            patch.object(scanner, "db", self.db),
            patch.object(scanner, "_telegram_credentials", return_value=("token", "chat")),
            patch.object(scanner, "send_executable_photo", side_effect=_image_sender(101)) as send,
        ):
            self.assertTrue(scanner.send_daily_executable_notification(ready, "2026-08-27"))
            self.assertFalse(scanner.send_daily_executable_notification(ready, "2026-08-27"))
        send.assert_called_once()
        saved = self.db.collection("notifications").document("daily_executable_2026-08-27").value
        self.assertEqual(saved["executable_count"], 1)

    def test_empty_executable_result_is_still_sent_once(self):
        waiting = [dict(self.rows[0], Entry_Status="等待拉回")]
        with (
            patch.object(scanner, "db", self.db),
            patch.object(scanner, "_telegram_credentials", return_value=("token", "chat")),
            patch.object(scanner, "send_executable_photo", side_effect=_image_sender(102)) as send,
        ):
            self.assertTrue(scanner.send_daily_executable_notification(waiting, "2026-08-27"))
            self.assertFalse(scanner.send_daily_executable_notification(waiting, "2026-08-27"))
        send.assert_called_once()
        saved = self.db.collection("notifications").document("daily_executable_2026-08-27").value
        self.assertEqual(saved["executable_count"], 0)

    def test_detailed_executable_image_uses_the_same_diversified_top10(self):
        rows = [
            dict(self.rows[0], 代號="1111", 產業="半導體"),
            dict(self.rows[0], 代號="2222", 產業="半導體"),
            dict(self.rows[0], 代號="3333", 產業="半導體"),
            dict(self.rows[0], 代號="4444", 產業="電子零組件"),
        ]
        expected = scanner.select_executable_top10(rows)
        with (
            patch.object(scanner, "db", self.db),
            patch.object(scanner, "_telegram_credentials", return_value=("token", "chat")),
            patch.object(scanner, "send_executable_photo", side_effect=_image_sender(105)) as send,
        ):
            scanner.send_daily_executable_notification(rows, "2026-08-27")
        self.assertEqual(send.call_args.kwargs["selected_results"], expected)
        self.assertNotIn("3333", [row["代號"] for row in expected])

    def test_tracking_performance_uses_prior_analysis_and_deduplicates(self):
        history = self.db.collection("top10_tracking_history").document("2026-08-28")
        history.value = {"data": {"records": [{
            "ticker": "2330", "name": "台積電", "entry_date": "2026-08-27",
            "entry_price": 100, "mark_price": 101, "daily_return_pct": 1,
            "pnl_pct": 1, "data_status": "ok", "action": "ENTRY",
        }]}}
        tracker = self.db.collection("market_data").document("top10_tracker")
        tracker.value = {"data": {"positions": [{
            "ticker": "2330", "entry_date": "2026-08-27", "status": "OPEN", "pnl_pct": 1,
        }]}}
        with (
            patch.object(scanner, "db", self.db),
            patch.object(scanner, "_telegram_credentials", return_value=("token", "chat")),
            patch.object(scanner, "send_tracking_performance_photo", side_effect=_performance_sender(104)) as send,
        ):
            self.assertFalse(scanner.send_daily_tracking_performance_notification("2026-08-27"))
            self.assertTrue(scanner.send_daily_tracking_performance_notification("2026-08-28"))
            self.assertFalse(scanner.send_daily_tracking_performance_notification("2026-08-28"))
        send.assert_called_once()
        saved = self.db.collection("notifications").document(
            "daily_tracking_performance_2026-08-28"
        ).value
        self.assertEqual(saved["tracked_count"], 1)
        self.assertEqual(saved["valid_count"], 1)
        self.assertEqual(saved["page_count"], 1)
        self.assertEqual(saved["message_id"], 104)

    def test_daily_notifications_attempt_all_artifacts_after_one_failure(self):
        with (
            patch.object(
                scanner,
                "send_daily_top10_notification",
                side_effect=ValueError("render failed"),
            ) as top10_sender,
            patch.object(scanner, "send_daily_executable_notification") as executable_sender,
            patch.object(
                scanner,
                "send_daily_tracking_performance_notification",
            ) as tracking_sender,
            patch.object(scanner, "send_daily_research_notification") as research_sender,
        ):
            with self.assertRaisesRegex(RuntimeError, "可執行 Top10: ValueError"):
                scanner.send_daily_notifications(
                    self.rows,
                    "2026-08-27",
                    resend=True,
                )

        top10_sender.assert_called_once_with(self.rows, "2026-08-27", resend=True)
        executable_sender.assert_called_once_with(self.rows, "2026-08-27", resend=True)
        tracking_sender.assert_called_once_with("2026-08-27", resend=True)
        research_sender.assert_called_once_with(self.rows, "2026-08-27", resend=True)

    def test_performance_pages_resume_confirmed_receipts_after_explicit_rejection(self):
        notification = self.db.collection("notifications").document("daily_tracking_performance_2026-08-28")
        delivered = []
        attempts = []

        def transport(*args, **kwargs):
            skip = kwargs["skip_page_numbers"]
            attempts.append(set(skip))
            for page in range(1, 3):
                if page in skip:
                    continue
                kwargs["on_page_sending"](page, 2)
                self.assertEqual(notification.value["in_flight"], str(page))
                self.assertEqual(notification.value["status"], "sending")
                if page == 2 and len(attempts) == 1:
                    raise DeliveryRejected("mocked rejection")
                delivered.append(page)
                kwargs["on_page_sent"](page, 100 + page, 2)

        report = {"tracked_count": 12, "valid_count": 12, "missing_count": 0, "page_count": 2}
        with (
            patch.object(scanner, "db", self.db),
            patch.object(scanner, "_load_tracking_performance_data", return_value=([{}], [], {})),
            patch.object(scanner, "build_tracking_performance_report", return_value=report),
            patch.object(scanner, "_telegram_credentials", return_value=("token", "chat")),
            patch.object(scanner, "send_tracking_performance_photo", side_effect=transport) as send,
        ):
            with self.assertRaises(DeliveryRejected):
                scanner.send_daily_tracking_performance_notification("2026-08-28")
            self.assertEqual(notification.value["sent_pages"], {"1": 101})
            self.assertTrue(scanner.send_daily_tracking_performance_notification("2026-08-28"))
            self.assertFalse(scanner.send_daily_tracking_performance_notification("2026-08-28"))
        self.assertEqual(delivered, [1, 2])
        self.assertEqual(attempts, [set(), {1}])
        self.assertEqual(send.call_count, 2)
        self.assertEqual(notification.value["sent_pages"], {"1": 101, "2": 102})
        self.assertEqual(notification.value["tracked_count"], 12)

    def test_uncertain_top10_still_attempts_other_daily_artifacts_without_resending(self):
        fingerprint = scanner._top10_notification_fingerprint(
            scanner.select_executable_top10(self.rows), "2026-08-27",
        )
        self.db.collection("notifications").document("daily_top10_2026-08-27").set({
            "date": "2026-08-27", "fingerprint": fingerprint, "status": "uncertain",
            "in_flight": "1",
        })
        with (
            patch.object(scanner, "db", self.db),
            patch.object(scanner, "send_top10_photo") as transport,
            patch.object(scanner, "send_daily_executable_notification") as executable,
            patch.object(scanner, "send_daily_tracking_performance_notification") as performance,
            patch.object(scanner, "send_daily_research_notification") as research,
        ):
            with self.assertRaisesRegex(RuntimeError, "可執行 Top10: DeliveryUncertain"):
                scanner.send_daily_notifications(self.rows, "2026-08-27")
            with self.assertRaises(DeliveryUncertain):
                scanner.send_daily_top10_notification(self.rows, "2026-08-27")
        transport.assert_not_called()
        executable.assert_called_once_with(self.rows, "2026-08-27", resend=False)
        performance.assert_called_once_with("2026-08-27", resend=False)
        research.assert_called_once_with(self.rows, "2026-08-27", resend=False)

    def test_research_refuses_running_or_mismatched_scan(self):
        lock = self.db.collection("system_locks").document("daily_scan")
        with (
            patch.object(scanner, "db", self.db),
            patch.object(scanner, "_load_daily_scan_doc", return_value={
                "scan_date": "2026-08-27", "data": self.rows,
            }),
            patch("research_delivery.deliver_report") as send,
        ):
            for state in (
                {"status": "running", "trading_date": "2026-08-27"},
                {"status": "completed", "trading_date": "2026-08-26"},
            ):
                lock.set(state)
                with self.assertRaisesRegex(RuntimeError, "已完成且一致"):
                    scanner.send_daily_research_notification(self.rows, "2026-08-27")
            lock.set({"status": "completed", "trading_date": "2026-08-27"})
            with self.assertRaisesRegex(RuntimeError, "已完成且一致"):
                scanner.send_daily_research_notification([], "2026-08-27")
        send.assert_not_called()

    def test_research_sends_only_completed_matching_scan(self):
        self.db.collection("system_locks").document("daily_scan").set({
            "status": "completed", "trading_date": "2026-08-27",
        })
        with (
            patch.object(scanner, "db", self.db),
            patch.object(scanner, "_load_daily_scan_doc", return_value={
                "scan_date": "2026-08-27", "data": self.rows,
            }),
            patch("research_delivery.deliver_report", return_value=True) as send,
        ):
            self.assertTrue(scanner.send_daily_research_notification(self.rows, "2026-08-27"))
        self.assertEqual(send.call_args.args[1:], (self.rows, "2026-08-27"))


if __name__ == "__main__":
    unittest.main()
