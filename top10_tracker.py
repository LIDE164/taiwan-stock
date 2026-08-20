"""Pure Top-10 position tracking using daily OHLC bars."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date, datetime
from typing import Any


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _quote(row: Mapping[str, Any] | None) -> dict[str, float] | None:
    """Return a complete, internally consistent OHLC bar or no quote at all."""
    if not isinstance(row, Mapping):
        return None
    aliases = {
        "open": ("開盤價", "Open", "open"),
        "high": ("最高價", "High", "high"),
        "low": ("最低價", "Low", "low"),
        "close": ("收盤價", "Close", "close"),
    }
    quote: dict[str, float] = {}
    for field, keys in aliases.items():
        raw = next((row.get(key) for key in keys if key in row), None)
        try:
            value = float(str(raw).replace(",", ""))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value) or value <= 0:
            return None
        quote[field] = value
    if quote["high"] < max(quote["open"], quote["close"]):
        return None
    if quote["low"] > min(quote["open"], quote["close"]):
        return None
    if quote["low"] > quote["high"]:
        return None
    return quote


def _position_id(position: Mapping[str, Any]) -> str:
    existing = str(position.get("position_id", "")).strip()
    if existing:
        return existing
    return f"{position.get('ticker', '')}:{position.get('entry_date', '')}"


def _rank(row: Mapping[str, Any] | None) -> int | None:
    value = (row or {}).get("Rank")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _snapshot(
    position: Mapping[str, Any],
    trading_date: str,
    row: Mapping[str, Any] | None,
    bar: Mapping[str, float] | None,
    *,
    previous_mark: float = 0.0,
    action: str,
    data_status: str = "ok",
    take_profit_pct: float = 15.0,
    stop_loss_pct: float = 10.0,
) -> dict[str, Any]:
    entry = _number(position.get("entry_price"))
    mark = _number(position.get("current_price"), entry)
    highest = _number(position.get("highest_price"), entry)
    lowest = _number(position.get("lowest_price"), entry)
    daily_return = round((mark / previous_mark - 1) * 100, 2) if previous_mark > 0 else None
    quote = dict(bar or {})
    source = row or {}
    return {
        "date": trading_date,
        "position_id": _position_id(position),
        "ticker": str(position.get("ticker", "")),
        "name": str(position.get("name", position.get("ticker", ""))),
        "entry_date": str(position.get("entry_date", "")),
        "entry_price": round(entry, 4),
        "open": round(quote["open"], 4) if quote else None,
        "high": round(quote["high"], 4) if quote else None,
        "low": round(quote["low"], 4) if quote else None,
        "close": round(quote["close"], 4) if quote else None,
        "mark_price": round(mark, 4),
        "daily_return_pct": daily_return,
        "pnl_pct": round(_number(position.get("pnl_pct")), 2),
        "highest_price": round(highest, 4),
        "lowest_price": round(lowest, 4),
        "mfe_pct": round((highest / entry - 1) * 100, 2) if entry > 0 else None,
        "mae_pct": round((lowest / entry - 1) * 100, 2) if entry > 0 else None,
        "target_price": round(entry * (1 + take_profit_pct / 100), 4) if entry > 0 else None,
        "stop_price": round(entry * (1 - stop_loss_pct / 100), 4) if entry > 0 else None,
        "status": str(position.get("status", "")),
        "action": action,
        "close_date": position.get("close_date"),
        "close_price": position.get("close_price"),
        "is_top10": bool(row),
        "top10_rank": _rank(row),
        "score": _number(source.get("Score")) if source.get("Score") is not None else None,
        "data_status": data_status,
    }


def _firestore_value(value: Any) -> Any:
    """Convert pandas/numpy-like scalars and nested values into Firestore-safe data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _firestore_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_firestore_value(item) for item in value]
    if hasattr(value, "item"):
        try:
            return _firestore_value(value.item())
        except (TypeError, ValueError):
            pass
    return str(value)


