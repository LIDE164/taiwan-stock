"""Batch public intraday OHLCV fallback for Taiwan-listed stocks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import pandas as pd
import yfinance as yf

from app_security import normalize_ticker


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


def fetch_yahoo_intraday_quotes(
    records: Sequence[Mapping[str, Any]],
    *,
    now_tpe: datetime,
    limit: int | None = None,
    chunk_size: int = 50,
) -> dict[str, dict[str, Any]]:
    symbol_to_ticker: dict[str, str] = {}
    quote_limit = None if limit is None else max(0, int(limit))
    for record in records:
        if quote_limit is not None and len(symbol_to_ticker) >= quote_limit:
            break
        if not isinstance(record, Mapping):
            continue
        ticker = normalize_ticker(record.get("代號", ""))
        symbol = yahoo_symbol_for_record(record)
        if ticker and symbol:
            symbol_to_ticker.setdefault(symbol, ticker)

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
