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
        }]

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


if __name__ == "__main__":
    unittest.main()
