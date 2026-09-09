import unittest

import numpy as np
import pandas as pd

from analysis_core import apply_technical_indicators, build_score_input


class AnalysisValidationTests(unittest.TestCase):
    def _frame(self):
        close = np.linspace(100, 120, 80)
        raw = pd.DataFrame({
            "Open": close - 0.2,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": np.full(len(close), 1_000_000),
        })
        return apply_technical_indicators(raw)

    def test_missing_60ma_does_not_use_close_as_a_substitute(self):
        frame = self._frame()
        frame.loc[frame.index[-1], "60MA"] = np.nan
        self.assertEqual(build_score_input(frame), {})

    def test_missing_previous_macd_does_not_use_zero_as_a_substitute(self):
        frame = self._frame()
        frame.loc[frame.index[-2], "MACD_Hist"] = np.nan
        self.assertEqual(build_score_input(frame), {})

    def test_roc20_uses_close_from_twenty_sessions_ago(self):
        frame = self._frame().tail(21)

        score_input = build_score_input(frame)

        expected = (frame["Close"].iloc[-1] / frame["Close"].iloc[-21] - 1) * 100
        self.assertAlmostEqual(score_input["ROC_20"], expected)

    def test_roc20_requires_twenty_one_observations(self):
        self.assertEqual(build_score_input(self._frame().tail(20)), {})

    def test_tomorrow_five_ma_deduction_uses_t_minus_four_close(self):
        frame = self._frame().tail(21)

        score_input = build_score_input(frame)

        self.assertEqual(
            score_input["明日5MA扣抵價"],
            round(float(frame["Close"].iloc[-5]), 2),
        )


if __name__ == "__main__":
    unittest.main()
