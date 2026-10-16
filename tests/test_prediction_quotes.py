from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from prediction_quotes import fetch_market_index_quote, fetch_prediction_quotes, quote_from_chart_result


TPE = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 10, 5, 11, 0, tzinfo=TPE)


def stamp(hour: int, minute: int = 0, day: int = 5) -> int:
    return int(datetime(2026, 10, day, hour, minute, tzinfo=TPE).timestamp())


def result(symbol: str = "2330.TW") -> dict:
    return {
        "meta": {
            "symbol": symbol, "currency": "TWD", "exchangeTimezoneName": "Asia/Taipei",
            "regularMarketTime": stamp(10, 40), "regularMarketPrice": 105,
            "chartPreviousClose": 81, "previousClose": 82,
            "currentTradingPeriod": {"regular": {"start": stamp(9), "end": stamp(13, 30)}},
        },
        "timestamp": [stamp(9, day=2), stamp(9)],
        "indicators": {
            "quote": [{"open": [100, 103], "high": [104, 107], "low": [98, 102], "close": [101, 105]}],
            "adjclose": [{"adjclose": [91, 95]}],
        },
    }


def parse(payload: dict, **kwargs) -> dict:
    return quote_from_chart_result(payload, symbol="2330.TW", now_tpe=kwargs.pop("now_tpe", NOW), **kwargs)


def response(payload: dict) -> Mock:
    mocked = Mock()
    mocked.json.return_value = {"chart": {"result": [payload], "error": None}}
    return mocked


