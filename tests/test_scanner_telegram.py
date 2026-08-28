import unittest
from unittest.mock import patch

import scanner


class _Snapshot:
    def __init__(self, value):
        self._value = value
        self.exists = value is not None

    def to_dict(self):
        return dict(self._value or {})


class _Document:
    def __init__(self):
        self.value = None

    def get(self):
        return _Snapshot(self.value)

    def set(self, value, merge=False):
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

    def collection(self, name):
        return self.collections.setdefault(name, _Collection())


class ScannerTelegramTests(unittest.TestCase):
    def setUp(self):
        self.db = _Database()
        self.rows = [{
            "Rank": 1, "代號": "2330", "名稱": "台積電", "Score": 78,
            "收盤價": 1234, "漲跌幅": 1.2, "WinRate": 55, "Backtest_Samples": 40,
            "Entry_Status": "現在可執行",
        }]

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

    def test_same_ranking_is_sent_only_once(self):
        with (
            patch.object(scanner, "db", self.db),
            patch.object(scanner, "_telegram_credentials", return_value=("token", "chat")),
            patch.object(scanner, "send_top10_photo", return_value=99) as send,
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
            patch.object(scanner, "send_top10_photo", return_value=100) as send,
        ):
            scanner.send_daily_top10_notification(self.rows, "2026-08-27")
            scanner.send_daily_top10_notification(changed, "2026-08-27")
        self.assertEqual(send.call_count, 2)

    def test_waiting_names_do_not_fill_the_daily_top10(self):
        waiting = [dict(self.rows[0], Entry_Status="等待拉回")]
        with (
            patch.object(scanner, "db", self.db),
            patch.object(scanner, "_telegram_credentials", return_value=("token", "chat")),
            patch.object(scanner, "send_top10_photo", return_value=103) as send,
        ):
            self.assertTrue(scanner.send_daily_top10_notification(waiting, "2026-08-27"))
        send.assert_called_once_with([], "2026-08-27", "token", "chat")
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
            patch.object(scanner, "send_executable_photo", return_value=101) as send,
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
            patch.object(scanner, "send_executable_photo", return_value=102) as send,
        ):
            self.assertTrue(scanner.send_daily_executable_notification(waiting, "2026-08-27"))
            self.assertFalse(scanner.send_daily_executable_notification(waiting, "2026-08-27"))
        send.assert_called_once()
        saved = self.db.collection("notifications").document("daily_executable_2026-08-27").value
        self.assertEqual(saved["executable_count"], 0)

    def test_tracking_performance_starts_on_requested_date_and_deduplicates(self):
        history = self.db.collection("top10_tracking_history").document("2026-08-28")
        history.value = {"data": {"records": [{
            "ticker": "2330", "name": "台積電", "entry_date": "2026-08-28",
            "entry_price": 100, "mark_price": 101, "daily_return_pct": 1,
            "pnl_pct": 1, "data_status": "ok", "action": "ENTRY",
        }]}}
        tracker = self.db.collection("market_data").document("top10_tracker")
        tracker.value = {"data": {"positions": [{
            "ticker": "2330", "entry_date": "2026-08-28", "status": "OPEN", "pnl_pct": 1,
        }]}}
        with (
            patch.object(scanner, "db", self.db),
            patch.object(scanner, "_telegram_credentials", return_value=("token", "chat")),
            patch.object(scanner, "send_tracking_performance_photo", return_value=104) as send,
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


if __name__ == "__main__":
    unittest.main()
