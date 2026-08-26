"""Single-stock live quote loading for the analysis page."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from app_security import normalize_ticker
from intraday_quotes import fetch_yahoo_live_history_bundle
from market_http import http_get


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _valid_quote(values: Mapping[str, Any]) -> dict[str, float] | None:
    parsed = {key: _number(values.get(key)) for key in ("open", "high", "low", "close", "volume")}
    if any(value is None for value in parsed.values()):
        return None
    if parsed["high"] < max(parsed["open"], parsed["close"]):
        return None
    if parsed["low"] > min(parsed["open"], parsed["close"]):
        return None
    vwap = _number(values.get("vwap"))
    if vwap is not None and vwap < parsed["close"] * 2:
        parsed["vwap"] = vwap
    else:
        parsed["vwap"] = None
    return parsed


def _fugle_quote(ticker: str, api_key: str) -> dict[str, Any] | None:
    if not api_key:
        return None
    response = http_get(
        f"https://api.fugle.tw/marketdata/v1.0/stock/intraday/quote/{ticker}",
        headers={"X-API-KEY": api_key},
        timeout=5,
    )
    response.raise_for_status()
    payload = response.json()
    total = payload.get("total", {}) or {}
    volume = total.get("tradeVolume")
    trade_value = _number(total.get("tradeValue", total.get("tradeValueAmount")))
    normalized = _valid_quote({
        "close": payload.get("closePrice", payload.get("lastPrice")),
        "open": payload.get("openPrice"),
        "high": payload.get("highPrice"),
        "low": payload.get("lowPrice"),
        "volume": volume,
        "vwap": trade_value / float(volume) if trade_value is not None and _number(volume) else None,
    })
    return normalized


def _yahoo_records(ticker: str, record: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    baseline = dict(record or {})
    baseline["代號"] = ticker
    provenance = " ".join(
        str(baseline.get(key, "")) for key in ("Revenue_Source", "Institutional_Source")
    ).lower()
    alternate = {"代號": ticker}
    if "tpex" in provenance:
        alternate["Revenue_Source"] = "TWSE OpenAPI"
    else:
        alternate["Revenue_Source"] = "TPEx OpenAPI"
    return [baseline, alternate]


def fetch_analysis_live_quote(
    ticker: Any,
    record: Mapping[str, Any] | None,
    *,
    api_key: str,
    now_tpe: datetime,
) -> dict[str, Any] | None:
    """Use Fugle first and Yahoo delayed daily chart data as the fallback."""
    code = normalize_ticker(ticker)
    if not code:
        return None
    fetched_at = now_tpe.strftime("%Y-%m-%d %H:%M:%S")
    if api_key:
        try:
            quote = _fugle_quote(code, api_key)
            if quote:
                return {
                    **quote,
                    "date": now_tpe.strftime("%Y-%m-%d"),
                    "source": "Fugle",
                    "quote_time": fetched_at,
                    "freshness": "即時行情",
                }
        except Exception:
            pass

    quotes, _ = fetch_yahoo_live_history_bundle(
        _yahoo_records(code, record),
        now_tpe=now_tpe,
        max_workers=2,
    )
    quote = _valid_quote(quotes.get(code, {}))
    if not quote:
        return None
    return {
        **quote,
        "date": now_tpe.strftime("%Y-%m-%d"),
        "source": "Yahoo Chart 1d",
        "quote_time": fetched_at,
        "freshness": "延遲行情",
    }
