"""Shared, source-aware chip and revenue providers used by scanner and app."""

from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from requests import RequestException

from app_security import normalize_ticker
from market_http import http_get

FINMIND_DATA_URL = "https://api.finmindtrade.com/api/v4/data"
TWSE_REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
TPEX_REVENUE_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"
TWSE_INCOME_STATEMENT_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci"
TWSE_BALANCE_SHEET_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap07_L_ci"
TPEX_INCOME_STATEMENT_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ci"
TPEX_BALANCE_SHEET_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap07_O_ci"
TWSE_INSTITUTIONAL_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
TPEX_INSTITUTIONAL_URL = (
    "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php"
)
logger = logging.getLogger(__name__)
TPE = timezone(timedelta(hours=8))

_CACHE_TTL_SECONDS = 6 * 60 * 60
_CACHE_MAX_ENTRIES = 128
_CACHE_LOCK = threading.RLock()
_JSON_CACHE: dict[str, tuple[float, Any]] = {}
_KEY_LOCKS: dict[str, threading.Lock] = {}
_MARKET_INDEX: dict[str, str] = {}


def _prune_json_cache(now: float) -> None:
    expired = [key for key, (saved_at, _) in _JSON_CACHE.items() if now - saved_at > _CACHE_TTL_SECONDS]
    for key in expired:
        _JSON_CACHE.pop(key, None)
        key_lock = _KEY_LOCKS.get(key)
        if key_lock is None or not key_lock.locked():
            _KEY_LOCKS.pop(key, None)
    overflow = len(_JSON_CACHE) - _CACHE_MAX_ENTRIES
    if overflow > 0:
        oldest = sorted(_JSON_CACHE, key=lambda key: _JSON_CACHE[key][0])[:overflow]
        for key in oldest:
            _JSON_CACHE.pop(key, None)
            key_lock = _KEY_LOCKS.get(key)
            if key_lock is None or not key_lock.locked():
                _KEY_LOCKS.pop(key, None)


def clear_provider_cache() -> None:
    """Clear process-local public-data caches (mainly useful for tests/manual refresh)."""
    with _CACHE_LOCK:
        _JSON_CACHE.clear()
        _KEY_LOCKS.clear()
        _MARKET_INDEX.clear()


def _cached_json(key: str, url: str, *, params: dict[str, Any] | None = None) -> Any:
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _JSON_CACHE.get(key)
        if cached and now - cached[0] <= _CACHE_TTL_SECONDS:
            return cached[1]
        key_lock = _KEY_LOCKS.setdefault(key, threading.Lock())
    # Deduplicate the same resource while still allowing different trading dates in parallel.
    with key_lock:
        with _CACHE_LOCK:
            cached = _JSON_CACHE.get(key)
            if cached and now - cached[0] <= _CACHE_TTL_SECONDS:
                return cached[1]
        response = http_get(url, params=params, timeout=12)
        response.raise_for_status()
        payload = response.json()
        with _CACHE_LOCK:
            saved_at = time.monotonic()
            _JSON_CACHE[key] = (saved_at, payload)
            _prune_json_cache(saved_at)
        return payload


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        text = str(value).strip().replace(",", "")
        if text in ("", "-", "--", "N/A", "None"):
            return None
        number = float(text)
        return number if pd.notna(number) else None
    except (TypeError, ValueError):
        return None


def _roc_month_to_iso(value: Any) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) < 5:
        return ""
    try:
        return f"{int(digits[:-2]) + 1911:04d}-{int(digits[-2:]):02d}"
    except ValueError:
        return ""


def _roc_date(value: datetime) -> str:
    return f"{value.year - 1911:03d}/{value.month:02d}/{value.day:02d}"


def _first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _financial_period(row: dict[str, Any]) -> str:
    year_value = _first_value(row, "年度", "Year", "year")
    quarter_value = _first_value(row, "季別", "Season", "season", "Quarter", "quarter")
    year_digits = "".join(character for character in str(year_value or "") if character.isdigit())
    quarter_digits = "".join(character for character in str(quarter_value or "") if character.isdigit())
    if not year_digits or not quarter_digits:
        return ""
    try:
        year = int(year_digits)
        quarter = int(quarter_digits)
    except ValueError:
        return ""
    if year < 1911:
        year += 1911
    if year < 1900 or quarter not in range(1, 5):
        return ""
    return f"{year:04d}-Q{quarter}"


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return round(numerator / denominator * 100, 2)


