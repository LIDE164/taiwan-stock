from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from prediction_watch import (
    MAX_MESSAGE_UNITS, format_prediction_messages, notification_slot, select_prediction_rows,
)


def now(clock="10:05:00", day="2026-10-05"):
    return datetime.fromisoformat(f"{day}T{clock}+08:00")


def row(ticker="2330", **changes):
    return {"代號": ticker, "名稱": "台積電", "Data_Date": "2026-10-02", "Score": 90,
            "Entry_Status": "現在可執行", "Entry_Ready": True,
            "Execution_Versions": ["new", "legacy"], "收盤價": 999,
            **changes}


def quote(**changes):
    return {"status": "ok", "open": 100, "price": 103, "previous_close": 98,
            "observed_at": "2026-10-05T10:04:00+08:00", "date": "2026-10-05",
            "source": "TWSE MIS", "final": False, **changes}


def messages(rows=None, quotes=None, **changes):
    options = {"analysis_date": "2026-10-02", "trading_date": "2026-10-05",
               "slot": "1000", "now": now(), **changes}
    return format_prediction_messages([row()] if rows is None else rows,
                                      {"2330": quote()} if quotes is None else quotes, **options)


class PredictionSlotTests(unittest.TestCase):
    def test_slot_boundaries_do_not_backfill_previous_hour(self):
        expected = {
            "08:59:59": None, "09:00:00": None, "09:04:59": None, "09:05:00": "0900",
            "09:59:59": "0900", "10:00:00": None, "10:04:59": None, "10:05:00": "1000",
            "10:59:59": "1000", "11:05:00": "1100", "12:05:00": "1200",
            "12:59:59": "1200", "13:00:00": None, "13:04:59": None, "13:05:00": "1300",
            "13:30:00": "1300", "13:34:59": "1300", "13:35:00": "close",
            "14:29:59": "close", "14:30:00": None, "23:00:00": None,
        }
        for clock, slot in expected.items():
            with self.subTest(clock=clock):
                self.assertEqual(notification_slot(now(clock)), slot)

    def test_holiday_weekend_and_unknown_year_do_not_send(self):
        for day in ("2026-10-03", "2026-10-09", "2026-09-28", "2027-10-05"):
            self.assertIsNone(notification_slot(now(day=day)))

    def test_time_zone_conversion_and_naive_rejection(self):
        self.assertEqual(notification_slot(now().astimezone(timezone.utc)), "1000")
        with self.assertRaises(ValueError):
            notification_slot(datetime(2026, 10, 5, 10, 5))


class PredictionSelectionTests(unittest.TestCase):
    def test_uses_original_union_and_preserves_input(self):
        manifest = {"scan_date": "2026-10-02", "data": [row("2330"), row("2317", Score=95)]}
        frozen = deepcopy(manifest)
        selected = select_prediction_rows(manifest, now())
        self.assertEqual([r["代號"] for r in selected], ["2317", "2330"])
        self.assertEqual(selected[0]["Execution_Versions"], ["new"])
        self.assertEqual(manifest, frozen)
        selected[0]["Entry_Ready"] = False
        self.assertEqual(manifest, frozen)

    def test_limits_are_owned_by_existing_comparison_selector(self):
        with patch("prediction_watch.build_comparison_rows", return_value=[row()]) as selector:
            data = [row()]
            self.assertEqual(select_prediction_rows({"scan_date": "2026-10-02", "data": data}, now()), [row()])
            selector.assert_called_once_with(data)

    def test_empty_list_is_allowed_without_inventing_stock(self):
        self.assertEqual(select_prediction_rows({"scan_date": "2026-10-02", "data": []}, now()), [])

    def test_missing_malformed_mixed_and_expired_manifests_raise(self):
        manifests = [None, {}, {"scan_date": "2026-10-02"},
                     {"scan_date": "2026-10-02", "data": "broken"},
                     {"scan_date": "2026-10-02", "data": [None]},
                     {"scan_date": "2026-10-02", "data": [row(Data_Date="2026-10-01")]},
                     {"scan_date": "2026-10-01", "data": []},
                     {"scan_date": "2026-10-04", "data": []},
                     {"scan_date": "2026-10-05", "data": []}]
        for manifest in manifests:
            with self.subTest(manifest=manifest), self.assertRaises(ValueError):
                select_prediction_rows(manifest, now())

    def test_unknown_year_and_nontrading_today_raise(self):
        for day in ("2027-10-05", "2026-10-03", "2026-10-09"):
            with self.assertRaises(ValueError):
                select_prediction_rows({"scan_date": "2026-10-02", "data": []}, now(day=day))

    def test_official_long_weekend_targets_next_session(self):
        result = select_prediction_rows({"scan_date": "2026-09-24", "data": []}, now(day="2026-09-29"))
        self.assertEqual(result, [])


