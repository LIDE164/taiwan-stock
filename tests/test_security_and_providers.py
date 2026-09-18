import ssl
import unittest
from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pandas as pd

from app_security import (
    build_stock_url,
    escape_html,
    normalize_ticker,
    resolve_stock_identifier,
    safe_iso_date,
    scoped_document_name,
)
from data_providers import (
    _finmind_rows,
    _normalize_finmind_institutional_rows,
    _parse_official_revenue_row,
    _parse_tpex_institutional_payload,
    _parse_twse_institutional_payload,
    clear_provider_cache,
    fetch_institutional_rows,
    fetch_revenue_growth,
)
from market_http import RETRYABLE_STATUS_CODES, _build_session, call_with_backoff
from ui_components import generate_cards_html


class SecurityTests(unittest.TestCase):
    def test_ticker_and_date_validation_reject_markup(self):
        self.assertEqual(normalize_ticker("2330.TW"), "2330")
        self.assertEqual(normalize_ticker("<script>"), "")
        self.assertEqual(safe_iso_date("2026-08-17"), "2026-08-17")
        self.assertEqual(safe_iso_date("2026-99-99"), "")

    def test_urls_are_encoded_and_html_is_escaped(self):
        self.assertEqual(build_stock_url("2330", mode="intraday"), "/?stock=2330&mode=intraday")
        self.assertEqual(escape_html("<b>x</b>"), "&lt;b&gt;x&lt;/b&gt;")

    def test_scoped_documents_are_stable_and_do_not_expose_email(self):
        first = scoped_document_name("orders", {"email": "me@example.com"}, "")
        second = scoped_document_name("orders", {"email": "me@example.com"}, "")
        self.assertEqual(first, second)
        self.assertNotIn("example", first)

    def test_stock_name_links_resolve_only_unique_matches(self):
        names = {"2330": "台積電", "1111": "甲科技", "2222": "乙科技"}
        self.assertEqual(resolve_stock_identifier("2330", names), ("2330", "ok"))
        self.assertEqual(resolve_stock_identifier("台積電", names), ("2330", "ok"))
        self.assertEqual(resolve_stock_identifier("科技", names), ("", "ambiguous"))
        self.assertEqual(resolve_stock_identifier("不存在", names), ("", "not_found"))

    def test_card_renderer_escapes_cloud_values(self):
        frame = pd.DataFrame([{
            "代號": "2330", "名稱": "<img src=x onerror=alert(1)>", "Score": 60,
            "收盤價": 100, "漲跌": 1, "漲跌幅": 1, "Rank_Diff": "NEW",
            "Feature": "<script>alert(1)</script>",
        }])
        rendered = generate_cards_html(frame, safe_num=lambda value, default=0: float(value or default))
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<img", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_card_renderer_does_not_show_missing_institutional_data_as_zero(self):
        frame = pd.DataFrame([{
            "代號": "2330", "名稱": "台積電", "Score": 60,
            "收盤價": 100, "漲跌": 1, "漲跌幅": 1,
            "Whale_Net": None, "Whale_Net_Days": 0,
        }])
        rendered = generate_cards_html(frame, safe_num=lambda value, default=0: float(value or default))
        self.assertIn("法人資料", rendered)
        self.assertIn("--", rendered)
        self.assertNotIn("法人10日", rendered)

    def test_card_renderer_shows_postclose_to_intraday_score_change(self):
        frame = pd.DataFrame([{
            "代號": "2330", "名稱": "台積電", "Score": 78,
            "Original_Score": 72, "Score_Diff": 6,
            "Score_Mode_Raw": "realtime", "Score_Mode": "盤中參考分數",
            "Score_Source": "盤中重新評分", "收盤價": 100,
            "漲跌": 1, "漲跌幅": 1,
        }])
        rendered = generate_cards_html(
            frame,
            is_intraday=True,
            safe_num=lambda value, default=0: float(value or default),
        )
        self.assertIn("盤後 72 → 盤中 78（+6）", rendered)

    def test_card_renderer_shows_entry_status_and_levels(self):
        frame = pd.DataFrame([{
            "代號": "2330", "名稱": "台積電", "Score": 78,
            "收盤價": 100, "漲跌": 1, "漲跌幅": 1,
            "Entry_Status": "現在可執行", "Entry_Low": 99,
            "Entry_High": 101, "Entry_Stop": 96, "Entry_Target": 106,
            "Entry_RRR": 1.5, "No_Chase_Price": 103.5,
            "Entry_Reason": "<script>不可直接顯示</script>",
        }])
        rendered = generate_cards_html(frame, safe_num=lambda value, default=0: float(value or default))
        self.assertIn("現在可執行", rendered)
        self.assertIn("觀察買入區間", rendered)
        self.assertIn("99–101", rendered)
        self.assertIn("&lt;script&gt;不可直接顯示&lt;/script&gt;", rendered)
        self.assertNotIn("<script>", rendered)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        clear_provider_cache()

    def _response(self, payload):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = payload
        return response

    @patch("data_providers.http_get")
    def test_revenue_growth_is_calculated_from_point_rows(self, get):
        rows = [{"date": f"2025-{month:02d}-01", "revenue": 100} for month in range(1, 13)]
        rows += [{"date": "2026-01-01", "revenue": 120}, {"date": "2026-02-01", "revenue": 132}]
        get.return_value = self._response({"msg": "success", "data": rows})
        result = fetch_revenue_growth(
            "2330", "token", now=datetime(2026, 3, 5, 12, tzinfo=UTC)
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["mom"], 10.0)
        self.assertEqual(result["freshness"], "fresh")

    @patch("data_providers.http_get")
    def test_missing_revenue_is_not_represented_as_zero_growth(self, get):
        get.return_value = self._response({"msg": "success", "data": []})
        result = fetch_revenue_growth("2330", "token")
        self.assertEqual(result["status"], "empty")
        self.assertIsNone(result["mom"])
        self.assertIsNone(result["yoy"])

    @patch("data_providers.http_get")
    def test_institutional_rows_are_normalized(self, get):
        get.return_value = self._response({"msg": "success", "data": [
            {"date": "2026-08-14", "name": "Foreign_Investor", "buy": 5000, "sell": 1000},
            {"date": "2026-08-14", "name": "Investment_Trust", "buy": 3000, "sell": 1000},
            {"date": "2026-08-14", "name": "Dealer_self", "buy": 1000, "sell": 1000},
            {"date": "2026-08-14", "name": "Dealer_Hedging", "buy": 0, "sell": 0},
        ]})
        rows, status = fetch_institutional_rows(
            "2330", "token", now=datetime(2026, 8, 14, 21, tzinfo=UTC)
        )
        self.assertEqual(status, "partial")
        self.assertEqual(rows[0]["total"], 6)
        self.assertFalse(rows[0]["is_stale"])
        self.assertEqual(rows[0]["latest_date"], "2026-08-14")

    @patch("data_providers.http_get")
    def test_finmind_public_query_does_not_require_token(self, get):
        get.return_value = self._response({"msg": "success", "data": [{"date": "2026-08-19"}]})
        rows, status = _finmind_rows("dataset", "2330", "2026-08-01", "")
        self.assertEqual(status, "ok")
        self.assertEqual(len(rows), 1)
        self.assertNotIn("token", get.call_args.kwargs["params"])

    @patch("data_providers._fetch_official_institutional_rows")
    @patch("data_providers._finmind_rows")
    def test_missing_finmind_token_skips_the_redundant_finmind_request(self, finmind, official):
        official.return_value = ([{"date": "2026-08-19", "total": 10}], "partial")
        rows, status = fetch_institutional_rows("2330", "")
        finmind.assert_not_called()
        official.assert_called_once()
        self.assertEqual(rows[0]["total"], 10)
        self.assertEqual(status, "partial")

    def test_official_revenue_keeps_period_source_and_real_percentages(self):
        result = _parse_official_revenue_row({
            "資料年月": "11507",
            "營業收入-上月比較增減(%)": "12.345",
            "營業收入-去年同月增減(%)": "-4.567",
        }, "TWSE OpenAPI")
        self.assertEqual(result["mom"], 12.35)
        self.assertEqual(result["yoy"], -4.57)
        self.assertEqual(result["period"], "2026-07")
        self.assertEqual(result["as_of_period"], "2026-07")
        self.assertEqual(result["period_type"], "calendar_month")
        self.assertEqual(result["freshness"], "unknown")
        self.assertEqual(result["source"], "TWSE OpenAPI")

    def test_twse_and_tpex_chip_rows_use_reported_share_fields(self):
        row_date = datetime(2026, 8, 19, tzinfo=UTC)
        twse = _parse_twse_institutional_payload({
            "stat": "OK",
            "fields": [
                "證券代號", "外陸資買賣超股數(不含外資自營商)", "外資自營商買賣超股數",
                "投信買賣超股數", "自營商買賣超股數", "三大法人買賣超股數",
            ],
            "data": [["2330", "5,000", "1,000", "-2,000", "3,000", "6,000"]],
        }, "2330", row_date)
        self.assertEqual(twse["foreign"], 5)
        self.assertEqual(twse["foreign_ex_dealer"], 5)
        self.assertEqual(twse["foreign_dealer"], 1)
        self.assertEqual(twse["foreign_semantics"], "excludes_foreign_dealer")
        self.assertEqual(twse["total"], 6)
        self.assertEqual(twse["total_validation"], "matched")

        tpex_values = ["0"] * 24
        tpex_values[0] = "6488"
        tpex_values[4], tpex_values[7], tpex_values[10] = "4,000", "1,000", "5,000"
        tpex_values[13], tpex_values[22], tpex_values[23] = "2,000", "-1,000", "5,000"
        tpex = _parse_tpex_institutional_payload({
            "stat": "ok",
            "tables": [{"data": [tpex_values]}],
        }, "6488", row_date)
        self.assertEqual(tpex["foreign"], 4)
        self.assertEqual(tpex["trust"], 2)
        self.assertEqual(tpex["dealer"], -1)
        self.assertEqual(tpex["total"], 5)

    def test_tpex_prefers_named_fields_over_offsets(self):
        row_date = datetime(2026, 8, 19, tzinfo=UTC)
        result = _parse_tpex_institutional_payload({
            "stat": "ok",
            "tables": [{
                "fields": [
                    "代號", "投信買賣超股數", "三大法人買賣超股數",
                    "自營商買賣超股數", "外資自營商買賣超股數",
                    "外資及陸資(不含外資自營商)買賣超股數",
                ],
                "data": [["6488", "2,000", "5,000", "-1,000", "1,000", "4,000"]],
            }],
        }, "6488", row_date)
        self.assertEqual(result["foreign"], 4)
        self.assertEqual(result["foreign_dealer"], 1)
        self.assertEqual(result["total"], 5)

    def test_official_chip_missing_or_inconsistent_numbers_are_rejected(self):
        row_date = datetime(2026, 8, 19, tzinfo=UTC)
        base = {
            "stat": "OK",
            "fields": [
                "證券代號", "外陸資買賣超股數(不含外資自營商)",
                "投信買賣超股數", "自營商買賣超股數", "三大法人買賣超股數",
            ],
        }
        missing = dict(base, data=[["2330", "", "2,000", "3,000", "5,000"]])
        mismatch = dict(base, data=[["2330", "1,000", "2,000", "3,000", "99,000"]])
        self.assertIsNone(_parse_twse_institutional_payload(missing, "2330", row_date))
        self.assertIsNone(_parse_twse_institutional_payload(mismatch, "2330", row_date))

    def test_finmind_excludes_foreign_dealer_from_foreign_and_total(self):
        rows, status, stale = _normalize_finmind_institutional_rows([
            {"date": "2026-08-19", "name": "Foreign_Investor", "buy": 5000, "sell": 1000},
            {"date": "2026-08-19", "name": "Foreign_Dealer_Self", "buy": 2000, "sell": 1000},
            {"date": "2026-08-19", "name": "Investment_Trust", "buy": 3000, "sell": 1000},
            {"date": "2026-08-19", "name": "Dealer_self", "buy": 0, "sell": 1000},
            {"date": "2026-08-19", "name": "Dealer_Hedging", "buy": 0, "sell": 0},
        ], now=datetime(2026, 8, 19, 21, tzinfo=UTC))
        self.assertEqual(status, "partial")
        self.assertFalse(stale)
        self.assertEqual(rows[0]["foreign"], 4)
        self.assertEqual(rows[0]["foreign_dealer"], 1)
        self.assertEqual(rows[0]["dealer"], -1)
        self.assertEqual(rows[0]["total"], 5)

    def test_finmind_missing_required_category_is_not_zero_filled(self):
        rows, status, _ = _normalize_finmind_institutional_rows([
            {"date": "2026-08-19", "name": "Foreign_Investor", "buy": 1000, "sell": 0},
            {"date": "2026-08-19", "name": "Investment_Trust", "buy": 0, "sell": 0},
        ], now=datetime(2026, 8, 19, 21, tzinfo=UTC))
        self.assertEqual(rows, [])
        self.assertEqual(status, "partial")

    def test_finmind_requires_both_new_dealer_components(self):
        rows, status, _ = _normalize_finmind_institutional_rows([
            {"date": "2026-08-19", "name": "Foreign_Investor", "buy": 300, "sell": 0},
            {"date": "2026-08-19", "name": "Investment_Trust", "buy": 100, "sell": 0},
            {"date": "2026-08-19", "name": "Dealer_self", "buy": 200, "sell": 0},
        ], now=datetime(2026, 8, 19, 21, tzinfo=UTC))
        self.assertEqual(rows, [])
        self.assertEqual(status, "partial")

    def test_finmind_accepts_unambiguous_legacy_combined_dealer(self):
        rows, status, stale = _normalize_finmind_institutional_rows([
            {"date": "2026-08-19", "name": "Foreign_Investor", "buy": 300, "sell": 0},
            {"date": "2026-08-19", "name": "Investment_Trust", "buy": 100, "sell": 0},
            {"date": "2026-08-19", "name": "Dealer", "buy": 200, "sell": 0},
        ], now=datetime(2026, 8, 19, 21, tzinfo=UTC))
        self.assertEqual(status, "partial")
        self.assertFalse(stale)
        self.assertEqual(rows[0]["foreign"], 0.3)
        self.assertEqual(rows[0]["trust"], 0.1)
        self.assertEqual(rows[0]["dealer"], 0.2)
        self.assertEqual(rows[0]["total"], 0.6)

    def test_finmind_uses_legacy_combined_dealer_when_split_rows_are_zero(self):
        rows, status, _ = _normalize_finmind_institutional_rows([
            {"date": "2026-08-19", "name": "Foreign_Investor", "buy": 1000, "sell": 0},
            {"date": "2026-08-19", "name": "Investment_Trust", "buy": 0, "sell": 0},
            {"date": "2026-08-19", "name": "Dealer", "buy": 2000, "sell": 0},
            {"date": "2026-08-19", "name": "Dealer_self", "buy": 0, "sell": 0},
            {"date": "2026-08-19", "name": "Dealer_Hedging", "buy": 0, "sell": 0},
        ], now=datetime(2026, 8, 19, 21, tzinfo=UTC))
        self.assertEqual(status, "partial")
        self.assertEqual(rows[0]["dealer"], 2)
        self.assertEqual(rows[0]["total"], 3)

    def test_official_sub_lot_flows_are_not_rounded_to_fake_zero(self):
        row_date = datetime(2026, 8, 19, tzinfo=UTC)
        row = _parse_twse_institutional_payload({
            "stat": "OK",
            "fields": [
                "證券代號", "外陸資買賣超股數(不含外資自營商)",
                "投信買賣超股數", "自營商買賣超股數", "三大法人買賣超股數",
            ],
            "data": [["2330", "300", "-100", "50", "250"]],
        }, "2330", row_date)
        self.assertEqual(row["foreign"], 0.3)
        self.assertEqual(row["trust"], -0.1)
        self.assertEqual(row["dealer"], 0.05)
        self.assertEqual(row["total"], 0.25)
        self.assertAlmostEqual(
            row["total"], row["foreign"] + row["trust"] + row["dealer"]
        )

    @patch("data_providers._fetch_official_revenue_growth")
    @patch("data_providers._finmind_rows")
    def test_stale_finmind_revenue_falls_back_to_official(self, finmind, official):
        finmind.return_value = ([
            {"date": "2025-07-01", "revenue": 100},
            {"date": "2026-06-01", "revenue": 110},
            {"date": "2026-07-01", "revenue": 120},
        ], "ok")
        official.return_value = {
            "mom": 5.0,
            "yoy": 8.0,
            "period": "2026-08",
            "as_of_period": "2026-08",
            "period_type": "calendar_month",
            "freshness": "unknown",
            "source": "TWSE OpenAPI",
            "status": "ok",
        }
        result = fetch_revenue_growth(
            "2330", "token", now=datetime(2026, 9, 18, 12, tzinfo=UTC)
        )
        official.assert_called_once_with("2330")
        self.assertEqual(result["source"], "TWSE OpenAPI")
        self.assertEqual(result["period"], "2026-08")
        self.assertEqual(result["freshness"], "fresh")
        self.assertEqual(result["status"], "ok")

    @patch("data_providers._fetch_official_institutional_rows")
    @patch("data_providers._finmind_rows")
    def test_stale_finmind_rows_fall_back_to_official(self, finmind, official):
        finmind.return_value = ([
            {"date": "2026-08-18", "name": "Foreign_Investor", "buy": 1000, "sell": 0},
            {"date": "2026-08-18", "name": "Investment_Trust", "buy": 0, "sell": 0},
            {"date": "2026-08-18", "name": "Dealer_self", "buy": 0, "sell": 0},
            {"date": "2026-08-18", "name": "Dealer_Hedging", "buy": 0, "sell": 0},
        ], "ok")
        official.return_value = ([{
            "date": "2026-08-19", "foreign": 2, "trust": 0, "dealer": 0,
            "total": 2, "source": "TWSE T86", "is_stale": False,
        }], "partial")
        rows, status = fetch_institutional_rows(
            "2330", "token", now=datetime(2026, 8, 19, 21, tzinfo=UTC)
        )
        official.assert_called_once()
        self.assertEqual(rows[0]["source"], "TWSE T86")
        self.assertEqual(status, "partial")

    def test_http_session_retries_rate_limits_and_server_errors(self):
        session = _build_session()
        retry = session.get_adapter("https://").max_retries
        self.assertEqual(tuple(retry.status_forcelist), RETRYABLE_STATUS_CODES)
        self.assertTrue(retry.respect_retry_after_header)
        tpex_adapter = session.get_adapter("https://www.tpex.org.tw/openapi/v1/test")
        self.assertEqual(tpex_adapter.ssl_context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(tpex_adapter.ssl_context.check_hostname)
        strict_flag = getattr(ssl, "VERIFY_X509_STRICT", 0)
        if strict_flag:
            self.assertFalse(tpex_adapter.ssl_context.verify_flags & strict_flag)

    @patch("market_http.time.sleep")
    def test_non_requests_provider_uses_bounded_backoff(self, sleep):
        operation = Mock(side_effect=[RuntimeError("429"), "ok"])
        self.assertEqual(call_with_backoff(operation, attempts=2, backoff_factor=0.1), "ok")
        sleep.assert_called_once_with(0.1)


if __name__ == "__main__":
    unittest.main()