def _financial_risk(
    gross_margin: float | None,
    operating_margin: float | None,
    net_margin: float | None,
    debt_ratio: float | None,
    current_ratio: float | None,
) -> tuple[str, list[str]]:
    flags: list[str] = []
    severe = False
    warning = False
    if gross_margin is not None and gross_margin < 0:
        flags.append("negative_gross_margin")
        severe = True
    if operating_margin is not None and operating_margin < 0:
        flags.append("negative_operating_margin")
        severe = True
    if net_margin is not None and net_margin < 0:
        flags.append("negative_net_margin")
        severe = True
    if debt_ratio is not None:
        if debt_ratio > 70:
            flags.append("high_debt_ratio")
            severe = True
        elif debt_ratio > 60:
            flags.append("elevated_debt_ratio")
            warning = True
    if current_ratio is not None:
        if current_ratio < 100:
            flags.append("low_current_ratio")
            severe = True
        elif current_ratio < 150:
            flags.append("moderate_current_ratio")
            warning = True
    observed = (gross_margin, operating_margin, net_margin, debt_ratio, current_ratio)
    if not any(value is not None for value in observed):
        return "unknown", flags
    if severe:
        return "high", flags
    if warning:
        return "medium", flags
    return "low", flags


def _empty_financial_quality(status: str) -> dict[str, Any]:
    return {
        "period": "",
        "as_of_period": "",
        "period_type": "fiscal_quarter",
        "snapshot_type": "current_published",
        "freshness": "unknown",
        "source": "official open data (current snapshot)",
        "status": status,
        "revenue": None,
        "gross_profit": None,
        "operating_income": None,
        "net_income": None,
        "eps": None,
        "gross_margin": None,
        "operating_margin": None,
        "net_margin": None,
        "debt_ratio": None,
        "current_ratio": None,
        "risk_level": "unknown",
        "risk_flags": [],
    }


def _financial_rows(market: str, statement: str) -> list[dict[str, Any]]:
    urls = {
        ("listed", "income"): TWSE_INCOME_STATEMENT_URL,
        ("listed", "balance"): TWSE_BALANCE_SHEET_URL,
        ("otc", "income"): TPEX_INCOME_STATEMENT_URL,
        ("otc", "balance"): TPEX_BALANCE_SHEET_URL,
    }
    payload = _cached_json(f"financial-quality:{market}:{statement}", urls[(market, statement)])
    if not isinstance(payload, list):
        raise TypeError("official financial statement payload is not a list")
    return [row for row in payload if isinstance(row, dict)]


def _financial_ticker(row: dict[str, Any]) -> str:
    return normalize_ticker(
        _first_value(row, "公司代號", "SecuritiesCompanyCode", "證券代號", "CompanyCode")
    )


def _parse_financial_quality_rows(
    income_row: dict[str, Any] | None,
    balance_row: dict[str, Any] | None,
    *,
    period: str,
    source: str,
) -> dict[str, Any]:
    income_row = income_row or {}
    balance_row = balance_row or {}
    revenue = _number(_first_value(income_row, "營業收入", "收入合計", "營業收益"))
    gross_profit = _number(
        _first_value(income_row, "營業毛利（毛損）淨額", "營業毛利（毛損）", "營業毛利(毛損)淨額", "營業毛利(毛損)")
    )
    operating_income = _number(_first_value(income_row, "營業利益（損失）", "營業利益(損失)"))
    net_income = _number(_first_value(income_row, "本期淨利（淨損）", "本期淨利(淨損)", "本期稅後淨利（淨損）"))
    eps = _number(_first_value(income_row, "基本每股盈餘（元）", "基本每股盈餘(元)", "基本每股盈餘"))
    current_assets = _number(_first_value(balance_row, "流動資產"))
    total_assets = _number(_first_value(balance_row, "資產總計", "資產合計"))
    current_liabilities = _number(_first_value(balance_row, "流動負債"))
    total_liabilities = _number(_first_value(balance_row, "負債總計", "負債合計"))

    gross_margin = _ratio(gross_profit, revenue)
    operating_margin = _ratio(operating_income, revenue)
    net_margin = _ratio(net_income, revenue)
    debt_ratio = _ratio(total_liabilities, total_assets)
    current_ratio = _ratio(current_assets, current_liabilities)
    risk_level, risk_flags = _financial_risk(
        gross_margin, operating_margin, net_margin, debt_ratio, current_ratio
    )
    required_values = (
        revenue, gross_profit, operating_income, net_income, eps,
        gross_margin, operating_margin, net_margin, debt_ratio, current_ratio,
    )
    return {
        "period": period,
        "as_of_period": period,
        "period_type": "fiscal_quarter",
        # The official endpoint is a current snapshot and does not expose the
        # original publication timestamp.  Do not infer one: callers can use
        # ``as_of_period`` to apply their own as-of policy.
        "snapshot_type": "current_published",
        "freshness": "unknown",
        "source": source,
        "status": "ok" if all(value is not None for value in required_values) else "partial",
        "revenue": revenue,
        "gross_profit": gross_profit,
        "operating_income": operating_income,
        "net_income": net_income,
        "eps": eps,
        "gross_margin": gross_margin,
        "operating_margin": operating_margin,
        "net_margin": net_margin,
        "debt_ratio": debt_ratio,
        "current_ratio": current_ratio,
        "risk_level": risk_level,
        "risk_flags": risk_flags,
    }


