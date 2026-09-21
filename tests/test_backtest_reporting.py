import unittest

from backtest_reporting import (
    backtest_diagnostic_rows, backtest_record_fields, evidence_observation,
    reconcile_intraday_evidence, replace_backtest_snapshot, sample_breakdown, summarize_scan_evidence,
)


class BacktestReportingTests(unittest.TestCase):
    def test_legacy_validation_is_not_subtracted_twice(self):
        result = sample_breakdown({"Backtest_Samples": 30, "Validation_Samples": 9})
        self.assertIsNone(result["training"])
        self.assertFalse(result["known_split"])
        self.assertIn("原制 30", result["sample_text"])

    def test_explicit_disjoint_samples_show_all_three_counts(self):
        result = sample_breakdown({
            "Backtest_Samples": 1, "Backtest_Overall_Samples": 2, "Validation_Samples": 1,
        })
        self.assertEqual((result["overall"], result["training"], result["validation"]), (2, 1, 1))
        self.assertEqual(result["compact_text"], "全2/訓1/驗1")

    def test_unknown_and_inconsistent_counts_are_not_fabricated(self):
        result = sample_breakdown({"Backtest_Samples": float("nan")})
        self.assertIsNone(result["overall"])
        self.assertIsNone(result["training"])
        result = sample_breakdown({'Backtest_Samples': 1, 'Backtest_Training_Samples': 30,
                                   'Backtest_Overall_Samples': 2, 'Validation_Samples': 1})
        self.assertFalse(result['known_split'])
        self.assertFalse(result['consistent'])
        self.assertIn('分母不一致', result['sample_text'])
        result = sample_breakdown({"Backtest_Samples": 5, "Backtest_Overall_Samples": 5, "Validation_Samples": 3})
        self.assertIsNone(result["training"])

    @staticmethod
    def candidate(**updates):
        row = {
            "Score": 80, "收盤價": 100, "最高價": 101, "20MA": 100, "ATR": 4,
            "RSI": 55, "BIAS": 0, "BB_UP": 120, "Est_Vol_Ratio": 1.3,
            "Volume_Confirmed": True, "Confidence": 100, "Entry_Pattern": "回測支撐型",
            "Backtest_Samples": 1, "Validation_Samples": 0, "WinRate": 56.1,
            "Validation_WinRate": 0, "Entry_Status": "等待觸發",
        }
        row.update(updates)
        return row

    def test_observation_never_becomes_execution_approved(self):
        original = self.candidate()
        result = evidence_observation(original)
        self.assertIsNotNone(result)
        self.assertFalse(result["Entry_Ready"])
        self.assertEqual(result["Entry_Status_Group"], "validation")
        self.assertIn("15", result["Entry_Reason"])
        self.assertEqual(original["Entry_Status"], "等待觸發")
        self.assertNotIn("Entry_Ready", original)

    def test_observation_keeps_market_data_and_price_risk_vetoes(self):
        for updates in ({"Market_Regime": "空頭"}, {"Critical_Data_Ready": False}, {"RSI": 80}, {"收盤價": 115}):
            with self.subTest(updates=updates):
                self.assertIsNone(evidence_observation(self.candidate(**updates)))

    def test_summary_distinguishes_unknown_and_zero_and_observation(self):
        result = summarize_scan_evidence([
            self.candidate(), self.candidate(Backtest_Samples=0),
            self.candidate(Backtest_Samples=None, RSI=80),
        ])
        self.assertEqual((result["zero_samples"], result["one_sample"], result["missing_samples"]), (1, 1, 1))
        self.assertEqual(result["ready_count"], 0)
        self.assertEqual(result["sample_gate_pass"], 0)
        self.assertEqual(len(result["observations"]), 2)

    def test_unknown_score_or_stale_intraday_quote_never_becomes_observation(self):
        for score in (None, float('nan'), float('inf'), 'bad', 64):
            with self.subTest(score=score):
                self.assertIsNone(evidence_observation(self.candidate(Score=score)))
        self.assertIsNone(evidence_observation(self.candidate(), intraday=True))
        self.assertIsNone(evidence_observation(self.candidate(Intraday_Quote_Status='delayed'), intraday=True))
        self.assertIsNotNone(evidence_observation(self.candidate(Intraday_Quote_Status='realtime'), intraday=True))

    def test_metadata_mapping_includes_execution_evidence(self):
        fields = backtest_record_fields({
            'backtest_schema': 'executable_v3', 'closed_signals': 1,
            'training_samples': 1, 'overall_samples': 2, 'validation_samples': 1,
            'net_expectancy_pct': -1.5, 'validation_net_expectancy_pct': -2,
            'validation_wilson_low': 0, 'diagnostics': {'completed': 2},
        })
        self.assertEqual(fields['Backtest_Net_Expectancy'], -1.5)
        self.assertEqual(fields['Validation_Wilson_Low'], 0)
        self.assertEqual(sample_breakdown(fields)['compact_text'], '全2/訓1/驗1')
        self.assertNotIn('WinRate', fields)

    def test_legacy_cache_replacement_cannot_inherit_new_metadata(self):
        record = {'Score': 80, 'WinRate': 55, 'Backtest_Training_Samples': 2,
                  'Backtest_Overall_Samples': 3, 'Backtest_Diagnostics': {'completed': 3},
                  'Backtest_Schema': 'executable_v3', 'Backtest_Net_Expectancy': -3,
                  'Model_Confidence_Label': 'new low', 'Backtest_Max_Drawdown': 10}
        replace_backtest_snapshot(record, {'WinRate': 60, 'Backtest_Samples': 30, 'Validation_Samples': 9})
        self.assertEqual(record['Score'], 80)
        self.assertEqual(record['WinRate'], 60)
        self.assertNotIn('Backtest_Training_Samples', record)
        self.assertNotIn('Backtest_Diagnostics', record)
        self.assertNotIn('Backtest_Net_Expectancy', record)
        self.assertNotIn('Model_Confidence_Label', record)
        self.assertNotIn('Backtest_Max_Drawdown', record)
        self.assertFalse(sample_breakdown(record)['known_split'])

    def test_legacy_inclusive_counts_are_not_counted_as_training_gate_pass(self):
        summary = summarize_scan_evidence([self.candidate(Backtest_Samples=30, Validation_Samples=9)])
        self.assertEqual(summary['sample_gate_pass'], 0)
        summary = summarize_scan_evidence([self.candidate(Backtest_Samples=30, Validation_Samples=9,
                                                        Backtest_Overall_Samples=39)])
        self.assertEqual(summary['sample_gate_pass'], 1)

    def test_live_approval_and_samples_use_the_same_frozen_evidence(self):
        live = self.candidate(Backtest_Samples=30, Validation_Samples=10, WinRate=70,
                              Validation_WinRate=65, Entry_Ready=True, Entry_Status='現在可執行')
        result = reconcile_intraday_evidence(self.candidate(), live)
        self.assertEqual(result['Backtest_Samples'], 1)
        self.assertFalse(result['Entry_Ready'])
        self.assertIn('15', result['Entry_Reason'])

    def test_diagnostics_show_only_recorded_stages(self):
        self.assertEqual(backtest_diagnostic_rows({}), [])
        self.assertEqual(backtest_diagnostic_rows({'Backtest_Diagnostics': 'bad'}), [])
        rows = backtest_diagnostic_rows({'Backtest_Diagnostics': {'filled': 3, 'completed': 1}})
        self.assertEqual(rows, [{'階段': '隔日觸價', '筆數': 3}, {'階段': '有效完成交易（全期）', '筆數': 1}])


if __name__ == "__main__":
    unittest.main()
