"""Offline parser checks: no Firebase, Yahoo or exchange requests."""

import unittest
from copy import deepcopy
from unittest.mock import patch

from official_scan_universe import parse_twse_daily_universe


def report():
    return {
        "stat": "OK", "date": "20261013", "type": "ALLBUT0999",
        "params": {"date": "20261013", "type": "ALLBUT0999", "response": "json"},
        "tables": [
            {"title": "指數", "fields": ["指數", "收盤指數"], "data": [["發行量加權", "100.00"]]},
            {"title": "每日收盤行情", "fields": ["證券名稱", "成交股數", "證券代號", "收盤價"],
             "data": [["台積電", "40,700,261", "2330", "2,490.00"],
                      ["無成交股票", "0", "1234", "--"], ["ETF", "1,000", "00632R", "20.00"]]},
            {"fields": [], "data": []},
        ],
    }


class OfficialScanUniverseTests(unittest.TestCase):
    def setUp(self):
        blocked = patch("requests.sessions.Session.request", side_effect=AssertionError("network forbidden"))
        blocked.start()
        self.addCleanup(blocked.stop)
        self.payload = report()

    def parse(self, payload=None, expected="2026-10-13"):
        return parse_twse_daily_universe(self.payload if payload is None else payload, expected)

    def test_actual_fields_are_found_by_name_and_response_date_becomes_roc(self):
        original = deepcopy(self.payload)
        rows = self.parse()
        self.assertEqual(rows, [
            {"Code": "2330", "Name": "台積電", "TradeVolume": "40700261", "Date": "1151013"},
            {"Code": "1234", "Name": "無成交股票", "TradeVolume": "0", "Date": "1151013"},
            {"Code": "00632R", "Name": "ETF", "TradeVolume": "1000", "Date": "1151013"},
        ])
        self.assertEqual(self.payload, original)

    def test_stock_table_position_can_change(self):
        self.payload["tables"].reverse()
        self.assertEqual(self.parse()[0]["Code"], "2330")

    def test_notes_only_section_and_unrelated_headers_do_not_block_stock_table(self):
        self.payload["tables"].extend([
            {"notes": ["官方註解"]},
            {"title": "說明", "fields": None},
            {"fields": ["其他", "其他"], "data": [["甲", "乙"]]},
        ])
        self.assertEqual(self.parse()[0]["Code"], "2330")

    def test_optional_params_echo_is_not_the_authoritative_date(self):
        del self.payload["params"]
        self.assertEqual(self.parse()[0]["Date"], "1151013")
        self.payload["date"] = "20261012"
        with self.assertRaises(ValueError):
            self.parse()

    def test_missing_failed_or_wrong_report_metadata_rejected(self):
        for key, value in (("stat", "查無資料"), ("stat", True), ("stat", None),
                           ("type", "ALL"), ("type", None), ("date", None),
                           ("date", "20261012"), ("date", "2026-10-13"),
                           ("date", "20261313"), ("date", 20261013)):
            changed = deepcopy(self.payload)
            changed[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.parse(changed)

    def test_expected_date_must_be_strict_iso(self):
        for expected in (None, 20261013, "20261013", "2026-10-13T00:00:00", "2026-02-30", "2026-10-12"):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                self.parse(expected=expected)

    def test_mismatched_or_malformed_echo_params_rejected(self):
        for params in (None, [], {}, {"date": "20261012", "type": "ALLBUT0999"},
                       {"date": "20261013", "type": "ALL"}):
            changed = deepcopy(self.payload)
            changed["params"] = params
            with self.subTest(params=params), self.assertRaises(ValueError):
                self.parse(changed)

    def test_date_cannot_be_relabelled_from_request_or_params(self):
        self.payload["date"] = "20261012"
        self.payload["tables"][1]["title"] = "115年10月13日每日收盤行情"
        with self.assertRaises(ValueError):
            self.parse()

    def test_non_mapping_payloads_rejected(self):
        for value in ([], "OK", True, 123):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.parse(value)

    def test_no_table_or_multiple_matching_tables_rejected(self):
        stock_table = deepcopy(self.payload["tables"][1])
        for tables in (None, [], [None], {}, [self.payload["tables"][0]], [stock_table, stock_table]):
            changed = deepcopy(self.payload)
            changed["tables"] = tables
            with self.subTest(tables=tables), self.assertRaises(ValueError):
                self.parse(changed)

    def test_duplicate_or_missing_or_non_text_fields_rejected(self):
        for fields in (None, "證券代號", ["證券代號", "證券名稱", "成交股數", "成交股數"],
                       ["證券代號", "證券名稱"], ["證券代號", "證券名稱", "成交股數", None]):
            changed = deepcopy(self.payload)
            changed["tables"][1]["fields"] = fields
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.parse(changed)

    def test_row_shape_empty_data_and_bad_identifiers_rejected(self):
        for rows in (None, [], {}, [None], [["台積電", "100", "2330"]],
                     [["台積電", "100", "2330", "100", "extra"]],
                     [["台積電", "100", 2330, "100"]], [["", "100", "2330", "100"]],
                     [["台積電", "100", "<2330>", "100"]]):
            changed = deepcopy(self.payload)
            changed["tables"][1]["data"] = rows
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.parse(changed)

    def test_duplicate_code_including_whitespace_alias_rejected(self):
        for code in ("2330", " 2330 "):
            changed = deepcopy(self.payload)
            changed["tables"][1]["data"].append(["重複股", "100", code, "100"])
            with self.subTest(code=code), self.assertRaises(ValueError):
                self.parse(changed)

    def test_invalid_volume_is_never_dropped_or_replaced_with_zero(self):
        invalid = (None, "", "--", "NaN", "Infinity", float("nan"), float("inf"),
                   True, False, -1, "-1", "+1", "1.5", 1.5, 1.0, "1e6", "01", "1,23", "1,000,", "1,,000", "9" * 400)
        for volume in invalid:
            changed = deepcopy(self.payload)
            changed["tables"][1]["data"][0][1] = volume
            with self.subTest(volume=volume), self.assertRaises(ValueError):
                self.parse(changed)

    def test_positive_and_zero_integer_volumes_are_preserved(self):
        for volume, expected in ((0, "0"), (123, "123"), (" 1,234 ", "1234"), ("1234567890", "1234567890")):
            changed = deepcopy(self.payload)
            changed["tables"][1]["data"][0][1] = volume
            with self.subTest(volume=volume):
                self.assertEqual(self.parse(changed)[0]["TradeVolume"], expected)


if __name__ == "__main__":
    unittest.main()
