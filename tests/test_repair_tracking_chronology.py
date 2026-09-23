import unittest
from copy import deepcopy

from repair_tracking_chronology import replay_bundle, restore_reverse_gaps
from top10_tracker import update_positions_with_snapshots


PRIOR_DATE = "2026-09-22"
DATE = "2026-09-23"


def benchmark(date=DATE, predecessor=PRIOR_DATE):
    return {
        "date": date, "previous_trading_date": predecessor,
        "close": 48157.29, "previous_close": 47800.17,
        "daily_return_pct": 0.75, "opening_gap_pct": 0.2,
        "regime": "多頭",
    }


def bundle_fixture(count=1, other_count=0, schema=2):
    """Create authentic prior HOLD snapshots through the production tracker."""
    seeds, quotes = [], {}
    for index in range(count):
        ticker = str(2300 + index)
        execution_schema = schema if count == 1 else (1 if index < 16 else 2)
        position = {
            "position_id": f"{ticker}:2026-08-27", "ticker": ticker,
            "name": f"Fixture {ticker}", "signal_date": "2026-08-27",
            "entry_date": "2026-08-27", "entry_price": 100,
            "execution_schema": execution_schema, "status": "OPEN",
            "highest_price": 103, "lowest_price": 99,
            "current_price": 102, "pnl_pct": 2,
            "last_tracked_date": "2026-09-21",
            "entry_win_rate": 47.4, "entry_backtest_samples": 38,
            "entry_backtest_scope": "frozen entry evidence",
            "entry_backtest_status": "ok", "shares": 100,
            "signal_snapshot": {"WinRate": 47.4, "Backtest_Samples": 38},
        }
        if execution_schema >= 2:
            position.update(stop_price=95, target_price=115, holding_session_count=2)
        seeds.append(position)
        quotes[ticker] = {"Open": 102, "High": 104, "Low": 101, "Close": 103}
    prior_positions, prior_records = update_positions_with_snapshots(
        seeds, [], quotes, PRIOR_DATE,
        benchmark=benchmark(PRIOR_DATE, "2026-09-21"),
    )
    positions, failed_records = [], []
    for original, previous in zip(prior_positions, prior_records):
        failed = deepcopy(previous)
        failed.update(
            date=DATE, action="EXECUTION_DATA_GAP", status="EXCLUDED_DATA_GAP",
            data_status="execution_data_gap", open=None, high=None, low=None,
            close=None, ranking_status="ok", is_top10=True, top10_rank=2, score=82,
        )
        position = deepcopy(original)
        position.update(
            status="EXCLUDED_DATA_GAP", resolution_date=DATE,
            resolution_reason="false reversed predecessor",
            execution_data_gap_date="2026-09-22 後至 2026-09-21",
            execution_data_gap_from=PRIOR_DATE, execution_data_gap_through="2026-09-21",
            last_tracked_date=DATE, last_snapshot=failed,
        )
        for key in (
            "pnl_pct", "gross_pnl_amount", "estimated_transaction_cost",
            "net_pnl_amount", "net_pnl_pct",
        ):
            position[key] = failed[key] = None
        positions.append(position)
        failed_records.append(deepcopy(failed))
    others = [{
        "position_id": f"{6000 + index}:2026-08-27", "ticker": str(6000 + index),
        "execution_schema": 1, "entry_date": "2026-08-27", "entry_price": 100,
        "shares": 100, "status": "CLOSED_TP", "close_date": PRIOR_DATE,
        "close_price": 115, "last_snapshot": {"date": PRIOR_DATE, "action": "TAKE_PROFIT"},
        "user_metadata": {"preserve": index},
    } for index in range(other_count)]
    if other_count:
        others[-1].update(
            status="EXCLUDED_DATA_GAP", resolution_date=DATE,
            execution_data_gap_from="2026-09-18", execution_data_gap_through="2026-09-22",
        )
    positions.extend(others)
    prior_benchmark = benchmark(PRIOR_DATE, "2026-09-21")
    prior_benchmark["close"] = 47800.17
    return {
        "date": DATE, "prior_date": PRIOR_DATE, "benchmark": benchmark(),
        "backup": {
            "positions": positions,
            "tracker": {"data": {"latest_date": DATE, "record_count": len(positions),
                                  "history_dates": [DATE, PRIOR_DATE], "custom_metadata": "keep"}},
            "prior_history": {"data": {"date": PRIOR_DATE, "benchmark": prior_benchmark,
                                         "records": prior_records}},
            "history": {"data": {"date": DATE, "records": failed_records,
                                   "ranking_status": "ok", "custom_metadata": "keep"}},
        },
        "quotes": {
            p["ticker"]: {"date": DATE, "previous_date": PRIOR_DATE, "previous_close": 103,
                          "Open": 103, "High": 106, "Low": 102, "Close": 105}
            for p in prior_positions
        },
    }