def fetch_financial_quality(ticker: Any) -> dict[str, Any]:
    """Return the latest official current-snapshot financial quality metrics.

    These OpenAPI resources expose the currently published quarter, not a
    historical point-in-time series. Missing statement values remain ``None``.
    """
    code = normalize_ticker(ticker)
    if not code:
        return _empty_financial_quality("empty")
    successful_request = False
    partial_match: tuple[dict[str, Any] | None, dict[str, Any] | None, str, str] | None = None
    for market, source in (("listed", "TWSE OpenAPI (current snapshot)"), ("otc", "TPEx OpenAPI (current snapshot)")):
        income_rows: list[dict[str, Any]] = []
        balance_rows: list[dict[str, Any]] = []
        try:
            income_rows = _financial_rows(market, "income")
            successful_request = True
        except (RequestException, TypeError, ValueError) as exc:
            logger.warning("%s income statement request failed (%s)", market, type(exc).__name__)
        try:
            balance_rows = _financial_rows(market, "balance")
            successful_request = True
        except (RequestException, TypeError, ValueError) as exc:
            logger.warning("%s balance sheet request failed (%s)", market, type(exc).__name__)

        income_by_period = {
            _financial_period(row): row
            for row in income_rows
            if _financial_ticker(row) == code and _financial_period(row)
        }
        balance_by_period = {
            _financial_period(row): row
            for row in balance_rows
            if _financial_ticker(row) == code and _financial_period(row)
        }
        common_periods = income_by_period.keys() & balance_by_period.keys()
        if common_periods:
            latest_period = max(common_periods)
            return _parse_financial_quality_rows(
                income_by_period[latest_period], balance_by_period[latest_period],
                period=latest_period, source=source,
            )
        available_periods = income_by_period.keys() | balance_by_period.keys()
        if available_periods:
            latest_period = max(available_periods)
            partial_match = (
                income_by_period.get(latest_period), balance_by_period.get(latest_period), latest_period, source,
            )

    if partial_match:
        income_row, balance_row, period, source = partial_match
        return _parse_financial_quality_rows(income_row, balance_row, period=period, source=source)
    return _empty_financial_quality("empty" if successful_request else "error")


def _shares_to_lots(value: Any) -> float | None:
    """Convert a reported share count to lots without inventing a zero.

    Missing/invalid official fields used to become ``0`` here, which made a
    schema change indistinguishable from a genuine zero-position day.
    """
    shares = _number(value)
    # One lot is 1,000 shares, so three decimal places preserve an official
    # integer share count exactly.  Rounding to a whole lot turned genuine
    # small trades (for example 300 shares) into a fabricated zero.
    return round(shares / 1000, 3) if shares is not None else None


def _expected_revenue_period(now: datetime) -> str:
    """Latest calendar month expected after the statutory publication window."""
    local_now = now.astimezone(TPE) if now.tzinfo else now.replace(tzinfo=TPE)
    month_offset = -1 if local_now.day >= 11 else -2
    absolute_month = local_now.year * 12 + local_now.month - 1 + month_offset
    year, zero_based_month = divmod(absolute_month, 12)
    return f"{year:04d}-{zero_based_month + 1:02d}"


def _annotate_revenue_freshness(
    payload: dict[str, Any], now: datetime
) -> dict[str, Any]:
    """Attach period freshness without presenting an old release as current."""
    result = dict(payload)
    expected = _expected_revenue_period(now)
    result["expected_period"] = expected
    period = str(result.get("period") or "").strip()
    try:
        datetime.strptime(period, "%Y-%m")
    except (TypeError, ValueError):
        result["freshness"] = "unknown"
        return result
    stale = period < expected
    result["freshness"] = "stale" if stale else "fresh"
    if stale and str(result.get("status") or "").lower() in {"ok", "partial"}:
        result["status"] = "stale"
    return result