class PredictionQuoteParsingTests(unittest.TestCase):
    def test_current_quote_is_unadjusted_dated_and_not_final(self):
        payload = result()
        original = deepcopy(payload)
        quote = parse(payload)
        self.assertEqual(quote["status"], "ok")
        self.assertEqual(quote["open"], 103)
        self.assertEqual(quote["price"], 105)
        self.assertEqual(quote["previous_close"], 101)
        self.assertEqual(quote["previous_close_date"], "2026-10-02")
        self.assertEqual(quote["previous_close_basis"], "unadjusted_close")
        self.assertEqual(quote["observed_at"], "2026-10-05T10:40:00+08:00")
        self.assertEqual(quote["date"], "2026-10-05")
        self.assertIn("可能延遲", quote["source"])
        self.assertFalse(quote["final"])
        self.assertEqual(payload, original)

    def test_aware_utc_input_converts_to_taipei(self):
        self.assertEqual(parse(result(), now_tpe=NOW.astimezone(timezone.utc))["status"], "ok")

    def test_naive_clock_rejected(self):
        with self.assertRaises(ValueError):
            parse(result(), now_tpe=NOW.replace(tzinfo=None))
        with self.assertRaises(ValueError):
            fetch_prediction_quotes([], now_tpe=NOW.replace(tzinfo=None))

    def test_no_old_close_substitution(self):
        payload = result()
        payload["meta"]["regularMarketTime"] = stamp(13, 30, day=2)
        quote = parse(payload)
        self.assertEqual(quote["status"], "unavailable")
        self.assertIsNone(quote["open"])
        self.assertIsNone(quote["price"])
        self.assertIsNone(quote["observed_at"])

    def test_symbol_timezone_and_currency_must_match(self):
        for key, value in (("symbol", "2330.TWO"), ("exchangeTimezoneName", "UTC"), ("currency", "USD")):
            payload = result()
            payload["meta"][key] = value
            with self.subTest(key=key):
                self.assertEqual(parse(payload)["status"], "unavailable")

    def test_missing_or_bad_trade_time_rejected(self):
        for timestamp in (None, True, "wrong", math.inf, 1e100, stamp(11, 1), stamp(8, 59)):
            payload = result()
            payload["meta"]["regularMarketTime"] = timestamp
            with self.subTest(timestamp=timestamp):
                self.assertEqual(parse(payload)["status"], "unavailable")

    def test_stale_future_duplicate_or_bad_daily_timestamp_rejected(self):
        for timestamps in (
            [], [None], [stamp(9, day=2)], [stamp(9), stamp(9)], [stamp(9), stamp(9, 1)],
            [stamp(9), stamp(9, day=2)], [stamp(11)], [stamp(9), stamp(9, day=6)],
        ):
            payload = result()
            payload["timestamp"] = timestamps
            with self.subTest(timestamps=timestamps):
                self.assertEqual(parse(payload)["status"], "unavailable")

    def test_no_guessed_prices_when_ohlc_missing_nonfinite_or_invalid(self):
        for key, value in (("open", None), ("open", 0), ("low", 106), ("high", 102), ("close", math.nan)):
            payload = result()
            payload["indicators"]["quote"][0][key][1] = value
            with self.subTest(key=key, value=value):
                quote = parse(payload)
                self.assertEqual(quote["status"], "unavailable")
                self.assertIsNone(quote["price"])

    def test_unequal_field_length_is_unavailable(self):
        payload = result()
        payload["indicators"]["quote"][0]["open"] = [100]
        self.assertEqual(parse(payload)["status"], "unavailable")

    def test_latest_trade_and_daily_close_must_agree(self):
        for latest in (None, False, math.inf, 106, 108, 101):
            payload = result()
            payload["meta"]["regularMarketPrice"] = latest
            with self.subTest(latest=latest):
                self.assertEqual(parse(payload)["status"], "unavailable")

    def test_previous_close_is_optional_and_never_meta_range_start(self):
        payload = result()
        payload["indicators"]["quote"][0]["close"][0] = None
        quote = parse(payload)
        self.assertEqual(quote["status"], "ok")
        self.assertIsNone(quote["previous_close"])
        self.assertIsNone(quote["previous_close_date"])
        for key in ("timestamp",):
            payload[key] = payload[key][1:]
        for key in payload["indicators"]["quote"][0]:
            payload["indicators"]["quote"][0][key] = payload["indicators"]["quote"][0][key][1:]
        self.assertIsNone(parse(payload)["previous_close"])

    def test_missing_previous_session_cannot_use_an_older_close(self):
        payload = result()
        payload["timestamp"][0] = stamp(9, day=1)
        quote = parse(payload)
        self.assertEqual(quote["status"], "ok")
        self.assertIsNone(quote["previous_close"])
        self.assertIsNone(quote["previous_close_date"])

    def test_close_requires_end_of_session_trade_and_dated_session(self):
        payload = result()
        after_close = NOW.replace(hour=14)
        quote = parse(payload, now_tpe=after_close, closing=True)
        self.assertEqual(quote["status"], "unavailable")
        self.assertIsNone(quote["price"])
        self.assertEqual(quote["open"], 103)
        self.assertFalse(quote["final"])
        payload["meta"]["regularMarketTime"] = stamp(13, 30)
        quote = parse(payload, now_tpe=after_close, closing=True)
        self.assertEqual(quote["status"], "ok")
        self.assertTrue(quote["final"])
        self.assertEqual(quote["price"], 105)

    def test_future_or_missing_session_end_does_not_confirm_close(self):
        for regular in (
            {}, {"start": stamp(9), "end": stamp(14, 30)},
            {"start": stamp(9, day=2), "end": stamp(13, 30, day=2)},
            {"start": stamp(9), "end": stamp(13, 29)},
            {"start": stamp(8), "end": stamp(13, 30)},
        ):
            payload = result()
            payload["meta"]["regularMarketTime"] = stamp(13, 30)
            payload["meta"]["currentTradingPeriod"]["regular"] = regular
            with self.subTest(regular=regular):
                quote = parse(payload, now_tpe=NOW.replace(hour=14), closing=True)
                self.assertFalse(quote["final"])
                self.assertIsNone(quote["price"])

    def test_nonclosing_request_never_implicitly_labels_final(self):
        payload = result()
        payload["meta"]["regularMarketTime"] = stamp(13, 30)
        self.assertFalse(parse(payload, now_tpe=NOW.replace(hour=14))["final"])

    def test_after_hours_trade_cannot_be_regular_close_or_intraday_quote(self):
        for hour, minute in ((13, 31), (14, 0), (14, 30)):
            payload = result()
            payload["meta"]["regularMarketTime"] = stamp(hour, minute)
            for closing in (False, True):
                with self.subTest(hour=hour, minute=minute, closing=closing):
                    quote = parse(payload, now_tpe=NOW.replace(hour=15), closing=closing)
                    self.assertEqual(quote["status"], "unavailable")
                    self.assertIsNone(quote["price"])
                    self.assertFalse(quote["final"])

    def test_malformed_provider_payloads_fail_closed(self):
        for payload in (None, [], {}, {"meta": []}, {**result(), "indicators": []},
                        {**result(), "indicators": {"quote": [{} , {}]}}):
            with self.subTest(payload=payload):
                self.assertEqual(parse(payload)["status"], "unavailable")


