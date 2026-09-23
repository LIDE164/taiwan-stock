import unittest
from copy import deepcopy

from top10_tracker import update_positions_with_snapshots


class TrackingBenchmarkChronologyTests(unittest.TestCase):
    def setUp(self):
        self.position = {
            "position_id": "2330:2026-09-17",
            "ticker": "2330",
            "name": "台積電",
            "signal_date": "2026-09-17",
            "entry_date": "2026-09-18",
            "entry_price": 100,
            "status": "OPEN",
            "highest_price": 104,
            "lowest_price": 99,
            "current_price": 103,
            "pnl_pct": 3,
            "last_tracked_date": "2026-09-22",
            "last_snapshot": {
                "date": "2026-09-22",
                "action": "HOLD",
                "data_status": "ok",
                "status": "OPEN",
                "mark_price": 103,
            },
        }
        self.quotes = {"2330": {"Open": 103, "High": 105, "Low": 102, "Close": 104}}
        self.benchmark = {
            "date": "2026-09-23",
            "previous_trading_date": "2026-09-22",
            "close": 30000,
            "daily_return_pct": 0.5,
        }

    def test_earlier_benchmark_predecessor_aborts_without_changing_positions(self):
        positions = [deepcopy(self.position)]
        original = deepcopy(positions)
        benchmark = dict(self.benchmark, previous_trading_date="2026-09-21")

        with self.assertRaisesRegex(ValueError, "大盤交易日序列矛盾.*2026-09-22"):
            update_positions_with_snapshots(
                positions, [], self.quotes, "2026-09-23", benchmark=benchmark
            )

        self.assertEqual(positions, original)
        self.assertEqual(positions[0]["status"], "OPEN")
        self.assertNotIn("execution_data_gap_date", positions[0])

    def test_corrected_benchmark_can_resume_authentic_daily_tracking(self):
        positions, snapshots = update_positions_with_snapshots(
            [self.position], [], self.quotes, "2026-09-23", benchmark=self.benchmark
        )

        self.assertEqual(positions[0]["status"], "OPEN")
        self.assertEqual(positions[0]["last_tracked_date"], "2026-09-23")
        self.assertEqual(snapshots[0]["action"], "HOLD")
        self.assertEqual(snapshots[0]["data_status"], "ok")
        self.assertAlmostEqual(snapshots[0]["daily_return_pct"], 0.97)
        self.assertEqual(snapshots[0]["benchmark_return_pct"], 0.5)
        self.assertEqual(snapshots[0]["excess_return_pct"], 0.47)

    def test_genuinely_skipped_market_session_still_permanently_excludes(self):
        position = dict(self.position, last_tracked_date="2026-09-21")
        position["last_snapshot"] = dict(self.position["last_snapshot"], date="2026-09-21")

        positions, snapshots = update_positions_with_snapshots(
            [position], [], self.quotes, "2026-09-23", benchmark=self.benchmark
        )

        self.assertEqual(positions[0]["status"], "EXCLUDED_DATA_GAP")
        self.assertEqual(positions[0]["execution_data_gap_through"], "2026-09-22")
        self.assertEqual(snapshots[0]["action"], "EXECUTION_DATA_GAP")
        self.assertIsNone(snapshots[0]["pnl_pct"])

    def test_unrepaired_missing_stock_bar_still_permanently_excludes(self):
        position = deepcopy(self.position)
        position["last_snapshot"].update(action="DATA_MISSING", data_status="missing")

        positions, snapshots = update_positions_with_snapshots(
            [position], [], self.quotes, "2026-09-23", benchmark=self.benchmark
        )

        self.assertEqual(positions[0]["status"], "EXCLUDED_DATA_GAP")
        self.assertEqual(positions[0]["execution_data_gap_date"], "2026-09-22")
        self.assertEqual(snapshots[0]["action"], "EXECUTION_DATA_GAP")

    def test_pending_signal_disproves_earlier_benchmark_predecessor(self):
        position = {
            "ticker": "2454",
            "execution_schema": 2,
            "status": "PENDING",
            "signal_date": "2026-09-22",
            "expected_entry_date": "2026-09-23",
        }

        with self.assertRaisesRegex(ValueError, "大盤交易日序列矛盾.*signal_date"):
            update_positions_with_snapshots(
                [position], [], {}, "2026-09-23",
                benchmark=dict(self.benchmark, previous_trading_date="2026-09-21"),
            )

        self.assertEqual(position["status"], "PENDING")
        self.assertNotIn("expire_date", position)

    def test_same_day_or_future_predecessor_aborts(self):
        for predecessor in ("2026-09-23", "2026-09-24"):
            with self.subTest(predecessor=predecessor):
                with self.assertRaisesRegex(ValueError, "大盤交易日序列矛盾"):
                    update_positions_with_snapshots(
                        [self.position], [], self.quotes, "2026-09-23",
                        benchmark=dict(self.benchmark, previous_trading_date=predecessor),
                    )


if __name__ == "__main__":
    unittest.main()