def _official_revenue_rows(market: str) -> list[dict[str, Any]]:
    url = TWSE_REVENUE_URL if market == "listed" else TPEX_REVENUE_URL
    payload = _cached_json(f"revenue:{market}", url)
    if not isinstance(payload, list):
        raise TypeError("official revenue payload is not a list")
    rows = [row for row in payload if isinstance(row, dict)]
    with _CACHE_LOCK:
        for row in rows:
            code = normalize_ticker(row.get("公司代號"))
            if code:
                _MARKET_INDEX[code] = market
    return rows


def _parse_official_revenue_row(row: dict[str, Any], source: str) -> dict[str, Any]:
    mom = _number(row.get("營業收入-上月比較增減(%)"))
    yoy = _number(row.get("營業收入-去年同月增減(%)"))
    period = _roc_month_to_iso(row.get("資料年月"))
    return {
        "mom": round(mom, 2) if mom is not None else None,
        "yoy": round(yoy, 2) if yoy is not None else None,
        "period": period,
        "as_of_period": period,
        "period_type": "calendar_month",
        "freshness": "unknown",
        "source": source,
        "status": "ok" if period and mom is not None and yoy is not None else "partial",
    }


def _fetch_official_revenue_growth(ticker: Any) -> dict[str, Any]:
    code = normalize_ticker(ticker)
    successful_market_request = False
    for market, source in (("listed", "TWSE OpenAPI"), ("otc", "TPEx OpenAPI")):
        try:
            rows = _official_revenue_rows(market)
            successful_market_request = True
        except Exception as exc:
            logger.warning("%s revenue fallback failed (%s)", market, type(exc).__name__)
            continue
        row = next((item for item in rows if normalize_ticker(item.get("公司代號")) == code), None)
        if row:
            return _parse_official_revenue_row(row, source)
    return {
        "mom": None,
        "yoy": None,
        "period": "",
        "as_of_period": "",
        "period_type": "calendar_month",
        "freshness": "unknown",
        "source": "official open data",
        "status": "empty" if successful_market_request else "error",
    }