class PredictionMessagesTests(unittest.TestCase):
    def test_all_prices_returns_dates_and_version_labels(self):
        text = messages()[0]
        for expected in ("2330 台積電｜新制＋舊制", "開盤 100｜現價 103", "較開盤 +3.00%",
                         "較昨收 +5.10%", "2026-10-02", "2026-10-05", "10時盤中更新",
                         "10:05:00", "行情 10:04:00", "TWSE MIS", "不是進場訊號"):
            self.assertIn(expected, text)
        self.assertNotIn("999", text)

    def test_missing_open_never_falls_back_to_current_price(self):
        text = messages(quotes={"2330": quote(open=None)})[0]
        self.assertIn("開盤 --｜現價 103", text)
        self.assertIn("較開盤 --｜較昨收 +5.10%", text)

    def test_missing_previous_close_never_uses_saved_prediction_close(self):
        text = messages(quotes={"2330": quote(previous_close=None)})[0]
        self.assertIn("較昨收 --", text)
        self.assertNotIn("999", text)

    def test_previous_close_date_if_provided_must_be_previous_session(self):
        valid = messages(quotes={"2330": quote(previous_close_date="2026-10-02")})[0]
        self.assertIn("較昨收 +5.10%", valid)
        for day in (None, "2026-10-01", "2026-10-03", "2026-10-05", "2026-10-06", "bad"):
            with self.subTest(day=day):
                text = messages(quotes={"2330": quote(previous_close_date=day)})[0]
                self.assertIn("較昨收 --", text)
                self.assertIn("較開盤 +3.00%", text)

    def test_invalid_prices_are_not_used(self):
        for value in (None, 0, -1, True, float("inf"), float("nan"), "invalid"):
            with self.subTest(value=value):
                text = messages(quotes={"2330": quote(price=value)})[0]
                self.assertIn("現價 --", text)
                self.assertIn("成交價未確認", text)
                text = messages(quotes={"2330": quote(open=value, previous_close=value)})[0]
                self.assertIn("較開盤 --｜較昨收 --", text)

    def test_unavailable_keeps_stock_with_clear_unknowns(self):
        for quotes in ({}, {"2330": None}, {"2330": quote(status="unavailable")}):
            text = messages(quotes=quotes)[0]
            self.assertIn("2330 台積電", text)
            self.assertIn("行情無法取得", text)
            self.assertIn("現價 --", text)

    def test_unconfirmed_close_keeps_verified_open_not_unconfirmed_price(self):
        text = messages(quotes={"2330": quote(status="unavailable", price=None)},
                        slot="close", now=now("13:35:00"))[0]
        self.assertIn("開盤 100｜收盤待確認 --", text)
        self.assertIn("較開盤 --｜較昨收 --", text)
        self.assertIn("行情 10:04:00｜TWSE MIS", text)
        self.assertIn("僅保留已驗證開盤價", text)
        self.assertNotIn("最新已知價", text)
        # The unavailable status must override even a stray numeric price.
        stray = messages(quotes={"2330": quote(status="unavailable", price=103)},
                         slot="close", now=now("13:35:00"))[0]
        self.assertIn("開盤 100｜收盤待確認 --", stray)

    def test_unconfirmed_quote_cannot_keep_open_with_invalid_date_or_timestamp(self):
        for invalid in ({"date": "2026-10-02"}, {"date": "2026-10-06"},
                        {"observed_at": None}, {"observed_at": "2026-10-05T08:59:00+08:00"},
                        {"observed_at": "2026-10-05T14:00:00+08:00"},
                        {"observed_at": "2026-10-05T13:30:00"}):
            with self.subTest(invalid=invalid):
                text = messages(quotes={"2330": quote(status="unavailable", price=None, **invalid)},
                                slot="close", now=now("13:35:00"))[0]
                self.assertIn("開盤 --｜收盤待確認 --", text)
                self.assertNotIn("開盤 100", text)

    def test_quote_dates_naive_future_and_outside_session_are_rejected(self):
        invalid = ({"date": "2026-10-02"}, {"date": "2026-10-06"},
                   {"observed_at": "2026-10-05T10:06:00+08:00"},
                   {"observed_at": "2026-10-05T10:04:00"}, {"observed_at": None},
                   {"observed_at": "not a timestamp"}, {"observed_at": "2026-10-05T08:59:59+08:00"},
                   {"observed_at": "2026-10-04T10:04:00+08:00"})
        for changes in invalid:
            with self.subTest(changes=changes):
                text = messages(quotes={"2330": quote(**changes)})[0]
                self.assertIn("現價 --", text)
                self.assertNotIn("較開盤 +", text)

    def test_utc_timestamp_is_converted(self):
        text = messages(quotes={"2330": quote(observed_at="2026-10-05T02:04:00Z")})[0]
        self.assertIn("行情 10:04:00", text)

    def test_old_same_day_quote_is_labeled_delayed(self):
        text = messages(quotes={"2330": quote(observed_at="2026-10-05T09:44:59+08:00")})[0]
        self.assertIn("延遲／舊報價", text)
        self.assertIn("現價 103", text)
        self.assertNotIn("非逐筆即時", text)
        exact = messages(quotes={"2330": quote(observed_at="2026-10-05T09:45:00+08:00")})[0]
        self.assertNotIn("延遲／舊報價", exact)

    def test_close_requires_explicit_final_and_final_session_time(self):
        kwargs = {"slot": "close", "now": now("13:35:00")}
        final = messages(quotes={"2330": quote(final=True, observed_at="2026-10-05T13:30:00+08:00")}, **kwargs)[0]
        self.assertIn("開盤 100｜收盤 103", final)
        for changes in ({"final": False, "observed_at": "2026-10-05T13:30:00+08:00"},
                        {"final": True, "observed_at": "2026-10-05T13:29:59+08:00"},
                        {"final": 1, "observed_at": "2026-10-05T13:30:00+08:00"}):
            text = messages(quotes={"2330": quote(**changes)}, **kwargs)[0]
            self.assertIn("收盤待確認（最新已知價） 103", text)
            self.assertNotIn("｜收盤 103", text)
        missing = messages(quotes={}, **kwargs)[0]
        self.assertIn("收盤待確認 --", missing)

    def test_after_hours_quotes_do_not_masquerade_as_regular_close(self):
        text = messages(quotes={"2330": quote(final=True, observed_at="2026-10-05T14:00:00+08:00")},
                        slot="close", now=now("14:05:00"))[0]
        self.assertIn("非一般交易時段行情", text)
        self.assertIn("收盤待確認 --", text)

    def test_confirmed_closing_price_is_not_mislabeled_delayed_next_hour(self):
        text = messages(quotes={"2330": quote(final=True, observed_at="2026-10-05T13:30:00+08:00")},
                        slot="close", now=now("14:05:00"))[0]
        self.assertIn("開盤 100｜收盤 103", text)
        self.assertIn("當日收盤已確認", text)
        self.assertIn("行情 13:30:00", text)
        self.assertNotIn("延遲／舊報價", text)

    def test_all_twenty_stocks_split_max_ten_and_each_part_self_contained(self):
        rows = [row(str(1000 + i), 名稱="🧪" * 1000) for i in range(20)]
        quotes = {r["代號"]: quote(source="🧪" * 1000) for r in rows}
        parts = messages(rows, quotes)
        for part in parts:
            self.assertLess(len(part.encode("utf-16-le")) // 2, MAX_MESSAGE_UNITS)
            self.assertLessEqual(part.count("開盤 100｜"), 10)
            for expected in ("2026-10-02", "2026-10-05", "10:05:00", "不是進場訊號"):
                self.assertIn(expected, part)
        for r in rows:
            self.assertEqual("\n".join(parts).count(r["代號"] + " "), 1)

    def test_no_input_mutation(self):
        rows, quotes = [row()], {"2330": quote()}
        original = deepcopy((rows, quotes))
        messages(rows, quotes)
        self.assertEqual((rows, quotes), original)

    def test_empty_watchlist_explicit(self):
        parts = messages(rows=[])
        self.assertEqual(len(parts), 1)
        self.assertIn("沒有預測名單股票", parts[0])

    def test_invalid_notice_dates_current_slot_or_input_raise(self):
        for changes in ({"analysis_date": "2026-10-01"}, {"analysis_date": "2026-10-04"},
                        {"trading_date": "2026-10-06"}, {"trading_date": "unknown"},
                        {"slot": "0900"}, {"slot": "made_up"}, {"now": now("10:00:00")},
                        {"now": now(day="2027-10-05")}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                messages(**changes)
        for rows in ([None], [row(Data_Date="2026-10-01")], [row(代號="<invalid>")], "invalid"):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                messages(rows=rows)


if __name__ == "__main__":
    unittest.main()
