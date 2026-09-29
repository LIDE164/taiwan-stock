import unittest
from datetime import date, datetime

from market_calendar import is_scheduled_session, next_scheduled_session
from top10_telegram import prediction_title


class MarketCalendarTests(unittest.TestCase):
    def test_mid_autumn_and_teachers_day_are_not_forecast_sessions(self):
        self.assertEqual(next_scheduled_session("2026-09-24"), date(2026, 9, 29))
        self.assertEqual(prediction_title("2026-09-24"), "9/29股票預測")
        for day in (25, 26, 27, 28):
            with self.subTest(day=day):
                self.assertFalse(is_scheduled_session(date(2026, 9, day)))
        self.assertTrue(is_scheduled_session("2026-09-29"))

    def test_lunar_new_year_includes_settlement_only_days(self):
        self.assertEqual(next_scheduled_session("2026-02-11"), date(2026, 2, 23))
        for day in range(12, 23):
            with self.subTest(day=day):
                self.assertFalse(is_scheduled_session(date(2026, 2, day)))

    def test_official_opening_notices_are_not_holidays(self):
        for day in ("2026-01-02", "2026-02-11", "2026-02-23"):
            with self.subTest(day=day):
                self.assertTrue(is_scheduled_session(day))

    def test_other_official_2026_holiday_spans(self):
        expected = {
            "2026-01-01": "2026-01-02",
            "2026-02-26": "2026-03-02",
            "2026-04-02": "2026-04-07",
            "2026-04-30": "2026-05-04",
            "2026-06-18": "2026-06-22",
            "2026-10-08": "2026-10-12",
            "2026-10-23": "2026-10-27",
            "2026-12-24": "2026-12-28",
        }
        for previous, following in expected.items():
            with self.subTest(previous=previous):
                self.assertEqual(next_scheduled_session(previous), date.fromisoformat(following))

    def test_regular_sessions_and_weekend_keep_existing_titles(self):
        self.assertEqual(prediction_title("2026-08-26"), "8/27股票預測")
        self.assertEqual(prediction_title("2026-08-28"), "8/31股票預測")
        self.assertEqual(next_scheduled_session("2026-09-29"), date(2026, 9, 30))

    def test_unknown_years_and_year_boundary_never_guess(self):
        for value in ("2025-12-31", "2026-12-31", "2027-01-01", "9999-12-31"):
            with self.subTest(value=value):
                self.assertIsNone(next_scheduled_session(value))
                self.assertEqual(prediction_title(value), "下一交易日股票預測")
        self.assertIsNone(is_scheduled_session("2027-01-04"))

    def test_invalid_dates_stay_unknown(self):
        for value in (None, "invalid", "2026-02-30", "", 20260924):
            with self.subTest(value=value):
                self.assertIsNone(next_scheduled_session(value))
                self.assertIsNone(is_scheduled_session(value))

    def test_date_and_iso_datetime_inputs_remain_compatible(self):
        for value in (
            date(2026, 9, 24), datetime(2026, 9, 24, 15),
            "2026-09-24T15:00:00+08:00", " 2026-09-24 ",
        ):
            with self.subTest(value=value):
                self.assertEqual(next_scheduled_session(value), date(2026, 9, 29))


if __name__ == "__main__":
    unittest.main()
