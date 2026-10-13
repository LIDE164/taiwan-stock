from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from completed_daily_bar import merge_completed_daily_bar, TPE


def frame(days, *, actions=True):
    data = {"Open": [100.0] * len(days), "High": [105.0] * len(days),
            "Low": [99.0] * len(days), "Close": [103.0] * len(days), "Volume": [1000.0] * len(days)}
    if actions:
        data.update({"Dividends": [0.0] * len(days), "Stock Splits": [0.0] * len(days)})
    return pd.DataFrame(data, index=pd.DatetimeIndex(days))


class CompletedDailyBarTests(unittest.TestCase):
    def setUp(self):
        self.history = frame(["2026-10-07", "2026-10-08"])
        self.latest = frame(["2026-10-12"])
        self.now = datetime(2026, 10, 13, 8, 20, tzinfo=TPE)

    def merge(self, **kwargs):
        return merge_completed_daily_bar(
            kwargs.pop("history", self.history), kwargs.pop("latest", self.latest),
            kwargs.pop("expected_date", "2026-10-12"), now=kwargs.pop("now", self.now), **kwargs,
        )

    def test_merges_one_real_bar_over_official_holiday_and_preserves_inputs_attrs(self):
        self.history.attrs = {"diagnostic": {"count": 1}}
        before = deepcopy(self.history)
        latest_before = deepcopy(self.latest)
        merged = self.merge()
        self.assertEqual(len(merged), 3)
        self.assertEqual(merged.index[-1], pd.Timestamp("2026-10-12"))
        self.assertEqual(merged.iloc[-1]["Close"], 103)
        self.assertEqual(merged.attrs["completed_daily_bar_previous_date"], "2026-10-08")
        self.assertEqual(merged.attrs["completed_daily_bar_date"], "2026-10-12")
        self.assertIn("Yahoo", merged.attrs["completed_daily_bar_source"])
        self.assertEqual(merged.attrs["diagnostic"], {"count": 1})
        assert_frame_equal(self.history, before)
        assert_frame_equal(self.latest, latest_before)

    def test_same_day_postclose_is_allowed_but_intraday_is_not(self):
        merged = self.merge(now=datetime(2026, 10, 12, 14, 30, tzinfo=TPE))
        self.assertEqual(len(merged), 3)
        for hour, minute in ((8, 0), (9, 0), (13, 30), (14, 29)):
            with self.subTest(hour=hour, minute=minute), self.assertRaises(ValueError):
                self.merge(now=datetime(2026, 10, 12, hour, minute, tzinfo=TPE))

    def test_preopen_recovery_rejects_opening_or_later_and_obsolete_target(self):
        for now in (self.now.replace(hour=9, minute=0), self.now.replace(hour=16),
                    self.now.replace(day=14), self.now.replace(tzinfo=None)):
            with self.subTest(now=now), self.assertRaises(ValueError):
                self.merge(now=now)

    def test_utc_clock_and_aware_indices_are_converted_to_taipei(self):
        self.history.index = self.history.index.tz_localize(TPE).tz_convert("UTC")
        self.latest.index = self.latest.index.tz_localize(TPE).tz_convert("UTC")
        merged = self.merge(now=self.now.astimezone(timezone.utc))
        self.assertIsNone(merged.index.tz)
        self.assertEqual(merged.index[-1].date().isoformat(), "2026-10-12")

    def test_stale_indicators_are_not_carried_forward(self):
        self.history["MA20"] = 101
        self.history["MACD_Hist"] = 2
        merged = self.merge()
        self.assertNotIn("MA20", merged.columns)
        self.assertNotIn("MACD_Hist", merged.columns)

    def test_cannot_bridge_two_missing_sessions(self):
        with self.assertRaises(ValueError):
            self.merge(history=frame(["2026-10-06", "2026-10-07"]))

    def test_refuses_wrong_target_or_multiple_supplement_bars(self):
        for latest in (frame(["2026-10-08"]), frame(["2026-10-08", "2026-10-12"]),
                       frame(["2026-10-13"]), frame([])):
            with self.subTest(index=latest.index), self.assertRaises(ValueError):
                self.merge(latest=latest)

    def test_refuses_duplicate_or_unsorted_history_or_future_bars(self):
        for days in (["2026-10-08", "2026-10-08"], ["2026-10-08", "2026-10-07"],
                     ["2026-10-08", "2026-10-13"], ["2026-10-08 00:00", "2026-10-08 09:00"]):
            with self.subTest(days=days), self.assertRaises(ValueError):
                self.merge(history=frame(days))

    def test_refuses_nan_nat_or_nondatetime_indices(self):
        for index in (pd.DatetimeIndex([pd.NaT]), pd.Index(["2026-10-12"])):
            latest = self.latest.copy()
            latest.index = index
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.merge(latest=latest)

    def test_invalid_new_ohlcv_never_repaired(self):
        for key, value in (("Open", 0), ("Close", np.nan), ("High", np.inf), ("Low", 104),
                           ("High", 102), ("Volume", -1), ("Volume", np.nan)):
            latest = self.latest.copy()
            latest.loc[latest.index[0], key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.merge(latest=latest)

    def test_zero_volume_allowed_for_actual_index_bar(self):
        latest = frame(["2026-10-12"], actions=False)
        latest["Volume"] = 0
        merged = self.merge(history=frame(["2026-10-08"], actions=False), latest=latest, require_actions=False)
        self.assertEqual(merged.iloc[-1]["Volume"], 0)

    def test_company_action_missing_unknown_or_nonzero_blocks_join_on_either_side(self):
        for side in ("history", "latest"):
            for key in ("Dividends", "Stock Splits"):
                for value in (None, 1.0, np.nan):
                    modified = getattr(self, side).copy()
                    if value is None:
                        modified = modified.drop(columns=key)
                    else:
                        modified.loc[modified.index[-1], key] = value
                    with self.subTest(side=side, key=key, value=value), self.assertRaises(ValueError):
                        self.merge(**{side: modified})

    def test_index_mode_still_rejects_present_nonzero_actions(self):
        self.latest["Dividends"] = 1
        with self.assertRaises(ValueError):
            self.merge(require_actions=False)

    def test_existing_null_cell_can_be_filled_but_conflicting_price_cannot(self):
        self.history = frame(["2026-10-08", "2026-10-12"])
        self.history.loc[pd.Timestamp("2026-10-12"), "Close"] = np.nan
        merged = self.merge()
        self.assertEqual(merged.iloc[-1]["Close"], 103)
        self.history.loc[pd.Timestamp("2026-10-12"), "Open"] = 101
        with self.assertRaises(ValueError):
            self.merge()

    def test_nonfinite_existing_cell_is_not_silently_replaced(self):
        self.history = frame(["2026-10-08", "2026-10-12"])
        self.history.loc[pd.Timestamp("2026-10-12"), "Close"] = np.inf
        with self.assertRaises(ValueError):
            self.merge()

    def test_invalid_previous_bar_blocks_join(self):
        self.history.loc[self.history.index[-1], "High"] = 99
        with self.assertRaises(ValueError):
            self.merge()

    def test_expected_date_must_be_strict_known_session(self):
        for expected in ("2026-10-09", "20261012", "2026-10-12T00:00:00", "2027-01-04", None):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                self.merge(expected_date=expected)


if __name__ == "__main__":
    unittest.main()