def _finmind_rows(dataset: str, ticker: Any, start_date: str, token: str) -> tuple[list[dict[str, Any]], str]:
    code = normalize_ticker(ticker)
    if not code:
        return [], "missing"
    params = {
        "dataset": dataset,
        "data_id": code,
        "start_date": start_date,
    }
    # FinMind supports a bounded public quota without a token. A configured token
    # raises the quota, but its absence must not be treated as missing market data.
    if token:
        params["token"] = token
    response = http_get(
        FINMIND_DATA_URL,
        params=params,
        timeout=8,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("msg") not in (None, "success"):
        return [], "error"
    rows = payload.get("data", [])
    return (rows, "ok") if isinstance(rows, list) and rows else ([], "empty")


def fetch_revenue_growth(ticker: Any, token: str, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(TPE)
    # One official bulk request serves the entire tokenless scanner universe and
    # avoids consuming one FinMind public-quota request per stock.
    if not token:
        official_result = _annotate_revenue_freshness(
            _fetch_official_revenue_growth(ticker), now
        )
        if official_result["status"] in ("ok", "partial", "stale"):
            return official_result
    finmind_result: dict[str, Any] = {
        "mom": None,
        "yoy": None,
        "period": "",
        "as_of_period": "",
        "period_type": "calendar_month",
        "freshness": "unknown",
        "source": "FinMind",
        "status": "missing" if not token else "error",
    }
    try:
        rows, status = _finmind_rows(
            "TaiwanStockMonthRevenue",
            ticker,
            (now - timedelta(days=500)).strftime("%Y-%m-%d"),
            token,
        )
        mom = yoy = None
        period = ""
        if rows:
            frame = pd.DataFrame(rows).sort_values(by="date").reset_index(drop=True)
            frame["revenue"] = pd.to_numeric(frame["revenue"], errors="coerce")
            frame["period"] = pd.to_datetime(frame["date"], errors="coerce").dt.to_period("M")
            frame = frame.dropna(subset=["revenue", "period"]).drop_duplicates("period", keep="last")
            if not frame.empty:
                latest = frame.iloc[-1]
                latest_period = latest["period"]
                period = str(latest_period)
                latest_revenue = float(latest["revenue"])
                previous = frame[frame["period"] == latest_period - 1]
                year_ago = frame[frame["period"] == latest_period - 12]
                if not previous.empty and float(previous.iloc[-1]["revenue"]) > 0:
                    mom = (latest_revenue / float(previous.iloc[-1]["revenue"]) - 1) * 100
                if not year_ago.empty and float(year_ago.iloc[-1]["revenue"]) > 0:
                    yoy = (latest_revenue / float(year_ago.iloc[-1]["revenue"]) - 1) * 100
        if rows and (mom is None or yoy is None):
            status = "partial"
        finmind_result = _annotate_revenue_freshness({
            "mom": round(mom, 2) if mom is not None else None,
            "yoy": round(yoy, 2) if yoy is not None else None,
            "period": period,
            "as_of_period": period,
            "period_type": "calendar_month",
            "freshness": "unknown",
            "source": "FinMind",
            "status": status,
        }, now)
    except Exception as exc:
        logger.warning("FinMind revenue request failed for %s (%s)", normalize_ticker(ticker), type(exc).__name__)
        finmind_result["status"] = "error"

    if finmind_result["status"] == "ok":
        return finmind_result
    # A syntactically valid FinMind response can still be months behind.  In
    # that case consult the exchange snapshots rather than returning ``ok``.
    official_result = _annotate_revenue_freshness(
        _fetch_official_revenue_growth(ticker), now
    )
    if official_result["status"] in ("ok", "partial", "stale"):
        return official_result
    return finmind_result if token else official_result


def _market_for_ticker(ticker: Any) -> str:
    code = normalize_ticker(ticker)
    with _CACHE_LOCK:
        market = _MARKET_INDEX.get(code, "")
    if market:
        return market
    for candidate in ("listed", "otc"):
        try:
            _official_revenue_rows(candidate)
        except Exception:
            continue
        with _CACHE_LOCK:
            market = _MARKET_INDEX.get(code, "")
        if market:
            return market
    return ""


def _header_text(value: Any) -> str:
    """Return a stable comparison key for provider field labels."""
    if isinstance(value, dict):
        value = _first_value(value, "label", "name", "title", "text", "key")
    return (
        str(value or "")
        .replace("\n", "")
        .replace("\r", "")
        .replace(" ", "")
        .replace("（", "(")
        .replace("）", ")")
        .strip()
    )


def _field_value(row: dict[str, Any], *aliases: str) -> Any:
    normalized = {_header_text(key): value for key, value in row.items()}
    for alias in aliases:
        key = _header_text(alias)
        if key in normalized:
            return normalized[key]
    return None


def _build_institutional_row(
    *,
    row_date: datetime,
    source: str,
    foreign_ex_dealer: Any,
    trust: Any,
    dealer: Any,
    foreign_dealer: Any = None,
    reported_total: Any = None,
) -> dict[str, Any] | None:
    """Build one official row using the regulator's non-duplicating total.

    The official definition is foreign investors *excluding* foreign dealer
    self, plus investment trust, plus dealer.  Foreign dealer self is kept as
    separate metadata because it is already included in the dealer figure.
    """
    foreign_shares = _number(foreign_ex_dealer)
    trust_shares = _number(trust)
    dealer_shares = _number(dealer)
    if foreign_shares is None or trust_shares is None or dealer_shares is None:
        return None

    foreign_dealer_shares = _number(foreign_dealer)
    reported_total_shares = _number(reported_total)
    computed_total_shares = foreign_shares + trust_shares + dealer_shares
    if reported_total_shares is not None and abs(reported_total_shares - computed_total_shares) > 1:
        # A provider schema change must not silently produce a plausible but
        # wrong chip value.  The caller treats the skipped row as partial.
        return None

    foreign_lots = _shares_to_lots(foreign_shares)
    trust_lots = _shares_to_lots(trust_shares)
    dealer_lots = _shares_to_lots(dealer_shares)
    total_lots = _shares_to_lots(
        reported_total_shares if reported_total_shares is not None else computed_total_shares
    )
    if None in (foreign_lots, trust_lots, dealer_lots, total_lots):
        return None
    return {
        "date": row_date.strftime("%Y-%m-%d"),
        # ``foreign`` intentionally excludes Foreign_Dealer_Self.
        "foreign": foreign_lots,
        "foreign_ex_dealer": foreign_lots,
        "foreign_dealer": _shares_to_lots(foreign_dealer_shares),
        "foreign_semantics": "excludes_foreign_dealer",
        "trust": trust_lots,
        "dealer": dealer_lots,
        "total": total_lots,
        "reported_total": _shares_to_lots(reported_total_shares),
        "total_validation": "matched" if reported_total_shares is not None else "unavailable",
        "data_status": "ok" if reported_total_shares is not None else "partial",
        "source": source,
    }


def _parse_twse_institutional_payload(payload: Any, ticker: Any, row_date: datetime) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return None
    code = normalize_ticker(ticker)
    fields = payload.get("fields", [])
    data = payload.get("data", [])
    if not isinstance(fields, list) or not isinstance(data, list):
        return None
    for values in data:
        if not isinstance(values, list) or len(values) != len(fields):
            continue
        row = dict(zip(fields, values))
        row_code = _field_value(row, "證券代號", "股票代號", "代號")
        if normalize_ticker(row_code) != code:
            continue
        return _build_institutional_row(
            row_date=row_date,
            source="TWSE T86",
            foreign_ex_dealer=_field_value(
                row,
                "外陸資買賣超股數(不含外資自營商)",
                "外資及陸資買賣超股數(不含外資自營商)",
            ),
            foreign_dealer=_field_value(row, "外資自營商買賣超股數"),
            trust=_field_value(row, "投信買賣超股數"),
            dealer=_field_value(row, "自營商買賣超股數"),
            reported_total=_field_value(row, "三大法人買賣超股數", "三大法人買賣超股數合計"),
        )
    return None


def _parse_tpex_institutional_payload(payload: Any, ticker: Any, row_date: datetime) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    tables = payload.get("tables", [])
    if not isinstance(tables, list) or not tables:
        return None
    table = tables[0] if isinstance(tables[0], dict) else {}
    data = table.get("data", [])
    raw_fields = table.get("fields", table.get("columns", []))
    fields = [_header_text(field) for field in raw_fields] if isinstance(raw_fields, list) else []
    code = normalize_ticker(ticker)
    for values in data:
        if not isinstance(values, list) or not values:
            continue
        if fields and len(fields) == len(values):
            row = dict(zip(fields, values))
            row_code = _field_value(row, "代號", "證券代號", "股票代號")
            if normalize_ticker(row_code) != code:
                continue
            # Prefer provider labels whenever they are usable.  If required
            # labels disappear, return no row instead of applying stale offsets.
            return _build_institutional_row(
                row_date=row_date,
                source="TPEx 3insti",
                foreign_ex_dealer=_field_value(
                    row,
                    "外資及陸資(不含外資自營商)買賣超股數",
                    "外資及陸資買賣超股數(不含外資自營商)",
                    "外陸資買賣超股數(不含外資自營商)",
                ),
                foreign_dealer=_field_value(row, "外資自營商買賣超股數"),
                trust=_field_value(row, "投信買賣超股數"),
                dealer=_field_value(row, "自營商買賣超股數"),
                reported_total=_field_value(
                    row, "三大法人買賣超股數", "三大法人買賣超股數合計"
                ),
            )

        # Legacy TPEx JSON did not expose field labels.  Its documented layout
        # is: foreign ex-dealer 2:5, foreign dealer 5:8, combined foreign 8:11,
        # trust 11:14, dealer 20:23, official total at 23.  Validate the total
        # so a future schema shift cannot silently be interpreted as real data.
        if len(values) < 23 or normalize_ticker(values[0]) != code:
            continue
        return _build_institutional_row(
            row_date=row_date,
            source="TPEx 3insti",
            foreign_ex_dealer=values[4],
            foreign_dealer=values[7],
            trust=values[13],
            dealer=values[22],
            reported_total=values[23] if len(values) > 23 else None,
        )
    return None


def _payload_contains_institutional_ticker(payload: Any, ticker: Any, market: str) -> bool:
    """Whether a provider payload contains the requested ticker row.

    This lets the fetcher distinguish a normal no-row response from a row that
    was rejected because required numeric fields or the schema were invalid.
    """
    code = normalize_ticker(ticker)
    if not code or not isinstance(payload, dict):
        return False
    if market == "listed":
        fields = payload.get("fields", [])
        data = payload.get("data", [])
        if not isinstance(fields, list) or not isinstance(data, list):
            return False
        for values in data:
            if not isinstance(values, list) or len(values) != len(fields):
                continue
            row = dict(zip(fields, values))
            if normalize_ticker(_field_value(row, "證券代號", "股票代號", "代號")) == code:
                return True
        return False
    tables = payload.get("tables", [])
    if not isinstance(tables, list) or not tables or not isinstance(tables[0], dict):
        return False
    return any(
        isinstance(values, list) and bool(values) and normalize_ticker(values[0]) == code
        for values in tables[0].get("data", [])
    )


def _expected_latest_institutional_date(now: datetime) -> str:
    """Most recent weekday expected after Taiwan's cash-market close.

    Exchange holidays cannot be inferred from a timestamp alone.  When a
    weekday holiday occurs, the official fallback will normally corroborate
    the same prior date; freshness metadata remains explicit for the caller.
    """
    local_now = now.astimezone(TPE) if now.tzinfo else now.replace(tzinfo=TPE)
    candidate = local_now.date()
    if local_now.weekday() >= 5 or local_now.hour < 15:
        candidate -= timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate.isoformat()


def _annotate_institutional_freshness(
    rows: list[dict[str, Any]], now: datetime
) -> tuple[list[dict[str, Any]], bool]:
    expected = _expected_latest_institutional_date(now)
    dates = sorted(
        str(row.get("date", ""))
        for row in rows
        if str(row.get("date", ""))[:10].replace("-", "").isdigit()
    )
    latest = dates[-1] if dates else ""
    stale = not latest or latest < expected
    annotated: list[dict[str, Any]] = []
    for original in rows:
        row = dict(original)
        row.update({
            "latest_date": latest,
            "expected_latest_date": expected,
            "freshness": "stale" if stale else "fresh",
            "is_stale": stale,
        })
        annotated.append(row)
    return annotated, stale


def _fetch_official_institutional_rows(
    ticker: Any,
    *,
    now: datetime,
) -> tuple[list[dict[str, Any]], str]:
    market = _market_for_ticker(ticker)
    if market not in ("listed", "otc"):
        return [], "empty"

    candidate_dates = [
        now - timedelta(days=offset)
        for offset in range(28)
        if (now - timedelta(days=offset)).weekday() < 5
    ]

    def fetch_date(row_date: datetime) -> tuple[dict[str, Any] | None, bool, bool]:
        try:
            if market == "listed":
                date_text = row_date.strftime("%Y%m%d")
                payload = _cached_json(
                    f"institutional:listed:{date_text}",
                    TWSE_INSTITUTIONAL_URL,
                    params={"date": date_text, "selectType": "ALLBUT0999", "response": "json"},
                )
                row = _parse_twse_institutional_payload(payload, ticker, row_date)
            else:
                date_text = _roc_date(row_date)
                payload = _cached_json(
                    f"institutional:otc:{date_text}",
                    TPEX_INSTITUTIONAL_URL,
                    params={
                        "l": "zh-tw", "o": "json", "se": "EW", "t": "D",
                        "d": date_text, "s": "0,asc",
                    },
                )
                row = _parse_tpex_institutional_payload(payload, ticker, row_date)
            successful = isinstance(payload, dict) and payload.get("stat") in ("OK", "ok", None)
            schema_error = row is None and _payload_contains_institutional_ticker(
                payload, ticker, market
            )
            return row, successful, schema_error
        except Exception as exc:
            logger.warning(
                "%s institutional fallback failed for %s (%s)",
                market,
                row_date.strftime("%Y-%m-%d"),
                type(exc).__name__,
            )
            return None, False, False

    normalized: list[dict[str, Any]] = []
    successful_requests = 0
    schema_errors = 0
    # Twelve weekdays normally contain ten trading sessions. Query a small second
    # batch only when holidays or provider gaps leave the first batch incomplete.
    for batch in (candidate_dates[:12], candidate_dates[12:]):
        if not batch or len(normalized) >= 10:
            break
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(fetch_date, batch))
        successful_requests += sum(1 for _, successful, _ in results if successful)
        schema_errors += sum(1 for _, _, schema_error in results if schema_error)
        normalized.extend(row for row, _, _ in results if row)
        normalized = sorted(normalized, key=lambda item: item["date"], reverse=True)[:10]
    if normalized:
        normalized, stale = _annotate_institutional_freshness(normalized, now)
        complete = len(normalized) >= 10 and not schema_errors and not stale
        return normalized, "ok" if complete else "partial"
    if schema_errors:
        return [], "partial"
    return [], "empty" if successful_requests else "error"


def _normalize_finmind_institutional_rows(
    rows: list[dict[str, Any]], *, now: datetime
) -> tuple[list[dict[str, Any]], str, bool]:
    """Normalize FinMind categories using their documented exact meanings."""
    if not rows:
        return [], "empty", True
    frame = pd.DataFrame(rows)
    required_columns = {"buy", "sell", "date", "name"}
    if not required_columns.issubset(frame.columns):
        return [], "error", True

    frame["buy"] = pd.to_numeric(frame["buy"], errors="coerce")
    frame["sell"] = pd.to_numeric(frame["sell"], errors="coerce")
    parsed_dates = pd.to_datetime(frame["date"], errors="coerce")
    invalid_rows = frame[["buy", "sell"]].isna().any(axis=1) | parsed_dates.isna()
    had_partial = bool(invalid_rows.any())
    frame = frame.loc[~invalid_rows].copy()
    if frame.empty:
        return [], "error", True
    frame["date"] = parsed_dates.loc[frame.index].dt.strftime("%Y-%m-%d")
    frame["net"] = frame["buy"] - frame["sell"]
    frame["category"] = frame["name"].astype(str).str.strip().str.lower().map({
        "foreign_investor": "foreign",
        "外資": "foreign",
        "外資及陸資": "foreign",
        "foreign_dealer_self": "foreign_dealer",
        "外資自營商": "foreign_dealer",
        "investment_trust": "trust",
        "投信": "trust",
        "dealer": "dealer_combined",
        "自營商": "dealer_combined",
        "dealer_self": "dealer_self",
        "自營商(自行買賣)": "dealer_self",
        "自營商（自行買賣）": "dealer_self",
        "dealer_hedging": "dealer_hedging",
        "自營商(避險)": "dealer_hedging",
        "自營商（避險）": "dealer_hedging",
    })

    normalized: list[dict[str, Any]] = []
    for row_date, day in frame.groupby("date"):
        known = day.dropna(subset=["category"])
        categories = set(known["category"])
        if not {"foreign", "trust"}.issubset(categories):
            had_partial = True
            continue
        has_combined = "dealer_combined" in categories
        split_categories = {"dealer_self", "dealer_hedging"} & categories
        has_complete_split = split_categories == {"dealer_self", "dealer_hedging"}
        has_any_split = bool(split_categories)
        if not has_combined and not has_any_split:
            had_partial = True
            continue
        # In the newer FinMind schema Dealer_self and Dealer_Hedging are two
        # required components.  Treating an absent component as zero creates a
        # plausible but false dealer total.  A legacy day is accepted only when
        # it contains the single documented combined Dealer row and no split
        # categories at all.
        if has_any_split and not has_complete_split:
            had_partial = True
            continue
        category_nets = known.groupby("category")["net"].sum().to_dict()
        combined_dealer = float(category_nets.get("dealer_combined", 0.0))
        split_dealer = (
            float(category_nets["dealer_self"])
            + float(category_nets["dealer_hedging"])
            if has_complete_split
            else 0.0
        )
        # FinMind documents the combined and split dealer categories as
        # mutually exclusive eras.  Non-zero values in both are ambiguous.
        if has_combined and has_complete_split and combined_dealer and split_dealer:
            had_partial = True
            continue
        dealer_shares = (
            split_dealer
            if has_complete_split and (split_dealer or not combined_dealer)
            else combined_dealer
        )
        foreign_dealer_shares = (
            float(category_nets["foreign_dealer"])
            if "foreign_dealer" in categories
            else None
        )
        item = _build_institutional_row(
            row_date=datetime.strptime(str(row_date), "%Y-%m-%d").replace(tzinfo=TPE),
            source="FinMind",
            foreign_ex_dealer=float(category_nets["foreign"]),
            foreign_dealer=foreign_dealer_shares,
            trust=float(category_nets["trust"]),
            dealer=dealer_shares,
        )
        if item is None:
            had_partial = True
            continue
        item["data_status"] = "ok"
        item["total_validation"] = "computed_from_documented_components"
        normalized.append(item)

    normalized = sorted(normalized, key=lambda item: item["date"], reverse=True)[:10]
    if not normalized:
        return [], "partial" if had_partial else "empty", True
    normalized, stale = _annotate_institutional_freshness(normalized, now)
    complete_history = len(normalized) >= 10
    return normalized, "partial" if had_partial or stale or not complete_history else "ok", stale


def fetch_institutional_rows(ticker: Any, token: str, *, now: datetime | None = None) -> tuple[list[dict[str, Any]], str]:
    now = now or datetime.now(TPE)
    if not str(token or "").strip():
        return _fetch_official_institutional_rows(ticker, now=now)

    finmind_status = "error"
    finmind_rows: list[dict[str, Any]] = []
    try:
        rows, status = _finmind_rows(
            "TaiwanStockInstitutionalInvestorsBuySell",
            ticker,
            (now - timedelta(days=20)).strftime("%Y-%m-%d"),
            token,
        )
        if not rows:
            finmind_status = status
        else:
            finmind_rows, finmind_status, stale = _normalize_finmind_institutional_rows(
                rows, now=now
            )
            if finmind_rows and not stale:
                return finmind_rows, finmind_status
    except Exception as exc:
        logger.warning("FinMind institutional request failed for %s (%s)", normalize_ticker(ticker), type(exc).__name__)
        finmind_status = "error"

    official_rows, official_status = _fetch_official_institutional_rows(ticker, now=now)
    if official_rows:
        return official_rows, official_status
    # Stale FinMind data is preferable to no history, but it is explicitly
    # partial and carries ``is_stale=True`` so callers cannot mistake it for
    # current-day chip data.
    if finmind_rows:
        return finmind_rows, "partial"
    return [], finmind_status
