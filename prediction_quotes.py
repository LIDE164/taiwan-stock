"""Dated, unadjusted quotes for prediction-list Telegram notifications.

Yahoo Chart is a public, potentially delayed source, not an exchange execution
feed.  A daily candle alone never proves either freshness or a closing price.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timezone
import math
from typing import Any
from zoneinfo import ZoneInfo

import requests

from app_security import normalize_ticker
from market_calendar import next_scheduled_session

TPE = ZoneInfo("Asia/Taipei")
SOURCE = "Yahoo Chart 1d（未調整；可能延遲）"
MAX_WORKERS = 4


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _timestamp(value: Any) -> datetime | None:
    number = _number(value)
    if number is None:
        return None
    try:
        return datetime.fromtimestamp(number, timezone.utc).astimezone(TPE)
    except (ValueError, OverflowError, OSError):
        return None


def _unavailable(symbol: str, reason: str) -> dict[str, Any]:
    return {
        "status": "unavailable", "reason": reason, "symbol": symbol,
        "open": None, "price": None, "previous_close": None,
        "previous_close_date": None, "previous_close_basis": "unadjusted_close",
        "observed_at": None, "date": None, "source": SOURCE, "final": False,
    }


def _bar_values(quote: Mapping[str, Any], index: int) -> tuple[float, float, float, float] | None:
    values: list[float] = []
    for key in ("open", "high", "low", "close"):
        series = quote.get(key)
        if not isinstance(series, list) or index >= len(series):
            return None
        number = _number(series[index])
        if number is None:
            return None
        values.append(number)
    opening, high, low, close = values
    tolerance = max(values) * 1e-7  # JSON/float noise only, not a price tick.
    if high + tolerance < max(opening, close, low) or low - tolerance > min(opening, close):
        return None
    return opening, high, low, close


def quote_from_chart_result(
    result: Any,
    *,
    symbol: str,
    now_tpe: datetime,
    closing: bool = False,
) -> dict[str, Any]:
    """Validate a provider result without adjusting prices or inventing a trade.

    A closing request stays unavailable until the dated exchange session has
    ended AND the provider's latest regular trade is at/after 13:30.  The two
    reported closing prices must agree.  A delayed 13:20 quote is never a close.
    """
    if now_tpe.tzinfo is None or now_tpe.utcoffset() is None:
        raise ValueError("now_tpe must be timezone-aware")
    now = now_tpe.astimezone(TPE)
    empty = _unavailable(symbol, "尚無可驗證的當日成交行情")
    if not isinstance(result, Mapping):
        return empty
    meta = result.get("meta")
    if not isinstance(meta, Mapping):
        return empty
    if (
        meta.get("symbol") != symbol
        or meta.get("exchangeTimezoneName") != "Asia/Taipei"
        or meta.get("currency") != "TWD"
    ):
        return _unavailable(symbol, "行情代號、時區或幣別無法驗證")
    observed = _timestamp(meta.get("regularMarketTime"))
    if observed is None or observed.date() != now.date():
        return _unavailable(symbol, "沒有當日成交時間；不以舊日收盤替代")
    if observed > now or observed.time() < time(9):
        return _unavailable(symbol, "成交時間在未來或尚未開盤")
    if observed.time() >= time(13, 31):
        return _unavailable(symbol, "行情時間已屬盤後；不混入一般交易收盤價")

    timestamps = result.get("timestamp")
    indicators = result.get("indicators")
    if not isinstance(timestamps, list) or not timestamps or not isinstance(indicators, Mapping):
        return empty
    dated = [_timestamp(value) for value in timestamps]
    if any(value is None for value in dated):
        return empty
    dates = [value for value in dated if value is not None]
    if any(first.date() >= second.date() for first, second in zip(dates, dates[1:])):
        return _unavailable(symbol, "日線時間重複或順序不明")
    if dates[-1].date() != now.date() or dates[-1] > observed:
        return _unavailable(symbol, "當日日線與成交時間不一致")
    quote_sets = indicators.get("quote")
    if not isinstance(quote_sets, list) or len(quote_sets) != 1 or not isinstance(quote_sets[0], Mapping):
        return empty
    raw_quote = quote_sets[0]
    values = _bar_values(raw_quote, len(dates) - 1)
    latest = _number(meta.get("regularMarketPrice"))
    if values is None or latest is None:
        return _unavailable(symbol, "當日 OHLC 或最新成交價缺漏／不合理")
    opening, high, low, close = values
    tolerance = max(high, latest) * 1e-7
    if latest > high + tolerance or latest < low - tolerance or abs(latest - close) > tolerance:
        return _unavailable(symbol, "日線與最新成交價尚未同步")

    quote = {
        **empty, "status": "ok", "reason": "", "open": opening,
        "price": close if closing else latest, "observed_at": observed.isoformat(),
        "date": now.date().isoformat(),
    }
    # chartPreviousClose is the value preceding the requested RANGE, not
    # necessarily yesterday.  Use only the preceding unadjusted daily candle.
    if len(dates) > 1 and next_scheduled_session(dates[-2].date()) == now.date():
        previous = _bar_values(raw_quote, len(dates) - 2)
        if previous is not None:
            quote["previous_close"] = previous[3]
            quote["previous_close_date"] = dates[-2].date().isoformat()

    if closing:
        periods = meta.get("currentTradingPeriod")
        regular = periods.get("regular") if isinstance(periods, Mapping) else None
        start = _timestamp(regular.get("start")) if isinstance(regular, Mapping) else None
        end = _timestamp(regular.get("end")) if isinstance(regular, Mapping) else None
        confirmed = (
            start is not None and end is not None
            and start.date() == now.date() == end.date()
            and start.time() == time(9) and end.time() >= time(13, 30)
            and start < end <= now and observed >= end
        )
        if not confirmed:
            quote.update(status="unavailable", price=None, reason="正式收盤尚未確認；不以延遲盤中價替代")
        else:
            quote["final"] = True
    return quote


def _symbols(record: Mapping[str, Any], ticker: str) -> tuple[str, ...]:
    supplied = str(record.get("代號") or record.get("ticker") or "").strip().upper()
    if supplied.endswith(".TWO"):
        return (f"{ticker}.TWO",)
    if supplied.endswith(".TW"):
        return (f"{ticker}.TW",)
    provenance = " ".join(str(record.get(key, "")) for key in (
        "Revenue_Source", "Institutional_Source",
    )).lower()
    if "tpex" in provenance and "twse" not in provenance:
        return (f"{ticker}.TWO",)
    if "twse" in provenance and "tpex" not in provenance:
        return (f"{ticker}.TW",)
    return (f"{ticker}.TW", f"{ticker}.TWO")


def fetch_prediction_quotes(
    records: Sequence[Mapping[str, Any]],
    *,
    now_tpe: datetime,
    closing: bool = False,
) -> dict[str, dict[str, Any]]:
    """Fetch each distinct ticker once, using at most two venue attempts.

    Public GETs have bounded connect/read timeouts and no automatic retries;
    errors are sanitized and every valid input ticker receives an explicit
    unavailable result if no dated quote can be verified.
    """
    if now_tpe.tzinfo is None or now_tpe.utcoffset() is None:
        raise ValueError("now_tpe must be timezone-aware")
    by_ticker: dict[str, Mapping[str, Any]] = {}
    for row in records:
        if not isinstance(row, Mapping):
            continue
        ticker = normalize_ticker(row.get("代號") or row.get("ticker"))
        if ticker:
            by_ticker.setdefault(ticker, row)
    if not by_ticker:
        return {}

    def fetch_one(pair: tuple[str, Mapping[str, Any]]) -> tuple[str, dict[str, Any]]:
        ticker, record = pair
        best = _unavailable("", "公開行情暫時無法取得")
        for symbol in _symbols(record, ticker):
            try:
                response = requests.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                    params={"range": "5d", "interval": "1d", "includePrePost": "false"},
                    headers={"User-Agent": "taiwan-stock-radar/1.0"}, timeout=(3, 8),
                )
                response.raise_for_status()
                payload = response.json()
                chart = payload.get("chart") if isinstance(payload, Mapping) else None
                results = chart.get("result") if isinstance(chart, Mapping) else None
                if not isinstance(results, list) or len(results) != 1:
                    continue
                parsed = quote_from_chart_result(
                    results[0], symbol=symbol, now_tpe=now_tpe, closing=closing,
                )
                # Do not lose a dated opening quote while checking unknown venue.
                if best.get("open") is None:
                    best = parsed
                if parsed["status"] == "ok" or parsed.get("open") is not None:
                    return ticker, parsed
            except Exception:
                # Never include URLs, payloads or credential-bearing exceptions.
                continue
        return ticker, best

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(by_ticker))) as executor:
        return dict(executor.map(fetch_one, by_ticker.items()))
