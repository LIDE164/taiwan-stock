"""Batch public OHLCV loading for Taiwan-listed intraday re-scoring."""

from __future__ import annotations

import concurrent.futures
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import pandas as pd
import yfinance as yf

from app_security import normalize_ticker
from market_http import http_get


def yahoo_symbol_for_record(record: Mapping[str, Any]) -> str:
    ticker = normalize_ticker(record.get("代號", ""))
    if not ticker:
        return ""
    provenance = " ".join(
        str(record.get(key, ""))
        for key in ("Revenue_Source", "Institutional_Source")
    ).lower()
    suffix = ".TWO" if "tpex" in provenance else ".TW"
    return f"{ticker}{suffix}"


def _symbol_frame(downloaded: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if downloaded is None or downloaded.empty:
        return pd.DataFrame()
    if not isinstance(downloaded.columns, pd.MultiIndex):
        return downloaded.copy()
    level_zero = downloaded.columns.get_level_values(0)
    if symbol in level_zero:
        return downloaded[symbol].copy()
    level_one = downloaded.columns.get_level_values(1)
    if symbol in level_one:
        return downloaded.xs(symbol, axis=1, level=1).copy()
    return pd.DataFrame()


def _symbol_ticker_map(
    records: Sequence[Mapping[str, Any]],
    limit: int | None,
) -> dict[str, str]:
    symbol_to_ticker: dict[str, str] = {}
    item_limit = None if limit is None else max(0, int(limit))
    for record in records:
        if item_limit is not None and len(symbol_to_ticker) >= item_limit:
            break
        if not isinstance(record, Mapping):
            continue
        ticker = normalize_ticker(record.get("代號", ""))
        symbol = yahoo_symbol_for_record(record)
        if ticker and symbol:
            symbol_to_ticker.setdefault(symbol, ticker)
    return symbol_to_ticker


def quote_from_intraday_frame(
    frame: pd.DataFrame,
    *,
    trading_date: str,
    source: str = "Yahoo 5m",
) -> dict[str, Any] | None:
    required = {"Open", "High", "Low", "Close", "Volume"}
    if frame is None or frame.empty or not required.issubset(frame.columns):
        return None
    work = frame.copy()
    index = pd.to_datetime(work.index, errors="coerce")
    valid_index = ~index.isna()
    work = work.loc[valid_index]
    index = index[valid_index]
    if index.tz is not None:
        index = index.tz_convert("Asia/Taipei")
    date_mask = pd.Index(index.strftime("%Y-%m-%d")) == trading_date
    work = work.loc[date_mask]
    work = work.dropna(subset=["Open", "High", "Low", "Close"])
    if work.empty:
        return None

    open_price = float(work["Open"].iloc[0])
    high_price = float(work["High"].max())
    low_price = float(work["Low"].min())
    close_price = float(work["Close"].iloc[-1])
    volume = float(pd.to_numeric(work["Volume"], errors="coerce").fillna(0).sum())
    if (
        min(open_price, high_price, low_price, close_price) <= 0
        or volume <= 0
        or high_price < max(open_price, close_price)
        or low_price > min(open_price, close_price)
    ):
        return None
    typical = (
        pd.to_numeric(work["High"], errors="coerce")
        + pd.to_numeric(work["Low"], errors="coerce")
        + pd.to_numeric(work["Close"], errors="coerce")
    ) / 3
    volumes = pd.to_numeric(work["Volume"], errors="coerce").fillna(0)
    vwap = float((typical * volumes).sum() / volume) if volume > 0 else None
    return {
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "volume": volume,
        "vwap": vwap,
        "date": trading_date,
        "source": source,
    }


def merge_intraday_quote_into_history(
    history: pd.DataFrame,
    quote: Mapping[str, Any],
    *,
    trading_date: str,
) -> pd.DataFrame | None:
    """Replace today's daily bar with a verified intraday aggregate."""
    required_history = {"Open", "High", "Low", "Close", "Volume"}
    required_quote = ("open", "high", "low", "close", "volume")
    if history is None or history.empty or not required_history.issubset(history.columns):
        return None
    if not isinstance(quote, Mapping) or any(quote.get(key) is None for key in required_quote):
        return None
    if quote.get("date") and str(quote.get("date")) != trading_date:
        return None
    try:
        open_price = float(quote["open"])
        high_price = float(quote["high"])
        low_price = float(quote["low"])
        close_price = float(quote["close"])
        volume = float(quote["volume"])
    except (TypeError, ValueError):
        return None
    if (
        min(open_price, high_price, low_price, close_price, volume) <= 0
        or high_price < max(open_price, close_price)
        or low_price > min(open_price, close_price)
    ):
        return None

    work = history.copy()
    index = pd.to_datetime(work.index, errors="coerce")
    valid_index = ~index.isna()
    work = work.loc[valid_index]
    index = index[valid_index]
    if index.tz is not None:
        index = index.tz_convert("Asia/Taipei").tz_localize(None)
    work.index = index.normalize()
    work = work[~work.index.duplicated(keep="last")]
    live_date = pd.Timestamp(trading_date)
    work.loc[live_date, ["Open", "High", "Low", "Close", "Volume"]] = [
        open_price,
        high_price,
        low_price,
        close_price,
        volume,
    ]
    try:
        vwap = float(quote.get("vwap"))
    except (TypeError, ValueError):
        vwap = 0.0
    if 0 < vwap < close_price * 2:
        work.loc[live_date, "VWAP"] = vwap
    return work.sort_index()


def history_and_quote_from_chart_result(
    result: Mapping[str, Any],
    *,
    trading_date: str,
    source: str = "Yahoo Chart 1d（延遲行情）",
) -> tuple[pd.DataFrame | None, dict[str, Any] | None]:
    """Parse Yahoo Chart JSON without yfinance's expensive per-symbol metadata work."""
    timestamps = list(result.get("timestamp") or []) if isinstance(result, Mapping) else []
    indicators = result.get("indicators") or {} if isinstance(result, Mapping) else {}
    quote_sets = indicators.get("quote") or [] if isinstance(indicators, Mapping) else []
    if not timestamps or not quote_sets or not isinstance(quote_sets[0], Mapping):
        return None, None
    raw_quote = quote_sets[0]
    fields = {
        "Open": list(raw_quote.get("open") or []),
        "High": list(raw_quote.get("high") or []),
        "Low": list(raw_quote.get("low") or []),
        "Close": list(raw_quote.get("close") or []),
        "Volume": list(raw_quote.get("volume") or []),
    }
    row_count = min([len(timestamps), *(len(values) for values in fields.values())])
    if row_count < 20:
        return None, None
    index = pd.to_datetime(timestamps[:row_count], unit="s", utc=True)
    index = index.tz_convert("Asia/Taipei").tz_localize(None).normalize()
    frame = pd.DataFrame(
        {name: pd.to_numeric(values[:row_count], errors="coerce") for name, values in fields.items()},
        index=index,
    )

    adj_sets = indicators.get("adjclose") or [] if isinstance(indicators, Mapping) else []
    if adj_sets and isinstance(adj_sets[0], Mapping):
        adjusted = pd.to_numeric(
            list(adj_sets[0].get("adjclose") or [])[:row_count],
            errors="coerce",
        )
        adjusted_series = pd.Series(adjusted, index=frame.index)
        factor = adjusted_series / frame["Close"]
        valid_factor = factor.where(factor.gt(0) & factor.lt(10), 1.0).fillna(1.0)
        for column in ("Open", "High", "Low", "Close"):
            frame[column] = frame[column] * valid_factor

    frame = frame.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    if len(frame) < 20 or frame.index[-1].strftime("%Y-%m-%d") != trading_date:
        return None, None
    latest = frame.iloc[-1]
    open_price = float(latest["Open"])
    high_price = float(latest["High"])
    low_price = float(latest["Low"])
    close_price = float(latest["Close"])
    volume = float(latest["Volume"])
    if (
        min(open_price, high_price, low_price, close_price, volume) <= 0
        or high_price < max(open_price, close_price)
        or low_price > min(open_price, close_price)
    ):
        return None, None
    return frame, {
        "date": trading_date,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "volume": volume,
        "vwap": None,
        "source": source,
    }


def fetch_yahoo_live_history_bundle(
    records: Sequence[Mapping[str, Any]],
    *,
    now_tpe: datetime,
    max_workers: int = 20,
) -> tuple[dict[str, dict[str, Any]], dict[str, pd.DataFrame]]:
    """Fetch current daily bars and six months of history concurrently via Chart JSON."""
    symbol_to_ticker = _symbol_ticker_map(records, None)
    trading_date = now_tpe.strftime("%Y-%m-%d")

    def fetch_one(symbol: str):
        try:
            response = http_get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                params={
                    "range": "6mo",
                    "interval": "1d",
                    "includePrePost": "false",
                    "events": "div,splits",
                },
                timeout=8,
            )
            response.raise_for_status()
            chart = response.json().get("chart", {})
            result = (chart.get("result") or [None])[0]
            if not isinstance(result, Mapping):
                return symbol, None, None
            history, quote = history_and_quote_from_chart_result(
                result,
                trading_date=trading_date,
            )
            return symbol, history, quote
        except Exception:
            return symbol, None, None

    quotes: dict[str, dict[str, Any]] = {}
    histories: dict[str, pd.DataFrame] = {}
    symbols = list(symbol_to_ticker)
    worker_count = min(max(1, int(max_workers)), max(1, len(symbols)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        for symbol, history, quote in executor.map(fetch_one, symbols):
            ticker = symbol_to_ticker[symbol]
            if history is not None and quote is not None:
                histories[ticker] = history
                quotes[ticker] = quote
    return quotes, histories


def fetch_yahoo_history_frames(
    records: Sequence[Mapping[str, Any]],
    *,
    limit: int | None = None,
    chunk_size: int = 200,
) -> dict[str, pd.DataFrame]:
    """Load enough daily bars for 60-day indicators in bounded batches."""
    symbol_to_ticker = _symbol_ticker_map(records, limit)
    histories: dict[str, pd.DataFrame] = {}
    symbols = list(symbol_to_ticker)
    effective_chunk = max(1, int(chunk_size))
    for start in range(0, len(symbols), effective_chunk):
        chunk = symbols[start:start + effective_chunk]
        try:
            downloaded = yf.download(
                chunk,
                period="6mo",
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
                timeout=12,
            )
        except Exception:
            continue
        for symbol in chunk:
            frame = _symbol_frame(downloaded, symbol)
            if frame.empty or not {"Open", "High", "Low", "Close", "Volume"}.issubset(frame.columns):
                continue
            frame = frame.dropna(subset=["Open", "High", "Low", "Close"])
            if len(frame) < 20:
                continue
            index = pd.to_datetime(frame.index, errors="coerce")
            valid_index = ~index.isna()
            frame = frame.loc[valid_index]
            index = index[valid_index]
            if index.tz is not None:
                index = index.tz_convert("Asia/Taipei").tz_localize(None)
            frame.index = index.normalize()
            histories[symbol_to_ticker[symbol]] = frame[~frame.index.duplicated(keep="last")]
    return histories


def fetch_yahoo_intraday_quotes(
    records: Sequence[Mapping[str, Any]],
    *,
    now_tpe: datetime,
    limit: int | None = None,
    chunk_size: int = 200,
) -> dict[str, dict[str, Any]]:
    symbol_to_ticker = _symbol_ticker_map(records, limit)

    quotes: dict[str, dict[str, Any]] = {}
    symbols = list(symbol_to_ticker)
    trading_date = now_tpe.strftime("%Y-%m-%d")
    for start in range(0, len(symbols), max(1, int(chunk_size))):
        chunk = symbols[start:start + max(1, int(chunk_size))]
        try:
            downloaded = yf.download(
                chunk,
                period="1d",
                interval="5m",
                group_by="ticker",
                auto_adjust=False,
                threads=True,
                progress=False,
                timeout=12,
            )
        except Exception:
            continue
        for symbol in chunk:
            quote = quote_from_intraday_frame(
                _symbol_frame(downloaded, symbol),
                trading_date=trading_date,
            )
            if quote:
                quotes[symbol_to_ticker[symbol]] = quote
    return quotes
