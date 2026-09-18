import unittest
from unittest.mock import patch

from data_providers import (
    _financial_period,
    _parse_financial_quality_rows,
    clear_provider_cache,
    fetch_financial_quality,
)


class FinancialQualityProviderTests(unittest.TestCase):
    def setUp(self):
        clear_provider_cache()

    def test_period_accepts_roc_and_western_years(self):
        self.assertEqual(_financial_period({"年度": "115", "季別": "2"}), "2026-Q2")
        self.assertEqual(_financial_period({"Year": "2025", "Season": "Q4"}), "2025-Q4")
        self.assertEqual(_financial_period({"年度": "bad", "季別": "2"}), "")

    def test_metrics_keep_missing_values_and_calculate_reported_ratios(self):
        result = _parse_financial_quality_rows(
            {
                "營業收入": "1,000",
                "營業毛利（毛損）淨額": "300",
                "營業利益（損失）": "100",
                "本期淨利（淨損）": "",
                "基本每股盈餘（元）": "1.25",
            },
            {"流動資產": "600", "資產總計": "2,000", "流動負債": "400", "負債總計": "1,200"},
            period="2026-Q2",
            source="TWSE OpenAPI (current snapshot)",
        )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["gross_margin"], 30.0)
        self.assertEqual(result["operating_margin"], 10.0)
        self.assertIsNone(result["net_income"])
        self.assertIsNone(result["net_margin"])
        self.assertEqual(result["debt_ratio"], 60.0)
        self.assertEqual(result["current_ratio"], 150.0)

    @patch("data_providers._financial_rows")
    def test_fetches_twse_mixed_field_names_and_uses_latest_common_period(self, rows):
        def payload(market, statement):
            if market == "listed" and statement == "income":
                return [{
                    "公司代號": "2330", "年度": "115", "季別": "2", "營業收入": "1000",
                    "營業毛利（毛損）淨額": "500", "營業利益（損失）": "400",
                    "本期淨利（淨損）": "350", "基本每股盈餘（元）": "5.5",
                }]
            if market == "listed" and statement == "balance":
                return [{
                    "SecuritiesCompanyCode": "2330", "Year": "115", "Season": "2",
                    "流動資產": "800", "資產總計": "2000", "流動負債": "400", "負債總計": "800",
                }]
            return []

        rows.side_effect = payload
        result = fetch_financial_quality("2330.TW")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["period"], "2026-Q2")
        self.assertEqual(result["source"], "TWSE OpenAPI (current snapshot)")
        self.assertEqual(result["as_of_period"], "2026-Q2")
        self.assertEqual(result["period_type"], "fiscal_quarter")
        self.assertEqual(result["snapshot_type"], "current_published")
        self.assertEqual(result["freshness"], "unknown")
        self.assertEqual(result["net_margin"], 35.0)
        self.assertEqual(result["debt_ratio"], 40.0)
        self.assertEqual(result["risk_level"], "low")
        self.assertEqual(result["risk_flags"], [])

    @patch("data_providers._financial_rows")
    def test_fetches_tpex_aliases_and_flags_financial_risk(self, rows):
        def payload(market, statement):
            if market == "listed":
                return []
            if statement == "income":
                return [{
                    "SecuritiesCompanyCode": "6488", "Year": "115", "Season": "2", "營業收入": "1000",
                    "營業毛利（毛損）": "200", "營業利益（損失）": "-10",
                    "本期淨利（淨損）": "-20", "基本每股盈餘（元）": "-0.2",
                }]
            return [{
                "SecuritiesCompanyCode": "6488", "年度": "115", "季別": "2",
                "流動資產": "90", "資產總計": "1000", "流動負債": "100", "負債總計": "750",
            }]

        rows.side_effect = payload
        result = fetch_financial_quality("6488")
        self.assertEqual(result["source"], "TPEx OpenAPI (current snapshot)")
        self.assertEqual(result["risk_level"], "high")
        self.assertIn("negative_operating_margin", result["risk_flags"])
        self.assertIn("negative_net_margin", result["risk_flags"])
        self.assertIn("high_debt_ratio", result["risk_flags"])
        self.assertIn("low_current_ratio", result["risk_flags"])

    @patch("data_providers._financial_rows")
    def test_mismatched_periods_are_not_combined(self, rows):
        def payload(market, statement):
            if market == "otc":
                return []
            if statement == "income":
                return [{"公司代號": "2330", "年度": "115", "季別": "2", "營業收入": "1000"}]
            return [{"公司代號": "2330", "年度": "115", "季別": "1", "負債總計": "10", "資產總計": "20"}]

        rows.side_effect = payload
        result = fetch_financial_quality("2330")
        self.assertEqual(result["period"], "2026-Q2")
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["debt_ratio"])

    @patch("data_providers._financial_rows", return_value=[])
    def test_empty_official_payload_does_not_fabricate_zeroes(self, rows):
        result = fetch_financial_quality("9999")
        self.assertEqual(rows.call_count, 4)
        self.assertEqual(result["status"], "empty")
        self.assertIsNone(result["revenue"])
        self.assertIsNone(result["current_ratio"])
        self.assertEqual(result["risk_level"], "unknown")


if __name__ == "__main__":
    unittest.main()
