import unittest

from top10_tracker import update_positions_with_snapshots


class ConfidenceSnapshotTests(unittest.TestCase):
    def test_signal_snapshot_freezes_data_model_fundamental_and_chip_evidence(self):
        ranking = [{
            "代號": "3498",
            "名稱": "陽程",
            "Rank": 4,
            "Score": 74,
            "開盤價": 50,
            "最高價": 52,
            "最低價": 49,
            "收盤價": 51,
            "Entry_Low": 49.5,
            "Entry_High": 51.5,
            "Entry_Stop": 47,
            "Entry_Target": 57,
            "Confidence": 88,
            "Data_Completeness": 88,
            "Model_Confidence": 61.5,
            "Model_Confidence_Label": "中等可信",
            "EPS": None,
            "EPS_Period": "missing",
            "MoM": -16.31,
            "YoY": 4.2,
            "Revenue_Period": "2026-08",
            "Revenue_Source": "FinMind",
            "Whale_Net": -772,
            "Whale_Net_Days": 3,
            "Institutional_Days": 3,
            "Institutional_Status": "ok",
            "Institutional_Source": "TWSE T86",
            "Institutional_Rows": [{
                "date": "2026-09-14",
                "foreign": -500,
                "trust": -200,
                "dealer": -72,
                "total": -772,
                "source": "TWSE T86",
            }],
        }]

        positions, daily = update_positions_with_snapshots([], ranking, {}, "2026-09-15")

        snapshot = positions[0]["signal_snapshot"]
        self.assertEqual(snapshot["Confidence"], 88)
        self.assertEqual(snapshot["Data_Completeness"], 88)
        self.assertEqual(snapshot["Model_Confidence"], 61.5)
        self.assertEqual(snapshot["Model_Confidence_Label"], "中等可信")
        self.assertIsNone(snapshot["EPS"])
        self.assertEqual(snapshot["MoM"], -16.31)
        self.assertEqual(snapshot["YoY"], 4.2)
        self.assertEqual(snapshot["Whale_Net"], -772)
        self.assertEqual(snapshot["Institutional_Rows"][0]["foreign"], -500)
        self.assertEqual(daily[0]["signal_snapshot"], snapshot)


if __name__ == "__main__":
    unittest.main()
