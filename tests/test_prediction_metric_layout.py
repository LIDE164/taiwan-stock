"""Regression coverage for the prediction card's grouped backtest evidence."""

from copy import deepcopy
import io
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from legacy_backtest import LEGACY_BACKTEST_COMMIT, LEGACY_BACKTEST_SCHEMA
from top10_telegram import render_executable_image


def prediction_record(ticker="2330", **updates):
    row = {
        "代號": ticker, "名稱": "測試", "Score": 80,
        "Data_Date": "2026-09-22", "收盤價": 100, "漲跌幅": 1.25,
        "Entry_Status": "現在可執行", "Entry_Ready": True,
        "Entry_Low": 98, "Entry_High": 100, "Entry_Stop": 95, "Entry_Target": 110,
        "Entry_Reason": "價格位於回測區且量比達標",
        "Backtest_Schema": "executable_v3", "WinRate": 99,
        "Backtest_Samples": 1, "Backtest_Training_Samples": 1,
        "Backtest_Overall_Samples": 2, "Validation_Samples": 1,
        "Legacy_Backtest": {
            "schema": LEGACY_BACKTEST_SCHEMA, "source_commit": LEGACY_BACKTEST_COMMIT,
            "as_of_date": "2026-09-22", "data_through": "2026-09-22",
            "status": "complete", "samples": 38, "wins": 18, "losses": 20,
            "win_rate": 47.4,
        },
        "Mini_K": [
            {"open": 99, "high": 102, "low": 98, "close": 100},
            {"open": 100, "high": 103, "low": 99, "close": 101},
        ],
    }
    row.update(updates)
    return row


def render_with_text_calls(records, *, comparison=False):
    """Observe real coordinates and glyph bounds while still drawing the PNG."""
    calls = []
    original_text = ImageDraw.ImageDraw.text

    def capture_text(draw, xy, text, *args, **kwargs):
        calls.append({
            "xy": xy, "text": str(text),
            "bbox": draw.textbbox(xy, text, font=kwargs.get("font"), anchor=kwargs.get("anchor")),
        })
        return original_text(draw, xy, text, *args, **kwargs)

    with patch.object(ImageDraw.ImageDraw, "text", new=capture_text):
        png = render_executable_image(
            records, "2026-09-22",
            **({"comparison_results": records} if comparison else {}),
        )
    with Image.open(io.BytesIO(png)) as image:
        image.load()
        size = image.size
        image_format = image.format
    return calls, size, image_format


class PredictionMetricLayoutTests(unittest.TestCase):
    def assert_grouped_metrics(self, calls, *, rate="47.4%", sample="舊樣本 38｜中等可信"):
        def single(text):
            matches = [call for call in calls if call["text"] == text]
            self.assertEqual(len(matches), 1, f"Expected one draw of {text!r}")
            return matches[0]

        score = single("80 分")
        win_rate = single(f"舊制技術回測 {rate}")
        evidence = single(sample)
        stock = next(call for call in calls if call["text"].startswith("2330 "))
        analysis = next(call for call in calls if call["text"].startswith("解析｜"))
        current_samples = single("新制 全2/訓1/驗1")
        financial = single("現價 / 漲跌")

        # All three metrics share a line between the name and its analysis.
        metric_y = [call["xy"][1] for call in (score, win_rate, evidence)]
        self.assertLessEqual(max(metric_y) - min(metric_y), 4)
        self.assertGreater(min(metric_y), stock["bbox"][3])
        self.assertLess(max(call["bbox"][3] for call in (score, win_rate, evidence)), analysis["xy"][1])
        self.assertLess(score["xy"][0], win_rate["xy"][0])
        self.assertLess(win_rate["xy"][0], evidence["xy"][0])
        self.assertLessEqual(score["bbox"][2], win_rate["bbox"][0])
        self.assertLessEqual(win_rate["bbox"][2], evidence["bbox"][0])
        self.assertLess(evidence["bbox"][2], 600, "Evidence must end before the candle chart")

        self.assertLess(analysis["xy"][1], current_samples["xy"][1])
        self.assertLess(current_samples["bbox"][3], financial["xy"][1])
        self.assertFalse(any(call["text"] in ("舊制技術回測", rate) for call in calls))
        self.assertFalse(any("99.0%" in call["text"] for call in calls))
        for label in ("建議買入區間", "建議零股", "估計停損淨損", "風險停損", "策略目標"):
            self.assertEqual(single(label)["xy"][1], financial["xy"][1])

    def test_prediction_groups_score_rate_and_credibility_without_overlapping(self):
        records = [prediction_record()]
        before = deepcopy(records)
        calls, size, image_format = render_with_text_calls(records)
        self.assert_grouped_metrics(calls)
        self.assertEqual((image_format, size), ("PNG", (1080, 1800)))
        self.assertEqual(records, before)

    def test_comparison_keeps_grouping_for_every_strategy_badge(self):
        for versions, label in (
            (["new"], "新制"),
            (["legacy"], "舊制"),
            (["new", "legacy"], "新制・舊制"),
        ):
            with self.subTest(versions=versions):
                calls, size, image_format = render_with_text_calls([
                    prediction_record(Execution_Versions=versions, Execution_Version_Label=label),
                ], comparison=True)
                self.assert_grouped_metrics(calls)
                badge = next(call for call in calls if call["text"] == label)
                score = next(call for call in calls if call["text"] == "80 分")
                self.assertLess(badge["bbox"][3], score["xy"][1])
                self.assertEqual(image_format, "PNG")
                self.assertEqual(size[0], 1080)

    def test_missing_legacy_rate_remains_unavailable_in_grouped_strip(self):
        for comparison in (False, True):
            with self.subTest(comparison=comparison):
                calls, _, _ = render_with_text_calls([
                    prediction_record(Legacy_Backtest=None, Execution_Versions=["new"]),
                ], comparison=comparison)
                self.assert_grouped_metrics(calls, rate="--", sample="舊制待回補｜資料未提供")

    def test_twenty_comparison_cards_keep_all_metrics_and_financial_rows_inside_png(self):
        records = [
            prediction_record(str(1000 + index), Execution_Versions=["new"], Execution_Version_Label="新制")
            for index in range(20)
        ]
        calls, size, image_format = render_with_text_calls(records, comparison=True)
        self.assertEqual(image_format, "PNG")
        self.assertEqual(size[0], 1080)
        self.assertGreater(size[1], 3500)
        for text in ("80 分", "舊制技術回測 47.4%", "舊樣本 38｜中等可信", "新制 全2/訓1/驗1", "策略目標"):
            self.assertEqual(sum(call["text"] == text for call in calls), 20, text)
        names = [call for call in calls if any(call["text"].startswith(f"{1000 + index} ") for index in range(20))]
        self.assertEqual(len(names), 20)
        for call in calls:
            self.assertGreaterEqual(call["bbox"][0], 0, call["text"])
            self.assertGreaterEqual(call["bbox"][1], 0, call["text"])
            self.assertLessEqual(call["bbox"][2], size[0], call["text"])
            self.assertLessEqual(call["bbox"][3], size[1], call["text"])


if __name__ == "__main__":
    unittest.main()