def build_top10_history_rows(top10_results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep the complete daily ranking record instead of a four-field summary."""
    rows: list[dict[str, Any]] = []
    for position, row in enumerate(top10_results, start=1):
        cleaned = _firestore_value(dict(row))
        if not isinstance(cleaned, dict):
            continue
        cleaned["Rank"] = int(cleaned.get("Rank") or position)
        rows.append(cleaned)
    return rows


def update_positions_with_snapshots(
    positions: Sequence[Mapping[str, Any]],
    top10_results: Sequence[Mapping[str, Any]],
    quotes: Mapping[str, Mapping[str, Any]],
    trading_date: str,
    *,
    take_profit_pct: float = 15.0,
    stop_loss_pct: float = 10.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Update positions and return one idempotent daily record for every tracked name."""
    # A forced same-day rerun rebuilds today's close entries from the latest Top-10.
    updated = [
        deepcopy(dict(position))
        for position in positions
        if isinstance(position, Mapping)
        and not (
            str(position.get("entry_date", "")) == trading_date
            and position.get("status") == "OPEN"
        )
    ]
    top_by_ticker = {str(row.get("代號", "")): dict(row) for row in top10_results if row.get("代號")}
    quote_by_ticker = {str(key): dict(value) for key, value in quotes.items()}
    for ticker, top_quote_row in top_by_ticker.items():
        if _quote(top_quote_row) is not None:
            quote_by_ticker[ticker] = top_quote_row
    snapshots: list[dict[str, Any]] = []

    for position in updated:
        if position.get("status") != "OPEN" and str(position.get("close_date", "")) != trading_date:
            position.pop("last_snapshot", None)
        position["position_id"] = _position_id(position)
        ticker = str(position.get("ticker", ""))
        top_row = top_by_ticker.get(ticker)

        if str(position.get("last_tracked_date", "")) == trading_date:
            previous_snapshot = position.get("last_snapshot")
            if isinstance(previous_snapshot, Mapping):
                snapshot = deepcopy(dict(previous_snapshot))
                snapshot.update({
                    "is_top10": bool(top_row),
                    "top10_rank": _rank(top_row),
                    "score": _number(top_row.get("Score")) if top_row and top_row.get("Score") is not None else None,
                })
                position["last_snapshot"] = snapshot
                snapshots.append(snapshot)
            continue

        if position.get("status") != "OPEN":
            if str(position.get("close_date", "")) == trading_date:
                snapshot = _snapshot(
                    position,
                    trading_date,
                    top_row,
                    None,
                    action="EXIT",
                    data_status="legacy_partial",
                    take_profit_pct=take_profit_pct,
                    stop_loss_pct=stop_loss_pct,
                )
                position["last_tracked_date"] = trading_date
                position["last_snapshot"] = snapshot
                snapshots.append(snapshot)
            continue

        raw_bar = quote_by_ticker.get(ticker)
        bar = _quote(raw_bar)
        entry = _number(position.get("entry_price"))
        previous_mark = _number(position.get("current_price"), entry)
        if entry <= 0 or bar is None:
            snapshot = _snapshot(
                position,
                trading_date,
                top_row,
                None,
                previous_mark=previous_mark,
                action="DATA_MISSING",
                data_status="missing",
                take_profit_pct=take_profit_pct,
                stop_loss_pct=stop_loss_pct,
            )
            position["last_tracked_date"] = trading_date
            position["last_snapshot"] = snapshot
            snapshots.append(snapshot)
            continue

        target = entry * (1 + take_profit_pct / 100)
        stop = entry * (1 - stop_loss_pct / 100)
        position["highest_price"] = max(_number(position.get("highest_price"), entry), bar["high"])
        position["lowest_price"] = min(_number(position.get("lowest_price"), entry), bar["low"])
        position["current_price"] = bar["close"]

        close_price = None
        close_status = None
        if bar["open"] <= stop:
            close_price, close_status = bar["open"], "CLOSED_SL"
        elif bar["open"] >= target:
            close_price, close_status = bar["open"], "CLOSED_TP"
        else:
            hit_stop = bar["low"] <= stop
            hit_target = bar["high"] >= target
            if hit_stop:
                close_price, close_status = stop, "CLOSED_SL"
            elif hit_target:
                close_price, close_status = target, "CLOSED_TP"

        mark = close_price if close_price is not None else bar["close"]
        position["pnl_pct"] = round((mark / entry - 1) * 100, 2)
        action = "HOLD"
        if close_status and close_price is not None:
            action = "TAKE_PROFIT" if close_status == "CLOSED_TP" else "STOP_LOSS"
            position.update({
                "status": close_status,
                "close_date": trading_date,
                "close_price": round(close_price, 4),
                "current_price": round(close_price, 4),
            })
        snapshot = _snapshot(
            position,
            trading_date,
            top_row,
            bar,
            previous_mark=previous_mark,
            action=action,
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
        )
        position["last_tracked_date"] = trading_date
        position["last_snapshot"] = snapshot
        snapshots.append(snapshot)

    blocked_today = {
        str(position.get("ticker", ""))
        for position in updated
        if position.get("status") == "OPEN" or str(position.get("close_date", "")) == trading_date
    }
    for rank, row in enumerate(top10_results, start=1):
        ticker = str(row.get("代號", ""))
        bar = _quote(row)
        if not ticker or ticker in blocked_today or bar is None:
            continue
        price = bar["close"]
        position = {
            "position_id": f"{ticker}:{trading_date}",
            "ticker": ticker,
            "name": str(row.get("名稱", ticker)),
            "entry_date": trading_date,
            "entry_price": price,
            "status": "OPEN",
            "close_date": None,
            "close_price": None,
            "highest_price": price,
            "lowest_price": price,
            "current_price": price,
            "pnl_pct": 0.0,
        }
        ranked_row = dict(row)
        ranked_row.setdefault("Rank", rank)
        snapshot = _snapshot(
            position,
            trading_date,
            ranked_row,
            bar,
            action="ENTRY",
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
        )
        position["last_tracked_date"] = trading_date
        position["last_snapshot"] = snapshot
        updated.append(position)
        snapshots.append(snapshot)
        blocked_today.add(ticker)
    return updated, snapshots


def update_positions(
    positions: Sequence[Mapping[str, Any]],
    top10_results: Sequence[Mapping[str, Any]],
    quotes: Mapping[str, Mapping[str, Any]],
    trading_date: str,
    *,
    take_profit_pct: float = 15.0,
    stop_loss_pct: float = 10.0,
) -> list[dict[str, Any]]:
    """Settle old positions, then enter new Top-10 names at today's close.

    If both thresholds occur in one OHLC bar, the stop is applied first because
    daily bars cannot reveal intraday ordering. A position entered at today's
    close is never evaluated against today's earlier high/low and cannot be
    reopened on the same trading date after closing.
    """
    updated, _ = update_positions_with_snapshots(
        positions,
        top10_results,
        quotes,
        trading_date,
        take_profit_pct=take_profit_pct,
        stop_loss_pct=stop_loss_pct,
    )
    return updated