class PredictionQuoteLoadingTests(unittest.TestCase):
    @patch("prediction_quotes.requests.get")
    def test_deduplicates_records_and_sets_timeout(self, mocked):
        mocked.return_value = response(result())
        rows = [{"代號": "2330", "Revenue_Source": "TWSE"}, {"代號": "2330"}]
        original = deepcopy(rows)
        quotes = fetch_prediction_quotes(rows, now_tpe=NOW)
        self.assertEqual(list(quotes), ["2330"])
        self.assertEqual(quotes["2330"]["status"], "ok")
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.kwargs["timeout"], (3, 8))
        self.assertEqual(mocked.call_args.kwargs["params"]["interval"], "1d")
        self.assertEqual(rows, original)

    @patch("prediction_quotes.requests.get")
    def test_known_otc_uses_only_tpex_symbol(self, mocked):
        mocked.return_value = response(result("6488.TWO"))
        quotes = fetch_prediction_quotes([{"代號": "6488", "Institutional_Source": "TPEx 3insti"}], now_tpe=NOW)
        self.assertEqual(quotes["6488"]["status"], "ok")
        self.assertTrue(mocked.call_args.args[0].endswith("6488.TWO"))
        mocked.assert_called_once()

    @patch("prediction_quotes.requests.get")
    def test_explicit_suffix_is_preserved(self, mocked):
        mocked.return_value = response(result("6488.TWO"))
        quotes = fetch_prediction_quotes([{"ticker": "6488.TWO"}], now_tpe=NOW)
        self.assertEqual(quotes["6488"]["symbol"], "6488.TWO")
        mocked.assert_called_once()

    @patch("prediction_quotes.requests.get")
    def test_unknown_venue_has_at_most_two_attempts(self, mocked):
        failure = Mock()
        failure.json.return_value = {"chart": {"result": None}}
        mocked.side_effect = [failure, response(result("6488.TWO"))]
        quotes = fetch_prediction_quotes([{"代號": "6488"}], now_tpe=NOW)
        self.assertEqual(quotes["6488"]["status"], "ok")
        self.assertEqual(mocked.call_count, 2)

    @patch("prediction_quotes.requests.get")
    def test_unverified_close_keeps_known_opening_and_does_not_probe_other_venue(self, mocked):
        mocked.return_value = response(result())
        quotes = fetch_prediction_quotes([{"代號": "2330"}], now_tpe=NOW + timedelta(hours=3), closing=True)
        self.assertEqual(quotes["2330"]["open"], 103)
        self.assertEqual(quotes["2330"]["status"], "unavailable")
        mocked.assert_called_once()

    @patch("prediction_quotes.requests.get")
    def test_error_is_sanitized_and_every_ticker_has_unavailable_record(self, mocked):
        mocked.side_effect = RuntimeError("secret-token-in-error")
        quotes = fetch_prediction_quotes([{"代號": "2330"}], now_tpe=NOW)
        self.assertEqual(quotes["2330"]["status"], "unavailable")
        self.assertEqual(mocked.call_count, 2)
        self.assertNotIn("secret-token", str(quotes))
        self.assertIsNone(quotes["2330"]["price"])

    @patch("prediction_quotes.requests.get")
    def test_empty_invalid_inputs_do_not_request_network(self, mocked):
        self.assertEqual(fetch_prediction_quotes([], now_tpe=NOW), {})
        self.assertEqual(fetch_prediction_quotes([None, {}, {"代號": "bad/path"}], now_tpe=NOW), {})
        mocked.assert_not_called()

    @patch("prediction_quotes.requests.get")
    def test_wrong_returned_symbol_is_never_accepted(self, mocked):
        mocked.return_value = response(result("2317.TW"))
        quotes = fetch_prediction_quotes([{"代號": "2330", "Revenue_Source": "TWSE"}], now_tpe=NOW)
        self.assertEqual(quotes["2330"]["status"], "unavailable")
        mocked.assert_called_once()


