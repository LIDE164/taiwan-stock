import unittest

from top10_tracker import (
    backfill_entry_backtest_snapshots,
    build_top10_history_rows,
    restore_entry_positions_from_history,
    update_positions,
    update_positions_with_snapshots,
)


class Top10TrackerTests(unittest.TestCase):
    def test_restores_missing_start_positions_from_saved_analysis(self):
        history = [{
            "代號": "2330", "名稱": "台積電", "Rank": 1,
            "開盤價": 100, "最高價": 105, "最低價": 99, "收盤價": 103,
            "WinRate": 55, "Backtest_Samples": 40,
        }]
        positions, added = restore_entry_positions_from_history([], history, "2026-08-27")
        self.assertEqual(added, 1)
        self.assertEqual(positions[0]["position_id"], "2330:2026-08-27")
        self.assertEqual(positions[0]["entry_price"], 103)
        self.assertEqual(positions[0]["entry_backtest_samples"], 40)

        rerun, rerun_added = restore_entry_positions_from_history(
            positions, history, "2026-08-27"
        )
        self.assertEqual(rerun_added, 0)
        self.assertEqual(len(rerun), 1)

    def test_both_thresholds_hit_uses_conservative_stop_and_no_same_day_reentry(self):
        existing = [{
            "ticker": "2330", "name": "台積電", "entry_date": "2026-08-14",
            "entry_price": 100, "status": "OPEN", "highest_price": 100,
            "lowest_price": 100, "current_price": 100, "pnl_pct": 0,
        }]
        top10 = [{
            "代號": "2330", "名稱": "台積電", "開盤價": 100,
            "最高價": 116, "最低價": 89, "收盤價": 105,
        }]
        result = update_positions(existing, top10, {}, "2026-08-17")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "CLOSED_SL")
        self.assertEqual(result[0]["close_price"], 90)

    def test_new_signal_waits_for_next_session_before_entry(self):
        top10 = [{
            "代號": "2454", "名稱": "聯發科", "開盤價": 100,
            "最高價": 130, "最低價": 70, "收盤價": 110,
            "WinRate": 54.32, "Backtest_Samples": 37,
            "Backtest_Scope": "純技術面逐步前推",
            "Entry_Low": 105, "Entry_High": 110,
            "Entry_Stop": 100, "Entry_Target": 120,
        }]
        positions, snapshots = update_positions_with_snapshots([], top10, {}, "2026-08-17")
        self.assertEqual(positions[0]["status"], "PENDING")
        self.assertIsNone(positions[0]["entry_price"])
        self.assertIsNone(positions[0]["highest_price"])
        self.assertEqual(positions[0]["signal_date"], "2026-08-17")
        self.assertEqual(positions[0]["entry_win_rate"], 54.32)
        self.assertEqual(positions[0]["entry_backtest_samples"], 37)
        self.assertEqual(positions[0]["entry_backtest_scope"], "純技術面逐步前推")
        self.assertEqual(positions[0]["entry_backtest_status"], "ok")
        self.assertEqual(snapshots[0]["entry_win_rate"], 54.32)
        self.assertEqual(snapshots[0]["entry_backtest_samples"], 37)
        self.assertEqual(snapshots[0]["action"], "SIGNAL")
        self.assertIsNone(snapshots[0]["pnl_pct"])
        self.assertIsNone(snapshots[0]["highest_price"])
        self.assertIsNone(snapshots[0]["lowest_price"])

        quotes = {"2454": {"Open": 108, "High": 115, "Low": 90, "Close": 109}}
        positions, snapshots = update_positions_with_snapshots(
            positions, [], quotes, "2026-08-18"
        )
        self.assertEqual(positions[0]["status"], "OPEN")
        self.assertEqual(positions[0]["entry_date"], "2026-08-18")
        self.assertEqual(positions[0]["entry_price"], 108)
        self.assertEqual(positions[0]["highest_price"], 108)
        self.assertEqual(positions[0]["lowest_price"], 108)
        self.assertEqual(positions[0]["shares"], 625)
        self.assertEqual(positions[0]["planned_risk_amount"], 5000)
        self.assertEqual(snapshots[0]["action"], "ENTRY")
        self.assertEqual(snapshots[0]["stop_price"], 100)
        self.assertEqual(snapshots[0]["target_price"], 120)

    def test_entry_backtest_snapshot_is_not_replaced_by_later_ranking(self):
        existing = [{
            "ticker": "2330", "name": "台積電", "entry_date": "2026-08-14",
            "entry_price": 100, "status": "OPEN", "highest_price": 100,
            "lowest_price": 100, "current_price": 100, "pnl_pct": 0,
            "entry_win_rate": 51.25, "entry_backtest_samples": 40,
            "entry_backtest_scope": "入榜日口徑", "entry_backtest_status": "ok",
        }]
        top10 = [{
            "代號": "2330", "名稱": "台積電", "Rank": 1,
            "開盤價": 101, "最高價": 104, "最低價": 99, "收盤價": 103,
            "WinRate": 88.8, "Backtest_Samples": 99,
        }]
        positions, snapshots = update_positions_with_snapshots(existing, top10, {}, "2026-08-17")
        self.assertEqual(positions[0]["entry_win_rate"], 51.25)
        self.assertEqual(positions[0]["entry_backtest_samples"], 40)
        self.assertEqual(snapshots[0]["entry_win_rate"], 51.25)
        self.assertEqual(snapshots[0]["entry_backtest_samples"], 40)

    def test_missing_entry_backtest_is_explicit_and_not_shown_as_zero_win_rate(self):
        top10 = [{
            "代號": "2454", "名稱": "聯發科", "開盤價": 100,
            "最高價": 111, "最低價": 99, "收盤價": 110,
            "WinRate": 0, "Backtest_Samples": 0,
            "Entry_Low": 105, "Entry_High": 110,
            "Entry_Stop": 100, "Entry_Target": 120,
        }]
        positions, snapshots = update_positions_with_snapshots([], top10, {}, "2026-08-17")
        self.assertIsNone(positions[0]["entry_win_rate"])
        self.assertEqual(positions[0]["entry_backtest_samples"], 0)
        self.assertEqual(positions[0]["entry_backtest_status"], "no_samples")
        self.assertIsNone(snapshots[0]["entry_win_rate"])

    def test_gap_through_stop_exits_at_open(self):
        existing = [{
            "ticker": "2317", "name": "鴻海", "entry_date": "2026-08-14",
            "entry_price": 100, "status": "OPEN", "highest_price": 100,
            "lowest_price": 100, "current_price": 100,
        }]
        quotes = {"2317": {"Open": 85, "High": 88, "Low": 82, "Close": 86}}
        result = update_positions(existing, [], quotes, "2026-08-17")
        self.assertEqual(result[0]["close_price"], 85)
        self.assertEqual(result[0]["status"], "CLOSED_SL")

    def test_daily_snapshot_contains_complete_ohlc_and_returns(self):
        existing = [{
            "ticker": "2330", "name": "台積電", "entry_date": "2026-08-14",
            "entry_price": 100, "status": "OPEN", "highest_price": 102,
            "lowest_price": 98, "current_price": 100, "pnl_pct": 0,
        }]
        top10 = [{
            "代號": "2330", "名稱": "台積電", "Rank": 2, "Score": 66,
            "開盤價": 101, "最高價": 106, "最低價": 99, "收盤價": 105,
        }]
        positions, snapshots = update_positions_with_snapshots(existing, top10, {}, "2026-08-17")
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["action"], "HOLD")
        self.assertEqual(snapshots[0]["top10_rank"], 2)
        self.assertEqual(snapshots[0]["daily_return_pct"], 5.0)
        self.assertEqual(snapshots[0]["previous_mark_price"], 100.0)
        self.assertEqual(snapshots[0]["daily_price_change"], 5.0)
        self.assertEqual(snapshots[0]["pnl_pct"], 5.0)
        self.assertEqual(
            [snapshots[0][field] for field in ("open", "high", "low", "close")],
            [101.0, 106.0, 99.0, 105.0],
        )
        self.assertEqual(positions[0]["last_tracked_date"], "2026-08-17")

    def test_same_day_rerun_rebuilds_entries_without_duplicates(self):
        top10 = [{
            "代號": "2454", "名稱": "聯發科", "Rank": 1, "Score": 70,
            "開盤價": 100, "最高價": 111, "最低價": 99, "收盤價": 110,
            "Entry_Low": 105, "Entry_High": 110,
            "Entry_Stop": 100, "Entry_Target": 120,
        }]
        first_positions, first_snapshots = update_positions_with_snapshots([], top10, {}, "2026-08-17")
        second_positions, second_snapshots = update_positions_with_snapshots(
            first_positions, top10, {}, "2026-08-17"
        )
        self.assertEqual(len(first_positions), 1)
        self.assertEqual(len(second_positions), 1)
        self.assertEqual(first_positions[0]["position_id"], second_positions[0]["position_id"])
        self.assertEqual(first_snapshots, second_snapshots)
        self.assertEqual(first_positions[0]["status"], "PENDING")

    def test_next_session_without_zone_touch_expires_signal(self):
        top10 = [{
            "代號": "2454", "名稱": "聯發科", "Rank": 1, "Score": 70,
            "開盤價": 100, "最高價": 103, "最低價": 99, "收盤價": 101,
            "Entry_Low": 100, "Entry_High": 102,
            "Entry_Stop": 95, "Entry_Target": 110,
        }]
        positions, _ = update_positions_with_snapshots([], top10, {}, "2026-08-17")
        quotes = {"2454": {"Open": 110, "High": 112, "Low": 105, "Close": 108}}
        positions, snapshots = update_positions_with_snapshots(
            positions, [], quotes, "2026-08-18"
        )
        self.assertEqual(positions[0]["status"], "EXPIRED")
        self.assertEqual(positions[0]["expire_date"], "2026-08-18")
        self.assertEqual(snapshots[0]["action"], "ENTRY_EXPIRED")
        self.assertIsNone(snapshots[0]["entry_price"])

    def test_strategy_levels_and_risk_sizing_are_immutable_after_fill(self):
        top10 = [{
            "代號": "2454", "名稱": "聯發科", "Rank": 1, "Score": 88,
            "漲跌幅": 2.5, "產業": "半導體",
            "開盤價": 100, "最高價": 103, "最低價": 99, "收盤價": 101,
            "Entry_Low": 100, "Entry_High": 102,
            "Entry_Stop": 95, "Entry_Target": 110,
        }]
        positions, _ = update_positions_with_snapshots([], top10, {}, "2026-08-17")
        fill_quote = {"2454": {"Open": 101, "High": 104, "Low": 98, "Close": 102}}
        positions, fill_snapshots = update_positions_with_snapshots(
            positions, [], fill_quote, "2026-08-18"
        )
        self.assertEqual(positions[0]["shares"], 833)
        self.assertEqual(positions[0]["planned_risk_amount"], 4998)
        self.assertEqual(positions[0]["signal_score"], 88)
        self.assertEqual(positions[0]["signal_industry"], "半導體")
        self.assertEqual(fill_snapshots[0]["action"], "ENTRY")
        self.assertIsNotNone(fill_snapshots[0]["net_pnl_amount"])

        later_ranking = [{
            "代號": "2454", "名稱": "聯發科", "Rank": 9, "Score": 61,
            "開盤價": 101, "最高價": 105, "最低價": 94, "收盤價": 96,
            "Entry_Low": 90, "Entry_High": 92,
            "Entry_Stop": 80, "Entry_Target": 130,
        }]
        positions, snapshots = update_positions_with_snapshots(
            positions, later_ranking, {}, "2026-08-19"
        )
        self.assertEqual(positions[0]["status"], "CLOSED_SL")
        self.assertEqual(positions[0]["close_price"], 95)
        self.assertEqual(snapshots[0]["stop_price"], 95)
        self.assertEqual(snapshots[0]["target_price"], 110)
        self.assertEqual(snapshots[0]["signal_score"], 88)
        self.assertLess(snapshots[0]["net_pnl_amount"], -4998)

    def test_daily_snapshot_records_market_and_excess_return_without_claiming_cause(self):
        existing = [{
            "ticker": "2330", "name": "台積電", "entry_date": "2026-08-14",
            "entry_price": 100, "status": "OPEN", "highest_price": 102,
            "lowest_price": 98, "current_price": 100, "pnl_pct": 0,
        }]
        quotes = {"2330": {"Open": 98.5, "High": 100, "Low": 96, "Close": 97}}
        benchmark = {
            "symbol": "TAIEX", "close": 22000, "daily_return_pct": -1.2,
            "regime": "空頭",
        }
        _, snapshots = update_positions_with_snapshots(
            existing, [], quotes, "2026-08-17", benchmark=benchmark
        )
        self.assertEqual(snapshots[0]["benchmark_return_pct"], -1.2)
        self.assertEqual(snapshots[0]["excess_return_pct"], -1.8)
        self.assertEqual(snapshots[0]["market_regime"], "空頭")
        self.assertIn("大盤", snapshots[0]["decline_diagnostic"])

    def test_missing_quote_creates_explicit_daily_record_without_settlement(self):
        existing = [{
            "ticker": "2317", "name": "鴻海", "entry_date": "2026-08-14",
            "entry_price": 100, "status": "OPEN", "highest_price": 100,
            "lowest_price": 100, "current_price": 100,
        }]
        positions, snapshots = update_positions_with_snapshots(existing, [], {}, "2026-08-17")
        self.assertEqual(positions[0]["status"], "OPEN")
        self.assertEqual(snapshots[0]["action"], "DATA_MISSING")
        self.assertEqual(snapshots[0]["data_status"], "missing")
        self.assertIsNone(snapshots[0]["close"])
        self.assertIsNone(snapshots[0]["daily_return_pct"])
        self.assertIsNone(snapshots[0]["daily_price_change"])

    def test_partial_quote_does_not_fill_ohl_with_close(self):
        existing = [{
            "ticker": "2317", "name": "鴻海", "entry_date": "2026-08-14",
            "entry_price": 100, "status": "OPEN", "highest_price": 100,
            "lowest_price": 100, "current_price": 100,
        }]
        quotes = {"2317": {"Close": 86}}
        positions, snapshots = update_positions_with_snapshots(existing, [], quotes, "2026-08-17")
        self.assertEqual(positions[0]["status"], "OPEN")
        self.assertEqual(snapshots[0]["action"], "DATA_MISSING")
        self.assertIsNone(snapshots[0]["open"])

    def test_partial_top10_quote_is_not_used_as_a_new_entry(self):
        top10 = [{"代號": "2454", "名稱": "聯發科", "收盤價": 110}]
        positions, snapshots = update_positions_with_snapshots([], top10, {}, "2026-08-17")
        self.assertEqual(positions, [])
        self.assertEqual(snapshots, [])

    def test_partial_top10_row_does_not_override_a_complete_market_quote(self):
        existing = [{
            "ticker": "2317", "name": "鴻海", "entry_date": "2026-08-14",
            "entry_price": 100, "status": "OPEN", "highest_price": 100,
            "lowest_price": 100, "current_price": 100,
        }]
        top10 = [{"代號": "2317", "名稱": "鴻海", "Rank": 3, "收盤價": 101}]
        quotes = {"2317": {"Open": 100, "High": 103, "Low": 99, "Close": 102}}
        positions, snapshots = update_positions_with_snapshots(existing, top10, quotes, "2026-08-17")
        self.assertEqual(positions[0]["current_price"], 102)
        self.assertEqual(snapshots[0]["top10_rank"], 3)
        self.assertEqual(snapshots[0]["data_status"], "ok")

    def test_complete_top10_history_keeps_all_scan_fields(self):
        source = [{
            "代號": "2330", "名稱": "台積電", "Score": 72,
            "開盤價": 1000, "最高價": 1020, "最低價": 995, "收盤價": 1015,
            "Confidence": 88, "Reasons": ["量價齊揚"],
        }]
        rows = build_top10_history_rows(source)
        self.assertEqual(rows[0]["Rank"], 1)
        self.assertEqual(rows[0]["Confidence"], 88)
        self.assertEqual(rows[0]["最高價"], 1020)
        self.assertEqual(rows[0]["Reasons"], ["量價齊揚"])

    def test_non_finite_history_values_are_stored_as_missing_not_fake_numbers(self):
        rows = build_top10_history_rows([{
            "代號": "2330", "名稱": "台積電", "Score": 70,
            "Confidence": float("nan"), "Validation_WinRate": float("inf"),
        }])
        self.assertIsNone(rows[0]["Confidence"])
        self.assertIsNone(rows[0]["Validation_WinRate"])

    def test_legacy_position_backfills_only_from_its_entry_day_ranking(self):
        positions = [{
            "ticker": "2330", "name": "台積電", "entry_date": "2026-08-14",
            "entry_price": 100, "status": "OPEN",
        }]
        histories = {
            "2026-08-14": [{
                "代號": "2330", "WinRate": 52.6, "Backtest_Samples": 31,
                "Backtest_Scope": "入榜日回測",
            }],
            "2026-08-17": [{
                "代號": "2330", "WinRate": 91.0, "Backtest_Samples": 80,
            }],
        }
        result = backfill_entry_backtest_snapshots(positions, histories)
        self.assertEqual(result[0]["entry_win_rate"], 52.6)
        self.assertEqual(result[0]["entry_backtest_samples"], 31)
        self.assertEqual(result[0]["entry_backtest_scope"], "入榜日回測")
        self.assertNotIn("entry_win_rate", positions[0])

    def test_legacy_position_stays_missing_when_entry_day_source_is_unavailable(self):
        positions = [{
            "ticker": "2330", "entry_date": "2026-08-14", "entry_price": 100,
        }]
        histories = {
            "2026-08-17": [{"代號": "2330", "WinRate": 91.0, "Backtest_Samples": 80}],
        }
        result = backfill_entry_backtest_snapshots(positions, histories)
        self.assertNotIn("entry_win_rate", result[0])
        self.assertNotIn("entry_backtest_samples", result[0])

    def test_legacy_ranking_without_backtest_fields_is_marked_missing(self):
        positions = [{
            "ticker": "2330", "entry_date": "2026-08-14", "entry_price": 100,
        }]
        histories = {"2026-08-14": [{"代號": "2330", "Score": 70}]}
        result = backfill_entry_backtest_snapshots(positions, histories)
        self.assertEqual(result[0]["entry_backtest_status"], "missing")
        self.assertIsNone(result[0]["entry_win_rate"])
        self.assertIsNone(result[0]["entry_backtest_samples"])


if __name__ == "__main__":
    unittest.main()
