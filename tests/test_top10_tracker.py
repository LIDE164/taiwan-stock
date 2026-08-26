import unittest

from top10_tracker import (
    backfill_entry_backtest_snapshots,
    build_top10_history_rows,
    update_positions,
    update_positions_with_snapshots,
)


class Top10TrackerTests(unittest.TestCase):
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

    def test_new_close_entry_does_not_use_earlier_intraday_extremes(self):
        top10 = [{
            "代號": "2454", "名稱": "聯發科", "開盤價": 100,
            "最高價": 130, "最低價": 70, "收盤價": 110,
            "WinRate": 54.32, "Backtest_Samples": 37,
            "Backtest_Scope": "純技術面逐步前推",
        }]
        positions, snapshots = update_positions_with_snapshots([], top10, {}, "2026-08-17")
        self.assertEqual(positions[0]["status"], "OPEN")
        self.assertEqual(positions[0]["entry_price"], 110)
        self.assertEqual(positions[0]["highest_price"], 110)
        self.assertEqual(positions[0]["entry_win_rate"], 54.32)
        self.assertEqual(positions[0]["entry_backtest_samples"], 37)
        self.assertEqual(positions[0]["entry_backtest_scope"], "純技術面逐步前推")
        self.assertEqual(positions[0]["entry_backtest_status"], "ok")
        self.assertEqual(snapshots[0]["entry_win_rate"], 54.32)
        self.assertEqual(snapshots[0]["entry_backtest_samples"], 37)

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
        }]
        first_positions, first_snapshots = update_positions_with_snapshots([], top10, {}, "2026-08-17")
        second_positions, second_snapshots = update_positions_with_snapshots(
            first_positions, top10, {}, "2026-08-17"
        )
        self.assertEqual(len(first_positions), 1)
        self.assertEqual(len(second_positions), 1)
        self.assertEqual(first_positions[0]["position_id"], second_positions[0]["position_id"])
        self.assertEqual(first_snapshots, second_snapshots)

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