class MarketIndexQuoteTests(unittest.TestCase):
    def fetch(self, payload=None, *, now_tpe=NOW, closing=False):
        mocked = Mock(return_value=response(result("^TWII") if payload is None else payload))
        return fetch_market_index_quote(now_tpe=now_tpe, closing=closing, http_get=mocked), mocked

    def test_fresh_index_uses_real_previous_session_and_bounded_request(self):
        quote, mocked = self.fetch()
        self.assertEqual(quote["symbol"], "^TWII")
        self.assertEqual(quote["status"], "ok")
        self.assertEqual(quote["open"], 103)
        self.assertEqual(quote["price"], 105)
        self.assertEqual(quote["previous_close"], 101)
        self.assertEqual(quote["previous_close_date"], "2026-10-02")
        self.assertEqual(quote["observed_at"], "2026-10-05T10:40:00+08:00")
        self.assertEqual(quote["date"], "2026-10-05")
        self.assertFalse(quote["final"])
        mocked.assert_called_once()
        self.assertTrue(mocked.call_args.args[0].endswith("%5ETWII"))
        self.assertEqual(mocked.call_args.kwargs["timeout"], (3, 8))
        self.assertEqual(mocked.call_args.kwargs["params"], {
            "range": "5d", "interval": "1d", "includePrePost": "false",
        })

    def test_stale_same_day_index_keeps_actual_time_and_is_not_final(self):
        payload = result("^TWII")
        payload["meta"]["regularMarketTime"] = stamp(9, 20)
        quote, _ = self.fetch(payload)
        self.assertEqual(quote["status"], "ok")
        self.assertEqual(quote["price"], 105)
        self.assertEqual(quote["observed_at"], "2026-10-05T09:20:00+08:00")
        self.assertFalse(quote["final"])

    def test_prior_day_future_and_missing_index_times_are_unavailable(self):
        for observed in (stamp(13, 30, day=2), stamp(11, 1), None):
            payload = result("^TWII")
            payload["meta"]["regularMarketTime"] = observed
            with self.subTest(observed=observed):
                quote, mocked = self.fetch(payload)
                self.assertEqual(quote["status"], "unavailable")
                self.assertIsNone(quote["price"])
                self.assertIsNone(quote["observed_at"])
                self.assertTrue(quote["reason"])
                mocked.assert_called_once()

    def test_index_previous_close_never_uses_metadata_or_today(self):
        for previous_day in (None, 1):
            payload = result("^TWII")
            if previous_day is None:
                payload["timestamp"] = payload["timestamp"][1:]
                for key, values in payload["indicators"]["quote"][0].items():
                    payload["indicators"]["quote"][0][key] = values[1:]
            else:
                payload["timestamp"][0] = stamp(9, day=previous_day)
            with self.subTest(previous_day=previous_day):
                quote, _ = self.fetch(payload)
                self.assertEqual(quote["status"], "ok")
                self.assertEqual(quote["price"], 105)
                self.assertIsNone(quote["previous_close"])
                self.assertIsNone(quote["previous_close_date"])

    def test_index_close_requires_dated_session_and_closing_observation(self):
        after_close = NOW.replace(hour=14)
        payload = result("^TWII")
        quote, _ = self.fetch(payload, now_tpe=after_close, closing=True)
        self.assertEqual(quote["status"], "unavailable")
        self.assertIsNone(quote["price"])
        self.assertEqual(quote["open"], 103)
        self.assertFalse(quote["final"])
        payload["meta"]["regularMarketTime"] = stamp(13, 30)
        quote, _ = self.fetch(payload, now_tpe=after_close, closing=True)
        self.assertEqual(quote["status"], "ok")
        self.assertEqual(quote["price"], 105)
        self.assertTrue(quote["final"])
        payload["meta"]["currentTradingPeriod"]["regular"]["end"] = stamp(13, 30, day=2)
        quote, _ = self.fetch(payload, now_tpe=after_close, closing=True)
        self.assertEqual(quote["status"], "unavailable")
        self.assertIsNone(quote["price"])
        self.assertFalse(quote["final"])

    def test_index_after_hours_trade_is_not_used_as_a_close(self):
        payload = result("^TWII")
        payload["meta"]["regularMarketTime"] = stamp(13, 31)
        quote, _ = self.fetch(payload, now_tpe=NOW.replace(hour=14), closing=True)
        self.assertEqual(quote["status"], "unavailable")
        self.assertIsNone(quote["price"])
        self.assertFalse(quote["final"])

    def test_index_does_not_implicitly_confirm_close(self):
        payload = result("^TWII")
        payload["meta"]["regularMarketTime"] = stamp(13, 30)
        quote, _ = self.fetch(payload, now_tpe=NOW.replace(hour=14))
        self.assertEqual(quote["status"], "ok")
        self.assertFalse(quote["final"])

    def test_mismatched_index_identity_and_daily_values_fail_closed(self):
        for field, value in (("symbol", "2330.TW"), ("currency", "USD"),
                             ("exchangeTimezoneName", "UTC"), ("regularMarketPrice", 106)):
            payload = result("^TWII")
            payload["meta"][field] = value
            with self.subTest(field=field):
                quote, _ = self.fetch(payload)
                self.assertEqual(quote["status"], "unavailable")
                self.assertIsNone(quote["price"])

    @patch("prediction_quotes.requests.get")
    def test_default_transport_is_mockable_and_failure_is_sanitized(self, mocked):
        mocked.side_effect = RuntimeError("secret-token-in-error")
        quote = fetch_market_index_quote(now_tpe=NOW)
        self.assertEqual(quote["status"], "unavailable")
        self.assertEqual(quote["symbol"], "^TWII")
        self.assertIsNone(quote["price"])
        self.assertNotIn("secret-token", str(quote))
        mocked.assert_called_once()

    def test_malformed_http_response_is_unavailable_without_retry(self):
        for payload in (None, [], {}, {"chart": {"result": None}},
                        {"chart": {"result": []}}, {"chart": {"result": [{}, {}]}}):
            mocked_response = Mock()
            mocked_response.json.return_value = payload
            mocked = Mock(return_value=mocked_response)
            with self.subTest(payload=payload):
                quote = fetch_market_index_quote(now_tpe=NOW, http_get=mocked)
                self.assertEqual(quote["status"], "unavailable")
                self.assertIsNone(quote["price"])
                mocked.assert_called_once()

    def test_index_naive_clock_rejected_before_network(self):
        mocked = Mock()
        with self.assertRaises(ValueError):
            fetch_market_index_quote(now_tpe=NOW.replace(tzinfo=None), http_get=mocked)
        mocked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
