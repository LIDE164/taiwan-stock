"""Shared, source-aware chip and revenue providers used by scanner and app."""

from __future__ import annotations

from datetime import datetime, timedelta
import concurrent.futures
import logging
import threading
import time
from typing import Any

import pandas as pd

from app_security import normalize_ticker
from market_http import http_get


FINMIND_DATA_URL = "https://api.finmindtrade.com/api/v4/data"
TWSE_REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
TPEX_REVENUE_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"
TWSE_INSTITUTIONAL_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
TPEX_INSTITUTIONAL_URL = (
    "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php"
)
logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 6 * 60 * 60
_CACHE_LOCK = threading.RLock()
_JSON_CACHE: dict[str, tuple[float, Any]] = {}
_KEY_LOCKS: dict[str, threading.Lock] = {}
_MARKET_INDEX: dict[str, str] = {}


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
            _JSON_CACHE[key] = (time.monotonic(), payload)
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


def _shares_to_lots(value: Any) -> int:
    shares = _number(value)
    return int(round(shares / 1000)) if shares is not None else 0


def _official_revenue_rows(market: str) -> list[dict[str, Any]]:
    url = TWSE_REVENUE_URL if market == "listed" else TPEX_REVENUE_URL
    payload = _cached_json(f"revenue:{market}", url)
    if not isinstance(payload, list):
        raise ValueError("official revenue payload is not a list")
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
    return {
        "mom": round(mom, 2) if mom is not None else None,
        "yoy": round(yoy, 2) if yoy is not None else None,
        "period": _roc_month_to_iso(row.get("資料年月")),
        "source": source,
        "status": "ok" if mom is not None and yoy is not None else "partial",
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
    now = now or datetime.now()
    # One official bulk request serves the entire tokenless scanner universe and
    # avoids consuming one FinMind public-quota request per stock.
    if not token:
        official_result = _fetch_official_revenue_growth(ticker)
        if official_result["status"] in ("ok", "partial"):
            return official_result
    finmind_result = {
        "mom": None,
        "yoy": None,
        "period": "",
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
        finmind_result = {
            "mom": round(mom, 2) if mom is not None else None,
            "yoy": round(yoy, 2) if yoy is not None else None,
            "period": period,
            "source": "FinMind",
            "status": status,
        }
    except Exception as exc:
        logger.warning("FinMind revenue request failed for %s (%s)", normalize_ticker(ticker), type(exc).__name__)
        finmind_result["status"] = "error"

    if finmind_result["status"] == "ok":
        return finmind_result
    official_result = _fetch_official_revenue_growth(ticker)
    if official_result["status"] in ("ok", "partial"):
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


def _parse_twse_institutional_payload(payload: Any, ticker: Any, row_date: datetime) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return None
    code = normalize_ticker(ticker)
    fields = payload.get("fields", [])
    data = payload.get("data", [])
    if not isinstance(fields, list) or not isinstance(data, list):
        return None
    for values in data:
        if not isinstance(values, list) or not values or normalize_ticker(values[0]) != code:
            continue
        row = dict(zip(fields, values))
        foreign = _shares_to_lots(row.get("外陸資買賣超股數(不含外資自營商)"))
        foreign += _shares_to_lots(row.get("外資自營商買賣超股數"))
        trust = _shares_to_lots(row.get("投信買賣超股數"))
        dealer = _shares_to_lots(row.get("自營商買賣超股數"))
        return {
            "date": row_date.strftime("%Y-%m-%d"),
            "foreign": foreign,
            "trust": trust,
            "dealer": dealer,
            "total": foreign + trust + dealer,
            "source": "TWSE T86",
        }
    return None


def _parse_tpex_institutional_payload(payload: Any, ticker: Any, row_date: datetime) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    tables = payload.get("tables", [])
    if not isinstance(tables, list) or not tables:
        return None
    data = tables[0].get("data", []) if isinstance(tables[0], dict) else []
    code = normalize_ticker(ticker)
    for values in data:
        if not isinstance(values, list) or len(values) < 23 or normalize_ticker(values[0]) != code:
            continue
        # TPEX groups: foreign total (8:11), trust (11:14), dealer total (20:23).
        foreign = _shares_to_lots(values[10])
        trust = _shares_to_lots(values[13])
        dealer = _shares_to_lots(values[22])
        return {
            "date": row_date.strftime("%Y-%m-%d"),
            "foreign": foreign,
            "trust": trust,
            "dealer": dealer,
            "total": foreign + trust + dealer,
            "source": "TPEx 3insti",
        }
    return None


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

    def fetch_date(row_date: datetime) -> tuple[dict[str, Any] | None, bool]:
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
            successful = isinstance(payload, dict) and payload.get("stat") in ("OK", None)
            return row, successful
        except Exception as exc:
            logger.warning(
                "%s institutional fallback failed for %s (%s)",
                market,
                row_date.strftime("%Y-%m-%d"),
                type(exc).__name__,
            )
            return None, False

    normalized: list[dict[str, Any]] = []
    successful_requests = 0
    # Twelve weekdays normally contain ten trading sessions. Query a small second
    # batch only when holidays or provider gaps leave the first batch incomplete.
    for batch in (candidate_dates[:12], candidate_dates[12:]):
        if not batch or len(normalized) >= 10:
            break
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(fetch_date, batch))
        successful_requests += sum(1 for _, successful in results if successful)
        normalized.extend(row for row, _ in results if row)
        normalized = sorted(normalized, key=lambda item: item["date"], reverse=True)[:10]
    if normalized:
        return normalized, "ok" if len(normalized) >= 10 else "partial"
    return [], "empty" if successful_requests else "error"


def fetch_institutional_rows(ticker: Any, token: str, *, now: datetime | None = None) -> tuple[list[dict[str, Any]], str]:
    now = now or datetime.now()
    finmind_status = "missing" if not token else "error"
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
            frame = pd.DataFrame(rows)
            if "buy" not in frame.columns or "sell" not in frame.columns or "date" not in frame.columns:
                finmind_status = "error"
            else:
                frame["buy"] = pd.to_numeric(frame["buy"], errors="coerce")
                frame["sell"] = pd.to_numeric(frame["sell"], errors="coerce")
                invalid_rows = frame[["buy", "sell"]].isna().any(axis=1)
                if invalid_rows.any():
                    status = "partial"
                    frame = frame[~invalid_rows].copy()
                if not frame.empty:
                    frame["net"] = (frame["buy"] - frame["sell"]) / 1000
                    frame["type"] = "其他"
                    names = frame.get("name", pd.Series("", index=frame.index)).astype(str)
                    frame.loc[names.str.contains("Dealer|自營", case=False, na=False), "type"] = "自營商"
                    # Foreign_Dealer_Self is an overseas-investor subcategory, not
                    # a domestic dealer; apply foreign/trust precedence last.
                    frame.loc[names.str.contains("Foreign|外資", case=False, na=False), "type"] = "外資"
                    frame.loc[names.str.contains("Trust|投信", case=False, na=False), "type"] = "投信"
                    pivot = frame.groupby(["date", "type"])["net"].sum().unstack(fill_value=0).reset_index()
                    for column in ("外資", "投信", "自營商"):
                        if column not in pivot.columns:
                            pivot[column] = 0
                    pivot["合計"] = pivot["外資"] + pivot["投信"] + pivot["自營商"]
                    normalized = [
                        {
                            "date": str(row["date"]),
                            "foreign": int(row["外資"]),
                            "trust": int(row["投信"]),
                            "dealer": int(row["自營商"]),
                            "total": int(row["合計"]),
                            "source": "FinMind",
                        }
                        for _, row in pivot.sort_values("date", ascending=False).head(10).iterrows()
                    ]
                    return normalized, status
                finmind_status = "error"
    except Exception as exc:
        logger.warning("FinMind institutional request failed for %s (%s)", normalize_ticker(ticker), type(exc).__name__)
        finmind_status = "error"

    official_rows, official_status = _fetch_official_institutional_rows(ticker, now=now)
    if official_rows:
        return official_rows, official_status
    return [], finmind_status if token else official_status
