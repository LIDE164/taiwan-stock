"""Parse the dated TWSE MI_INDEX universe without fetching or inventing data."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import date
from typing import Any


_FIELDS = ("證券代號", "證券名稱", "成交股數")
_REPORT_TYPE = "ALLBUT0999"
_SHARES = re.compile(r"(?:0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)")


def _response_date(payload: Mapping[str, Any], expected_date: str) -> str:
    try:
        expected = date.fromisoformat(expected_date)
    except (TypeError, ValueError):
        raise ValueError("預期行情日期必須為完整 ISO 日期") from None
    if not isinstance(expected_date, str) or expected.isoformat() != expected_date:
        raise ValueError("預期行情日期必須為完整 ISO 日期")
    raw = payload.get("date")
    if not isinstance(raw, str) or re.fullmatch(r"[0-9]{8}", raw) is None:
        raise ValueError("TWSE 回應缺少明確行情日期")
    try:
        observed = date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
    except ValueError:
        raise ValueError("TWSE 回應行情日期無效") from None
    if observed != expected:
        raise ValueError("TWSE 回應行情日期與預期不符")
    if "params" in payload:
        params = payload["params"]
        if (not isinstance(params, Mapping) or params.get("date") != raw
                or params.get("type") != _REPORT_TYPE):
            raise ValueError("TWSE 回應參數與行情日期或報表類別矛盾")
    roc_year = observed.year - 1911
    if not 1 <= roc_year <= 999:
        raise ValueError("TWSE 回應日期無法轉成既有民國日期格式")
    # Derived from the validated response date, never from a request label.
    return f"{roc_year:03d}{observed.month:02d}{observed.day:02d}"


def _shares(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("TWSE 成交股數必須為非負整數")
    text = str(value).strip()
    if _SHARES.fullmatch(text) is None:
        raise ValueError("TWSE 成交股數缺失或數字格式錯誤")
    plain = text.replace(",", "")
    try:
        finite = math.isfinite(float(plain))
    except (ValueError, OverflowError):
        finite = False
    if not finite:
        raise ValueError("TWSE 成交股數必須為有限數值")
    return plain


def parse_twse_daily_universe(payload: Any, expected_date: str) -> list[dict[str, str]]:
    """Return the existing STOCK_DAY_ALL-like code/name/volume/date fields.

    The full dated report must contain exactly one identifiable stock table.
    Zero volume remains a genuine zero for the scanner's existing filter.
    No row is silently dropped and no missing value becomes zero.
    """
    if not isinstance(payload, Mapping) or payload.get("stat") != "OK":
        raise ValueError("TWSE 全市場行情未確認成功")
    if payload.get("type") != _REPORT_TYPE:
        raise ValueError("TWSE 全市場行情報表類別不符")
    roc_date = _response_date(payload, expected_date)
    tables = payload.get("tables")
    if not isinstance(tables, list) or not tables:
        raise ValueError("TWSE 全市場行情缺少表格")
    candidates = []
    for table in tables:
        if not isinstance(table, Mapping):
            raise ValueError("TWSE 行情表格格式錯誤")
        fields = table.get("fields")
        # MI_INDEX includes a trailing notes-only section without fields/data.
        # It is not a stock table; validate the schema of stock candidates only.
        if not isinstance(fields, list) or not any(isinstance(field, str) and field in _FIELDS for field in fields):
            continue
        if (not isinstance(fields, list) or any(not isinstance(field, str) for field in fields)
                or len(fields) != len(set(fields))):
            raise ValueError("TWSE 行情欄位無效或重複")
        if not set(_FIELDS).issubset(fields):
            raise ValueError("TWSE 股票行情缺少必要欄位")
        candidates.append(table)
    if len(candidates) != 1:
        raise ValueError("TWSE 股票行情表格缺失或不唯一")
    selected = candidates[0]
    fields = selected["fields"]
    rows = selected.get("data")
    if not isinstance(rows, list) or not rows:
        raise ValueError("TWSE 股票行情資料為空或格式錯誤")
    indices = {field: fields.index(field) for field in _FIELDS}
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, list) or len(row) != len(fields):
            raise ValueError("TWSE 股票行情列寬與欄位不符")
        code, name = row[indices["證券代號"]], row[indices["證券名稱"]]
        if (not isinstance(code, str) or not isinstance(name, str)
                or re.fullmatch(r"[0-9A-Z]+", code.strip()) is None or not name.strip()):
            raise ValueError("TWSE 股票代號或名稱缺失／格式錯誤")
        code, name = code.strip(), name.strip()
        if code in seen:
            raise ValueError("TWSE 股票行情代號重複")
        seen.add(code)
        result.append({
            "Code": code, "Name": name,
            "TradeVolume": _shares(row[indices["成交股數"]]), "Date": roc_date,
        })
    return result
