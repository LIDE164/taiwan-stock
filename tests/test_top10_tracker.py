import unittest

from execution_costs import calculate_max_odd_lot_position
from top10_tracker import (
    MAX_BEARISH_OPENING_GAP_PCT,
    MAX_ACTIVE_INDUSTRY_POSITIONS,
    MAX_HOLDING_SESSIONS,
    MIN_EXECUTION_REWARD_RISK,
    backfill_entry_backtest_snapshots,
    build_cumulative_performance_summary,
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
        self.assertEqual(positions[0]["expected_entry_date"], "2026-08-18")
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

        quotes = {"2454": {"Open": 108, "High": 115, "Low": 105, "Close": 109}}
        positions, snapshots = update_positions_with_snapshots(
            positions, [], quotes, "2026-08-18"
        )
        self.assertEqual(positions[0]["status"], "OPEN")
        self.assertEqual(positions[0]["entry_date"], "2026-08-18")
        self.assertEqual(positions[0]["entry_price"], 108)
        self.assertEqual(positions[0]["highest_price"], 115)
        self.assertEqual(positions[0]["lowest_price"], 105)
        expected_risk = calculate_max_odd_lot_position(108, 100, 5000)
        self.assertEqual(positions[0]["shares"], expected_risk.shares)
        self.assertAlmostEqual(
            positions[0]["planned_risk_amount"],
            round(expected_risk.estimated_net_loss, 2),
        )
        self.assertLessEqual(positions[0]["planned_risk_amount"], 5000)
        self.assertEqual(positions[0]["risk_model"], "commission_tax_stop_slippage")
        self.assertEqual(positions[0]["entry_bar_resolution"], "resolved")
        self.assertTrue(positions[0]["entry_bar_extremes_included"])
        self.assertEqual(snapshots[0]["action"], "ENTRY")
        self.assertEqual(snapshots[0]["stop_price"], 100)
        self.assertEqual(snapshots[0]["target_price"], 120)

    def test_open_in_zone_fill_applies_same_bar_stop_before_target(self):
        top10 = [{
            "代號": "2454", "名稱": "聯發科", "開盤價": 108,
            "最高價": 110, "最低價": 105, "收盤價": 109,
            "Entry_Low": 105, "Entry_High": 110,
            "Entry_Stop": 100, "Entry_Target": 120,
        }]
        positions, _ = update_positions_with_snapshots([], top10, {}, "2026-08-17")
        quotes = {"2454": {"Open": 108, "High": 125, "Low": 95, "Close": 112}}

        positions, snapshots = update_positions_with_snapshots(
            positions, [], quotes, "2026-08-18"
        )

        self.assertEqual(positions[0]["status"], "CLOSED_SL")
        self.assertEqual(positions[0]["entry_price"], 108)
        self.assertEqual(positions[0]["close_price"], 100)
        self.assertEqual(positions[0]["highest_price"], 108)
        self.assertEqual(positions[0]["lowest_price"], 100)
        self.assertEqual(
            positions[0]["last_bar_excursion_status"], "both_touch_stop_first"
        )
        self.assertEqual(snapshots[0]["action"], "STOP_LOSS")
        self.assertEqual(snapshots[0]["entry_bar_resolution"], "resolved")
        self.assertFalse(snapshots[0]["entry_bar_extremes_included"])

    def test_touch_derived_fill_is_unresolved_without_bar_extremes(self):
        top10 = [{
            "代號": "2454", "名稱": "聯發科", "開盤價": 108,
            "最高價": 110, "最低價": 105, "收盤價": 109,
            "Entry_Low": 105, "Entry_High": 110,
            "Entry_Stop": 100, "Entry_Target": 125,
        }]
        positions, _ = update_positions_with_snapshots([], top10, {}, "2026-08-17")
        quotes = {"2454": {"Open": 115, "High": 125, "Low": 105, "Close": 108}}

        positions, snapshots = update_positions_with_snapshots(
            positions, [], quotes, "2026-08-18"
        )

        self.assertEqual(positions[0]["status"], "UNRESOLVED")
        self.assertEqual(positions[0]["entry_price"], 110)
        self.assertEqual(positions[0]["fill_rule"], "PULLBACK_TOUCH")
        self.assertEqual(positions[0]["highest_price"], 110)
        self.assertEqual(positions[0]["lowest_price"], 110)
        self.assertEqual(positions[0]["entry_bar_resolution"], "unresolved")
        self.assertEqual(
            positions[0]["entry_bar_exit_check"],
            "unresolved_due_to_daily_ohlc_order",
        )
        self.assertFalse(snapshots[0]["entry_bar_extremes_included"])
        self.assertEqual(
            snapshots[0]["bar_excursion_status"], "entry_order_unresolved"
        )
        self.assertEqual(snapshots[0]["action"], "EXECUTION_UNRESOLVED")
        self.assertEqual(snapshots[0]["data_status"], "unresolved")
        self.assertIsNone(snapshots[0]["pnl_pct"])
        self.assertIsNone(snapshots[0]["net_pnl_amount"])

    def test_pullback_touch_resolves_a_stop_that_must_follow_the_fill(self):
        top10 = [{
            "代號": "2454", "名稱": "聯發科", "開盤價": 108,
            "最高價": 110, "最低價": 105, "收盤價": 109,
            "Entry_Low": 105, "Entry_High": 110,
            "Entry_Stop": 100, "Entry_Target": 125,
        }]
        positions, _ = update_positions_with_snapshots([], top10, {}, "2026-08-17")

        positions, snapshots = update_positions_with_snapshots(
            positions,
            [],
            {"2454": {"Open": 115, "High": 118, "Low": 95, "Close": 108}},
            "2026-08-18",
        )

        self.assertEqual(positions[0]["status"], "CLOSED_SL")
        self.assertEqual(positions[0]["close_price"], 100)
        self.assertEqual(snapshots[0]["action"], "STOP_LOSS")
        self.assertEqual(positions[0]["entry_bar_resolution"], "resolved")

    def test_gap_below_touch_cancels_instead_of_assuming_a_rebound_fill(self):
        top10 = [{
            "代號": "2454", "名稱": "聯發科", "開盤價": 108,
            "最高價": 110, "最低價": 105, "收盤價": 109,
            "Entry_Low": 105, "Entry_High": 110,
            "Entry_Stop": 100, "Entry_Target": 120,
        }]
        positions, _ = update_positions_with_snapshots([], top10, {}, "2026-08-17")

        positions, snapshots = update_positions_with_snapshots(
            positions,
            [],
            {"2454": {"Open": 103, "High": 125, "Low": 102, "Close": 121}},
            "2026-08-18",
        )

        self.assertEqual(positions[0]["fill_rule"], "GAP_BELOW_CANCELLED")
        self.assertEqual(positions[0]["status"], "EXPIRED")
        self.assertIsNone(positions[0]["entry_price"])
        self.assertEqual(positions[0]["entry_session_status"], "gap_below_cancelled")
        self.assertIn("跳空低於建議進場區", positions[0]["expire_reason"])
        self.assertEqual(snapshots[0]["action"], "ENTRY_EXPIRED")
        self.assertIsNone(snapshots[0]["net_pnl_amount"])

    def test_low_fill_adjusted_reward_risk_expires_without_opening(self):
        top10 = [{
            "代號": "2454", "名稱": "聯發科", "開盤價": 108,
            "最高價": 110, "最低價": 105, "收盤價": 109,
            "Entry_Low": 105, "Entry_High": 110,
            "Entry_Stop": 100, "Entry_Target": 112,
        }]
        positions, _ = update_positions_with_snapshots([], top10, {}, "2026-08-17")

        positions, snapshots = update_positions_with_snapshots(
            positions,
            [],
            {"2454": {"Open": 110, "High": 111, "Low": 105, "Close": 108}},
            "2026-08-18",
        )

        self.assertEqual(positions[0]["status"], "EXPIRED")
        self.assertIsNone(positions[0]["entry_price"])
        self.assertEqual(
            positions[0]["entry_session_status"], "reward_risk_below_minimum"
        )
        self.assertEqual(positions[0]["candidate_entry_price"], 110)
        self.assertEqual(positions[0]["actual_reward_risk"], 0.2)
        self.assertEqual(
            positions[0]["minimum_reward_risk"], MIN_EXECUTION_REWARD_RISK
        )
        self.assertEqual(snapshots[0]["action"], "ENTRY_EXPIRED")
        self.assertEqual(snapshots[0]["actual_reward_risk"], 0.2)

    def test_bearish_taiex_opening_gap_cancels_next_session_entry(self):
        top10 = [{
            "代號": "2454", "名稱": "聯發科", "開盤價": 100,
            "最高價": 103, "最低價": 99, "收盤價": 101,
            "Entry_Low": 100, "Entry_High": 102,
            "Entry_Stop": 95, "Entry_Target": 110,
            "Market_Regime": "多頭",
        }]
        positions, _ = update_positions_with_snapshots([], top10, {}, "2026-08-17")
        positions, snapshots = update_positions_with_snapshots(
            positions,
            [],
            {"2454": {"Open": 101, "High": 104, "Low": 99, "Close": 102}},
            "2026-08-18",
            benchmark={
                "date": "2026-08-18",
                "previous_trading_date": "2026-08-17",
                "opening_gap_pct": MAX_BEARISH_OPENING_GAP_PCT - 0.1,
            },
        )
        self.assertEqual(positions[0]["status"], "EXPIRED")
        self.assertEqual(positions[0]["entry_session_status"], "market_open_gap_veto")
        self.assertIsNone(positions[0]["entry_price"])
        self.assertEqual(snapshots[0]["action"], "ENTRY_EXPIRED")

    def test_active_industry_limit_rejects_a_third_signal_with_audit_record(self):
        existing = []
        for index in range(MAX_ACTIVE_INDUSTRY_POSITIONS):
            ticker = f"88{index:02d}"
            existing.append({
                "position_id": f"{ticker}:2026-08-14",
                "ticker": ticker,
                "name": ticker,
                "execution_schema": 2,
                "status": "OPEN",
                "entry_date": "2026-08-15",
                "entry_price": 100,
                "current_price": 101,
                "highest_price": 102,
                "lowest_price": 99,
                "shares": 100,
                "stop_price": 95,
                "target_price": 110,
                "signal_industry": "半導體",
                "last_tracked_date": "2026-08-17",
                "last_snapshot": {"ticker": ticker, "action": "HOLD"},
            })
        top10 = [{
            "代號": "2454", "名稱": "聯發科", "產業": "半導體",
            "開盤價": 100, "最高價": 103, "最低價": 99, "收盤價": 101,
            "Entry_Low": 100, "Entry_High": 102,
            "Entry_Stop": 95, "Entry_Target": 110,
        }]
        positions, snapshots = update_positions_with_snapshots(
            existing, top10, {}, "2026-08-17"
        )
        rejected = next(position for position in positions if position["ticker"] == "2454")
        self.assertEqual(rejected["status"], "EXPIRED")
        self.assertEqual(rejected["entry_session_status"], "portfolio_limit_veto")
        self.assertIn("半導體", rejected["expire_reason"])
        self.assertEqual(snapshots[-1]["action"], "ENTRY_EXPIRED")

    def test_accepted_fill_persists_effective_reward_risk_and_holding_limit(self):
        top10 = [{
            "代號": "2454", "名稱": "聯發科", "開盤價": 100,
            "最高價": 103, "最低價": 99, "收盤價": 101,
            "Entry_Low": 100, "Entry_High": 102,
            "Entry_Stop": 95, "Entry_Target": 110,
        }]
        positions, _ = update_positions_with_snapshots([], top10, {}, "2026-08-17")
        positions, snapshots = update_positions_with_snapshots(
            positions,
            [],
            {"2454": {"Open": 101, "High": 104, "Low": 99, "Close": 102}},
            "2026-08-18",
        )

        self.assertEqual(positions[0]["status"], "OPEN")
        self.assertEqual(positions[0]["actual_reward_risk"], 1.5)
        self.assertEqual(positions[0]["holding_session_count"], 1)
        self.assertEqual(positions[0]["max_holding_sessions"], MAX_HOLDING_SESSIONS)
        self.assertEqual(snapshots[0]["actual_reward_risk"], 1.5)
        self.assertEqual(snapshots[0]["holding_session_count"], 1)

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
        quotes = {"2317": {"Open": 85, "High": 120, "Low": 82, "Close": 110}}
        result = update_positions(existing, [], quotes, "2026-08-17")
        self.assertEqual(result[0]["close_price"], 85)
        self.assertEqual(result[0]["status"], "CLOSED_SL")
        self.assertEqual(result[0]["highest_price"], 100)
        self.assertEqual(result[0]["lowest_price"], 85)
        self.assertEqual(result[0]["last_bar_excursion_status"], "gap_stop")

    def test_intraday_exit_does_not_use_extremes_after_assumed_exit(self):
        existing = [{
            "ticker": "2330", "name": "台積電", "entry_date": "2026-08-14",
            "entry_price": 100, "status": "OPEN", "highest_price": 102,
            "lowest_price": 98, "current_price": 100,
        }]
        quotes = {"2330": {"Open": 100, "High": 120, "Low": 85, "Close": 110}}

        positions, snapshots = update_positions_with_snapshots(
            existing, [], quotes, "2026-08-17"
        )

        self.assertEqual(positions[0]["status"], "CLOSED_SL")
        self.assertEqual(positions[0]["close_price"], 90)
        self.assertEqual(positions[0]["highest_price"], 102)
        self.assertEqual(positions[0]["lowest_price"], 90)
        self.assertEqual(
            positions[0]["last_bar_excursion_status"], "both_touch_stop_first"
        )
        self.assertEqual(snapshots[0]["mfe_pct"], 2.0)
        self.assertEqual(snapshots[0]["mae_pct"], -10.0)

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

    def test_schema2_position_exits_at_ninth_complete_session_close_with_costs(self):
        top10 = [{
            "代號": "2454", "名稱": "聯發科", "開盤價": 100,
            "最高價": 103, "最低價": 99, "收盤價": 101,
            "Entry_Low": 100, "Entry_High": 102,
            "Entry_Stop": 95, "Entry_Target": 110,
        }]
        positions, _ = update_positions_with_snapshots([], top10, {}, "2026-08-17")
        positions, _ = update_positions_with_snapshots(
            positions,
            [],
            {"2454": {"Open": 101, "High": 104, "Low": 99, "Close": 102}},
            "2026-08-18",
        )
        self.assertEqual(positions[0]["holding_session_count"], 1)

        tracking_dates = [
            "2026-08-19", "2026-08-20", "2026-08-21", "2026-08-24",
            "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28",
        ]
        for index, date_text in enumerate(tracking_dates, start=2):
            positions, snapshots = update_positions_with_snapshots(
                positions,
                [],
                {"2454": {"Open": 102, "High": 105, "Low": 99, "Close": 104}},
                date_text,
            )
            self.assertEqual(positions[0]["holding_session_count"], index)
            if index < MAX_HOLDING_SESSIONS:
                self.assertEqual(positions[0]["status"], "OPEN")
                self.assertEqual(snapshots[0]["action"], "HOLD")

        self.assertEqual(positions[0]["status"], "CLOSED_TIME")
        self.assertEqual(positions[0]["close_date"], "2026-08-28")
        self.assertEqual(positions[0]["close_price"], 104)
        self.assertEqual(positions[0]["close_reason"], "MAX_HOLDING_SESSIONS")
        self.assertEqual(positions[0]["last_bar_excursion_status"], "max_holding_close")
        self.assertEqual(snapshots[0]["action"], "TIME_EXIT")
        self.assertGreater(snapshots[0]["estimated_transaction_cost"], 0)
        self.assertLess(
            snapshots[0]["net_pnl_amount"], snapshots[0]["gross_pnl_amount"]
        )

        summary = build_cumulative_performance_summary(positions, "2026-08-28")
        self.assertEqual(summary["execution_schema_2_plus"]["trade_count"], 1)

    def test_missing_quote_and_same_date_rerun_do_not_consume_holding_sessions(self):
        existing = [{
            "ticker": "2330", "name": "台積電", "execution_schema": 2,
            "entry_session_status": "filled", "entry_date": "2026-08-14",
            "entry_price": 100, "status": "OPEN", "highest_price": 102,
            "lowest_price": 98, "current_price": 100, "shares": 100,
            "stop_price": 90, "target_price": 115,
            "holding_session_count": 4,
            "max_holding_sessions": MAX_HOLDING_SESSIONS,
        }]
        positions, missing = update_positions_with_snapshots(
            existing, [], {}, "2026-08-17"
        )
        self.assertEqual(positions[0]["holding_session_count"], 4)
        self.assertEqual(missing[0]["action"], "DATA_MISSING")

        quote = {"2330": {"Open": 100, "High": 103, "Low": 99, "Close": 102}}
        positions, first = update_positions_with_snapshots(
            positions, [], quote, "2026-08-18"
        )
        self.assertEqual(positions[0]["holding_session_count"], 5)
        positions, rerun = update_positions_with_snapshots(
            positions, [], quote, "2026-08-18"
        )
        self.assertEqual(positions[0]["holding_session_count"], 5)
        self.assertEqual(rerun, first)

    def test_stop_or_target_on_final_session_takes_priority_over_time_exit(self):
        base = {
            "ticker": "2330", "name": "台積電", "execution_schema": 2,
            "entry_session_status": "filled", "entry_date": "2026-08-14",
            "entry_price": 100, "status": "OPEN", "highest_price": 102,
            "lowest_price": 98, "current_price": 100, "shares": 100,
            "stop_price": 90, "target_price": 115,
            "holding_session_count": MAX_HOLDING_SESSIONS - 1,
            "max_holding_sessions": MAX_HOLDING_SESSIONS,
        }
        positions, snapshots = update_positions_with_snapshots(
            [base],
            [],
            {"2330": {"Open": 100, "High": 116, "Low": 99, "Close": 104}},
            "2026-08-17",
        )
        self.assertEqual(positions[0]["status"], "CLOSED_TP")
        self.assertEqual(positions[0]["close_price"], 115)
        self.assertEqual(snapshots[0]["action"], "TAKE_PROFIT")

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

    def test_missing_expected_session_never_fills_from_a_later_bar(self):
        top10 = [{
            "代號": "2454", "名稱": "聯發科", "開盤價": 100,
            "最高價": 103, "最低價": 99, "收盤價": 101,
            "Entry_Low": 100, "Entry_High": 102,
            "Entry_Stop": 95, "Entry_Target": 110,
        }]
        positions, _ = update_positions_with_snapshots([], top10, {}, "2026-08-21")
        self.assertEqual(positions[0]["expected_entry_date"], "2026-08-24")

        positions, missing = update_positions_with_snapshots(
            positions, [], {}, "2026-08-24"
        )
        self.assertEqual(positions[0]["status"], "PENDING")
        self.assertEqual(positions[0]["entry_session_status"], "data_missing")
        self.assertEqual(missing[0]["action"], "DATA_MISSING")

        late_quote = {
            "2454": {"Open": 101, "High": 104, "Low": 99, "Close": 102}
        }
        positions, snapshots = update_positions_with_snapshots(
            positions, [], late_quote, "2026-08-25"
        )
        self.assertEqual(positions[0]["status"], "EXPIRED")
        self.assertIsNone(positions[0]["entry_price"])
        self.assertEqual(positions[0]["entry_session_status"], "missed")
        self.assertEqual(snapshots[0]["action"], "ENTRY_EXPIRED")
        self.assertEqual(snapshots[0]["data_status"], "missing")

    def test_missing_expected_session_can_be_repaired_on_the_same_date(self):
        top10 = [{
            "代號": "2454", "名稱": "聯發科", "開盤價": 100,
            "最高價": 103, "最低價": 99, "收盤價": 101,
            "Entry_Low": 100, "Entry_High": 102,
            "Entry_Stop": 95, "Entry_Target": 110,
        }]
        positions, _ = update_positions_with_snapshots([], top10, {}, "2026-08-17")
        positions, first = update_positions_with_snapshots(
            positions, [], {}, "2026-08-18"
        )
        self.assertEqual(first[0]["action"], "DATA_MISSING")

        quote = {"2454": {"Open": 101, "High": 104, "Low": 99, "Close": 102}}
        positions, repaired = update_positions_with_snapshots(
            positions, [], quote, "2026-08-18"
        )
        self.assertEqual(positions[0]["status"], "OPEN")
        self.assertEqual(positions[0]["entry_date"], "2026-08-18")
        self.assertEqual(repaired[0]["action"], "ENTRY")

    def test_confirmed_market_predecessor_handles_an_exchange_holiday(self):
        top10 = [{
            "代號": "2454", "名稱": "聯發科", "開盤價": 100,
            "最高價": 103, "最低價": 99, "收盤價": 101,
            "Entry_Low": 100, "Entry_High": 102,
            "Entry_Stop": 95, "Entry_Target": 110,
        }]
        positions, _ = update_positions_with_snapshots([], top10, {}, "2026-09-04")
        self.assertEqual(positions[0]["expected_entry_date"], "2026-09-07")

        quote = {"2454": {"Open": 101, "High": 104, "Low": 99, "Close": 102}}
        positions, snapshots = update_positions_with_snapshots(
            positions,
            [],
            quote,
            "2026-09-08",
            benchmark={
                "date": "2026-09-08",
                "previous_trading_date": "2026-09-04",
            },
        )

        self.assertEqual(positions[0]["status"], "OPEN")
        self.assertEqual(positions[0]["entry_date"], "2026-09-08")
        self.assertEqual(positions[0]["expected_entry_date"], "2026-09-08")
        self.assertEqual(
            positions[0]["expected_entry_date_basis"],
            "confirmed_market_predecessor",
        )
        self.assertEqual(snapshots[0]["action"], "ENTRY")

    def test_legacy_pending_without_guard_cannot_fill_after_expected_date(self):
        legacy_pending = [{
            "position_id": "2454:2026-08-17",
            "execution_schema": 2,
            "ticker": "2454", "name": "聯發科",
            "signal_date": "2026-08-17", "status": "PENDING",
            "planned_entry_low": 100, "planned_entry_high": 102,
            "stop_price": 95, "target_price": 110,
            "current_price": 101,
        }]
        late_quote = {
            "2454": {"Open": 101, "High": 104, "Low": 99, "Close": 102}
        }

        positions, snapshots = update_positions_with_snapshots(
            legacy_pending, [], late_quote, "2026-08-19"
        )

        self.assertEqual(positions[0]["expected_entry_date"], "2026-08-18")
        self.assertEqual(positions[0]["status"], "EXPIRED")
        self.assertIsNone(positions[0].get("entry_price"))
        self.assertEqual(snapshots[0]["action"], "ENTRY_EXPIRED")

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
        expected_risk = calculate_max_odd_lot_position(101, 95, 5000)
        self.assertEqual(positions[0]["shares"], expected_risk.shares)
        self.assertAlmostEqual(
            positions[0]["planned_risk_amount"],
            round(expected_risk.estimated_net_loss, 2),
        )
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
        self.assertLess(snapshots[0]["net_pnl_amount"], 0)
        self.assertLessEqual(
            abs(snapshots[0]["net_pnl_amount"]),
            positions[0]["planned_risk_amount"],
        )

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

    def test_cumulative_summary_separates_schema2_and_legacy_realized_trades(self):
        positions = [
            {
                "ticker": "1111", "execution_schema": 2, "status": "CLOSED_TP",
                "entry_session_status": "filled", "entry_date": "2026-09-01",
                "close_date": "2026-09-03", "entry_price": 100,
                "close_price": 110, "shares": 100,
            },
            {
                "ticker": "2222", "execution_schema": 1, "status": "CLOSED_SL",
                "entry_date": "2026-09-01", "close_date": "2026-09-04",
                "entry_price": 100, "close_price": 90, "shares": 100,
            },
        ]

        summary = build_cumulative_performance_summary(positions, "2026-09-09")

        self.assertEqual(summary["included_count"], 2)
        self.assertEqual(summary["excluded_count"], 0)
        self.assertEqual(summary["execution_schema_2_plus"]["trade_count"], 1)
        self.assertEqual(summary["execution_schema_2_plus"]["wins"], 1)
        self.assertEqual(summary["legacy"]["trade_count"], 1)
        self.assertEqual(summary["legacy"]["losses"], 1)

    def test_cumulative_summary_excludes_unresolved_missing_and_future_results(self):
        positions = [
            {"ticker": "pending", "status": "PENDING"},
            {
                "ticker": "unresolved", "execution_schema": 2,
                "status": "CLOSED_TP", "entry_session_status": "filled",
                "entry_date": "2026-09-01", "close_date": "2026-09-03",
                "entry_price": 100, "close_price": 110, "shares": 100,
                "entry_bar_resolution": "unresolved",
            },
            {
                "ticker": "future", "execution_schema": 2,
                "status": "CLOSED_TP", "entry_session_status": "filled",
                "entry_date": "2026-09-01", "close_date": "2026-09-10",
                "entry_price": 100, "close_price": 110, "shares": 100,
            },
            {
                "ticker": "missing", "execution_schema": 2,
                "status": "CLOSED_SL", "entry_session_status": "filled",
                "entry_date": "2026-09-01", "close_date": "2026-09-04",
                "entry_price": 100, "close_price": 90, "shares": 100,
                "data_status": "missing",
            },
        ]

        summary = build_cumulative_performance_summary(positions, "2026-09-09")

        self.assertEqual(summary["included_count"], 0)
        self.assertEqual(summary["excluded_count"], 4)
        self.assertIsNone(summary["execution_schema_2_plus"]["win_rate_pct"])


if __name__ == "__main__":
    unittest.main()
