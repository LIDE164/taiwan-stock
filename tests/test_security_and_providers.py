import unittest
from unittest.mock import Mock, patch
import ssl
from datetime import datetime
import pandas as pd

from app_security import build_stock_url, escape_html, normalize_ticker, safe_iso_date, scoped_document_name
from data_providers import (
    _parse_official_revenue_row,
    _parse_tpex_institutional_payload,
    _parse_twse_institutional_payload,
    _finmind_rows,
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
        result = fetch_revenue_growth("2330", "token")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["mom"], 10.0)

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
        ]})
        rows, status = fetch_institutional_rows("2330", "token")
        self.assertEqual(status, "ok")
        self.assertEqual(rows[0]["total"], 6)

    @patch("data_providers.http_get")
    def test_finmind_public_query_does_not_require_token(self, get):
        get.return_value = self._response({"msg": "success", "data": [{"date": "2026-08-19"}]})
        rows, status = _finmind_rows("dataset", "2330", "2026-08-01", "")
        self.assertEqual(status, "ok")
        self.assertEqual(len(rows), 1)
        self.assertNotIn("token", get.call_args.kwargs["params"])

    def test_official_revenue_keeps_period_source_and_real_percentages(self):
        result = _parse_official_revenue_row({
            "資料年月": "11507",
            "營業收入-上月比較增減(%)": "12.345",
            "營業收入-去年同月增減(%)": "-4.567",
        }, "TWSE OpenAPI")
        self.assertEqual(result["mom"], 12.35)
        self.assertEqual(result["yoy"], -4.57)
        self.assertEqual(result["period"], "2026-07")
        self.assertEqual(result["source"], "TWSE OpenAPI")

    def test_twse_and_tpex_chip_rows_use_reported_share_fields(self):
        row_date = datetime(2026, 8, 19)
        twse = _parse_twse_institutional_payload({
            "stat": "OK",
            "fields": [
                "證券代號", "外陸資買賣超股數(不含外資自營商)", "外資自營商買賣超股數",
                "投信買賣超股數", "自營商買賣超股數",
            ],
            "data": [["2330", "5,000", "1,000", "-2,000", "3,000"]],
        }, "2330", row_date)
        self.assertEqual(twse["foreign"], 6)
        self.assertEqual(twse["total"], 7)

        tpex_values = ["0"] * 24
        tpex_values[0] = "6488"
        tpex_values[10], tpex_values[13], tpex_values[22] = "4,000", "2,000", "-1,000"
        tpex = _parse_tpex_institutional_payload({
            "stat": "ok",
            "tables": [{"data": [tpex_values]}],
        }, "6488", row_date)
        self.assertEqual(tpex["foreign"], 4)
        self.assertEqual(tpex["trust"], 2)
        self.assertEqual(tpex["dealer"], -1)
        self.assertEqual(tpex["total"], 5)

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