def restore(bundle):
    backup = bundle["backup"]
    return restore_reverse_gaps(
        backup["positions"], backup["prior_history"]["data"]["records"],
        backup["history"]["data"]["records"], bundle["prior_date"], bundle["date"],
    )


class RepairTrackingChronologyTests(unittest.TestCase):
    def test_restoration_requires_exact_reversed_gap_and_is_non_mutating(self):
        bundle = bundle_fixture(other_count=2)
        original = deepcopy(bundle)
        restored = restore(bundle)
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]["status"], "OPEN")
        self.assertEqual(restored[0]["last_tracked_date"], PRIOR_DATE)
        self.assertEqual(restored[0]["pnl_pct"], 3)
        self.assertNotIn("resolution_date", restored[0])
        self.assertNotIn("execution_data_gap_from", restored[0])
        self.assertEqual(bundle, original)

    def test_genuine_same_day_or_forward_gap_and_closed_positions_not_restored(self):
        changes = (
            {"execution_data_gap_from": "2026-09-18", "execution_data_gap_through": PRIOR_DATE},
            {"execution_data_gap_through": PRIOR_DATE},
            {"execution_data_gap_through": DATE},
            {"status": "CLOSED_TP"},
            {"resolution_date": "2026-09-21"},
        )
        for changed in changes:
            with self.subTest(changed=changed):
                bundle = bundle_fixture()
                bundle["backup"]["positions"][0].update(changed)
                with self.assertRaisesRegex(ValueError, "no proven"):
                    restore(bundle)

    def test_invalid_reverse_gap_date_is_not_accepted_as_evidence(self):
        bundle = bundle_fixture()
        bundle["backup"]["positions"][0]["execution_data_gap_through"] = "0000"
        with self.assertRaises(ValueError):
            restore(bundle)

    def test_complete_prior_hold_ohlc_is_required(self):
        changes = (
            {"action": "DATA_MISSING"}, {"data_status": "missing"}, {"status": "CLOSED_TP"},
            {"date": "2026-09-21"}, {"low": None}, {"high": 102}, {"close": float("nan")},
        )
        for changed in changes:
            with self.subTest(changed=changed):
                bundle = bundle_fixture()
                bundle["backup"]["prior_history"]["data"]["records"][0].update(changed)
                with self.assertRaisesRegex(ValueError, "prior evidence"):
                    restore(bundle)

    def test_changed_frozen_entry_evidence_rejected(self):
        for key, value in (
            ("entry_win_rate", 99), ("entry_backtest_samples", 99), ("entry_price", 101),
            ("shares", 200), ("holding_session_count", 4), ("stop_price", 94),
            ("target_price", 116), ("highest_price", 105), ("current_price", 104),
            ("signal_snapshot", {"WinRate": 99}),
        ):
            with self.subTest(key=key):
                bundle = bundle_fixture()
                bundle["backup"]["positions"][0][key] = value
                with self.assertRaises(ValueError):
                    restore(bundle)

    def test_nan_frozen_plan_is_rejected(self):
        bundle = bundle_fixture()
        bundle["backup"]["positions"][0]["stop_price"] = float("nan")
        with self.assertRaises(ValueError):
            restore(bundle)

    def test_duplicate_prior_or_current_snapshots_rejected(self):
        for history in ("history", "prior_history"):
            with self.subTest(history=history):
                bundle = bundle_fixture()
                records = bundle["backup"][history]["data"]["records"]
                records.append(deepcopy(records[0]))
                with self.assertRaisesRegex(ValueError, "duplicate"):
                    restore(bundle)

    def test_legacy_plan_without_persisted_levels_replays_without_changing_basis(self):
        bundle = bundle_fixture(schema=1)
        self.assertNotIn("stop_price", bundle["backup"]["positions"][0])
        self.assertNotIn("target_price", bundle["backup"]["positions"][0])
        positions, _, history = replay_bundle(bundle)
        self.assertEqual(positions[0]["execution_schema"], 1)
        self.assertNotIn("stop_price", positions[0])
        self.assertNotIn("target_price", positions[0])
        self.assertEqual(history["records"][0]["stop_price"], 90)
        self.assertEqual(history["records"][0]["target_price"], 115)

    def test_legacy_missing_default_fields_remain_missing_in_position(self):
        bundle = bundle_fixture(schema=1)
        position = bundle["backup"]["positions"][0]
        prior = bundle["backup"]["prior_history"]["data"]["records"][0]
        for key in ("signal_date", "execution_schema", "signal_snapshot"):
            position.pop(key)
        prior["signal_snapshot"] = {}
        positions, _, history = replay_bundle(bundle)
        for key in ("signal_date", "execution_schema", "signal_snapshot", "stop_price", "target_price"):
            self.assertNotIn(key, positions[0])
        self.assertEqual(history["records"][0]["execution_schema"], 1)
        self.assertEqual(history["records"][0]["signal_date"], "2026-08-27")
        self.assertEqual(history["records"][0]["signal_snapshot"], {})

    def test_replay_restores_20_and_preserves_37_other_positions_and_entry_stats(self):
        bundle = bundle_fixture(count=20, other_count=37)
        original = deepcopy(bundle)
        positions, tracker, history = replay_bundle(bundle)
        self.assertEqual(len(positions), 57)
        self.assertEqual(positions[20:], original["backup"]["positions"][20:])
        self.assertEqual(history["summary"]["actions"], {"HOLD": 20})
        self.assertEqual(history["summary"]["open_count"], 20)
        self.assertEqual(tracker["latest_snapshots"], history["records"])
        self.assertEqual(tracker["custom_metadata"], "keep")
        self.assertEqual(history["custom_metadata"], "keep")
        self.assertEqual(len(tracker["chronology_repair"]["affected_position_ids"]), 20)
        for position, record in zip(positions[:20], history["records"]):
            self.assertEqual(position["current_price"], 105)
            self.assertEqual(position["last_tracked_date"], DATE)
            self.assertEqual(position["entry_win_rate"], 47.4)
            self.assertEqual(position["entry_backtest_samples"], 38)
            self.assertEqual(position["signal_snapshot"], {"WinRate": 47.4, "Backtest_Samples": 38})
            self.assertEqual(record["data_status"], "ok")
            self.assertEqual(record["daily_return_pct"], 1.94)
            self.assertEqual(record["benchmark_return_pct"], 0.75)
            self.assertEqual(record["excess_return_pct"], 1.19)
            self.assertTrue(record["is_top10"])
            self.assertEqual(record["score"], 82)
            self.assertEqual(record["top10_rank"], 2)
        self.assertEqual(bundle, original)

    def test_replay_uses_actual_bar_for_stop_before_target(self):
        bundle = bundle_fixture()
        bundle["quotes"]["2300"].update(Open=103, High=117, Low=94, Close=105)
        positions, _, history = replay_bundle(bundle)
        self.assertEqual(positions[0]["status"], "CLOSED_SL")
        self.assertEqual(history["records"][0]["action"], "STOP_LOSS")
        self.assertLess(positions[0]["close_price"], 95)
        self.assertEqual(positions[0]["entry_backtest_samples"], 38)

    def test_missing_or_mismatched_quote_is_rejected(self):
        changes = (
            None, {"date": PRIOR_DATE}, {"previous_date": "2026-09-21"},
            {"previous_close": 102}, {"previous_close": float("nan")},
            {"Close": None}, {"Low": 107},
        )
        for changed in changes:
            with self.subTest(changed=changed):
                bundle = bundle_fixture()
                if changed is None:
                    bundle["quotes"].clear()
                else:
                    bundle["quotes"]["2300"].update(changed)
                with self.assertRaisesRegex(ValueError, "price evidence"):
                    replay_bundle(bundle)

    def test_benchmark_must_match_prior_saved_close_and_sessions(self):
        for changed in (
            {"previous_close": 47718.84}, {"previous_trading_date": "2026-09-21"},
            {"date": PRIOR_DATE}, {"previous_close": float("nan")},
        ):
            with self.subTest(changed=changed):
                bundle = bundle_fixture()
                bundle["benchmark"].update(changed)
                with self.assertRaises(ValueError):
                    replay_bundle(bundle)


if __name__ == "__main__":
    unittest.main()
