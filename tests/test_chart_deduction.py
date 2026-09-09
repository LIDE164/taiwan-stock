import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from analysis_core import apply_technical_indicators
from charts import draw_professional_chart


class ChartDeductionTests(unittest.TestCase):
    def test_chart_labels_t_minus_four_as_tomorrow_five_ma_deduction(self):
        close = np.linspace(100, 120, 80)
        frame = apply_technical_indicators(pd.DataFrame(
            {
                "Open": close - 0.2,
                "High": close + 1,
                "Low": close - 1,
                "Close": close,
                "Volume": np.full(len(close), 1_000_000),
            },
            index=pd.date_range("2026-01-01", periods=len(close), freq="B"),
        ))
        enriched = frame.assign(
            ai_score=0,
            ai_buy=False,
            ai_confidence=0,
            ai_pattern="資料不足",
            ai_conflict="低",
            ai_hover="資料不足",
        )

        with patch("charts.compute_ai_signals", return_value=enriched):
            figure = draw_professional_chart(
                frame,
                float(frame["Close"].iloc[-1]),
                view_days=30,
                show_buy_signal=False,
                show_sup_res=False,
                show_signals=False,
            )

        visible = frame.tail(30)
        trace = next(item for item in figure.data if item.name == "5MA扣抵")
        self.assertEqual(trace.x[0], visible.index[-5].strftime("%Y-%m-%d"))
        self.assertIn(f"明日扣抵 {visible['Close'].iloc[-5]:.1f}", trace.text[0])


if __name__ == "__main__":
    unittest.main()
