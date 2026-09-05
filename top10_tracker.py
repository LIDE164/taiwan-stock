"""Pure Top-10 position tracking using daily OHLC bars."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date, datetime
from typing import Any


TRACKER_EXECUTION_SCHEMA = 2
PER_POSITION_MAX_RISK = 5000.0
BUY_COMMISSION_RATE = 0.001425
SELL_COMMISSION_RATE = 0.001425
SELL_TAX_RATE = 0.003
MIN_COMMISSION = 20.0


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _optional_number(value: Any) -> float | None:
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _schema_version(position: Mapping[str, Any], default: int = 1) -> int:
    parsed = _optional_number(position.get("execution_schema"))
    return int(parsed) if parsed is not None and parsed >= 0 else default


def _entry_backtest_snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the technical backtest that existed when a name entered Top-10."""
    raw_samples = _optional_number(row.get("Backtest_Samples"))
    samples = int(raw_samples) if raw_samples is not None and raw_samples >= 0 else None
    raw_win_rate = _optional_number(row.get("WinRate"))
    win_rate = (
        round(raw_win_rate, 2)
        if samples is not None and samples > 0 and raw_win_rate is not None and 0 <= raw_win_rate <= 100
        else None
    )
    scope = str(row.get("Backtest_Scope") or "").strip()
    status = "ok" if win_rate is not None else ("no_samples" if samples == 0 else "missing")
    return {
        "entry_win_rate": win_rate,
        "entry_backtest_samples": samples,
        "entry_backtest_scope": scope or None,
        "entry_backtest_status": status,
    }


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
    origin_date = position.get("signal_date") or position.get("entry_date", "")
    return f"{position.get('ticker', '')}:{origin_date}"


def _position_levels(
    position: Mapping[str, Any],
    *,
    take_profit_pct: float,
    stop_loss_pct: float,
) -> tuple[float, float]:
    """Return immutable strategy levels for new positions and legacy fallbacks."""
    entry = _number(position.get("entry_price"))
    if _schema_version(position, 0) >= TRACKER_EXECUTION_SCHEMA:
        target = _optional_number(position.get("target_price"))
        stop = _optional_number(position.get("stop_price"))
        if (
            target is not None and stop is not None and stop > 0 and target > stop
            and (entry <= 0 or stop < entry < target)
        ):
            return target, stop
    return entry * (1 + take_profit_pct / 100), entry * (1 - stop_loss_pct / 100)


def _execution_metrics(position: Mapping[str, Any], mark: float) -> dict[str, Any]:
    """Estimate realizable P/L including Taiwan stock fees and sell tax."""
    entry = _number(position.get("entry_price"))
    shares = max(0, int(_number(position.get("shares"), 0)))
    if entry <= 0 or mark <= 0 or shares <= 0:
        return {
            "shares": shares or None,
            "entry_notional": None,
            "gross_pnl_amount": None,
            "estimated_transaction_cost": None,
            "net_pnl_amount": None,
            "net_pnl_pct": None,
        }
    entry_notional = entry * shares
    exit_notional = mark * shares
    buy_fee = max(entry_notional * BUY_COMMISSION_RATE, MIN_COMMISSION)
    sell_fee = max(exit_notional * SELL_COMMISSION_RATE, MIN_COMMISSION)
    sell_tax = exit_notional * SELL_TAX_RATE
    gross_pnl = exit_notional - entry_notional
    total_cost = buy_fee + sell_fee + sell_tax
    net_pnl = gross_pnl - total_cost
    return {
        "shares": shares,
        "entry_notional": round(entry_notional, 2),
        "gross_pnl_amount": round(gross_pnl, 2),
        "estimated_transaction_cost": round(total_cost, 2),
        "net_pnl_amount": round(net_pnl, 2),
        "net_pnl_pct": round(net_pnl / (entry_notional + buy_fee) * 100, 2),
    }


def _decline_diagnostic(
    position: Mapping[str, Any],
    bar: Mapping[str, float] | None,
    *,
    action: str,
    previous_mark: float,
) -> str | None:
    """Describe an observed price path; never claim an unobservable cause."""
    if action == "STOP_LOSS":
        return "觸發策略停損"
    if not bar or previous_mark <= 0 or bar["close"] >= previous_mark:
        return None
    gap_pct = (bar["open"] / previous_mark - 1) * 100
    intraday_pct = (bar["close"] / bar["open"] - 1) * 100
    signal_change = _optional_number(position.get("signal_change_pct"))
    observations: list[str] = []
    if gap_pct <= -2:
        observations.append(f"跳空走弱 {gap_pct:.1f}%")
    elif intraday_pct <= -2:
        observations.append(f"盤中賣壓 {intraday_pct:.1f}%")
    if signal_change is not None and signal_change >= 3:
        observations.append(f"入榜日已漲 {signal_change:.1f}%")
    return "｜".join(observations[:2]) or "收盤較前日走弱"


def _attach_benchmark(
    snapshot: dict[str, Any],
    benchmark: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Attach same-day market context and observable relative performance."""
    context = benchmark if isinstance(benchmark, Mapping) else {}
    benchmark_return = _optional_number(
        context.get("daily_return_pct", context.get("benchmark_return_pct"))
    )
    benchmark_close = _optional_number(context.get("close", context.get("benchmark_close")))
    snapshot["benchmark_symbol"] = str(context.get("symbol") or "^TWII") if context else None
    snapshot["benchmark_close"] = round(benchmark_close, 2) if benchmark_close is not None else None
    snapshot["benchmark_return_pct"] = (
        round(benchmark_return, 2) if benchmark_return is not None else None
    )
    snapshot["market_regime"] = str(context.get("regime") or "") or None
    daily_return = _optional_number(snapshot.get("daily_return_pct"))
    snapshot["excess_return_pct"] = (
        round(daily_return - benchmark_return, 2)
        if daily_return is not None and benchmark_return is not None
        else None
    )
    if daily_return is not None and daily_return < 0 and benchmark_return is not None:
        current = str(snapshot.get("decline_diagnostic") or "")
        relative_observation = ""
        if benchmark_return <= -1:
            relative_observation = f"大盤同步 {benchmark_return:.1f}%"
        elif daily_return - benchmark_return <= -2:
            relative_observation = f"弱於大盤 {abs(daily_return - benchmark_return):.1f}%"
        if relative_observation:
            if current == "收盤較前日走弱":
                snapshot["decline_diagnostic"] = relative_observation
            elif relative_observation not in current and len(current.split("｜")) < 2:
                snapshot["decline_diagnostic"] = f"{current}｜{relative_observation}"
    return snapshot


_SIGNAL_SNAPSHOT_FIELDS = (
    "Score", "Rank", "Overall_Rank", "漲跌幅", "產業", "Entry_Status",
    "Entry_Plan_Type", "Entry_Low", "Entry_High", "Entry_Stop", "Entry_Target",
    "No_Chase_Price", "Entry_Reason", "Entry_Pattern", "Signal_Conflict",
    "RSI", "BIAS", "ATR", "Est_Vol_Ratio", "Volume_Confirmed", "Confidence",
    "Data_Quality", "WinRate", "Backtest_Samples", "Backtest_Scope",
    "Validation_WinRate", "Validation_Samples", "Reasons", "Feature",
    "Market_Regime", "Market_Return",
)


def _signal_snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    return _firestore_value({key: row.get(key) for key in _SIGNAL_SNAPSHOT_FIELDS if key in row})


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
    highest = _optional_number(position.get("highest_price"))
    lowest = _optional_number(position.get("lowest_price"))
    if entry > 0:
        highest = highest if highest is not None and highest > 0 else entry
        lowest = lowest if lowest is not None and lowest > 0 else entry
    has_daily_price = data_status == "ok" and bar is not None
    daily_return = (
        round((mark / previous_mark - 1) * 100, 2)
        if has_daily_price and previous_mark > 0
        else None
    )
    daily_price_change = (
        round(mark - previous_mark, 4)
        if has_daily_price and previous_mark > 0
        else None
    )
    quote = dict(bar or {})
    source = row or {}
    target, stop = _position_levels(
        position,
        take_profit_pct=take_profit_pct,
        stop_loss_pct=stop_loss_pct,
    )
    result = {
        "date": trading_date,
        "position_id": _position_id(position),
        "ticker": str(position.get("ticker", "")),
        "name": str(position.get("name", position.get("ticker", ""))),
        "signal_date": str(position.get("signal_date") or position.get("entry_date", "")),
        "entry_date": str(position.get("entry_date") or ""),
        "entry_price": round(entry, 4) if entry > 0 else None,
        "execution_schema": _schema_version(position),
        "entry_plan_type": position.get("entry_plan_type"),
        "planned_entry_low": position.get("planned_entry_low"),
        "planned_entry_high": position.get("planned_entry_high"),
        "entry_win_rate": position.get("entry_win_rate"),
        "entry_backtest_samples": position.get("entry_backtest_samples"),
        "entry_backtest_scope": position.get("entry_backtest_scope"),
        "entry_backtest_status": str(position.get("entry_backtest_status", "missing")),
        "open": round(quote["open"], 4) if quote else None,
        "high": round(quote["high"], 4) if quote else None,
        "low": round(quote["low"], 4) if quote else None,
        "close": round(quote["close"], 4) if quote else None,
        "mark_price": round(mark, 4) if mark > 0 else None,
        "previous_mark_price": round(previous_mark, 4) if previous_mark > 0 else None,
        "daily_price_change": daily_price_change,
        "daily_return_pct": daily_return,
        "pnl_pct": round(_number(position.get("pnl_pct")), 2) if entry > 0 else None,
        "highest_price": round(highest, 4) if highest is not None and highest > 0 else None,
        "lowest_price": round(lowest, 4) if lowest is not None and lowest > 0 else None,
        "mfe_pct": round((highest / entry - 1) * 100, 2) if entry > 0 and highest is not None else None,
        "mae_pct": round((lowest / entry - 1) * 100, 2) if entry > 0 and lowest is not None else None,
        "target_price": round(target, 4) if target > 0 else None,
        "stop_price": round(stop, 4) if stop > 0 else None,
        "status": str(position.get("status", "")),
        "action": action,
        "close_date": position.get("close_date"),
        "close_price": position.get("close_price"),
        "is_top10": bool(row),
        "top10_rank": _rank(row),
        "score": _number(source.get("Score")) if source.get("Score") is not None else None,
        "signal_score": position.get("signal_score"),
        "signal_rank": position.get("signal_rank"),
        "signal_change_pct": position.get("signal_change_pct"),
        "signal_industry": position.get("signal_industry"),
        "signal_rsi": position.get("signal_rsi"),
        "signal_bias": position.get("signal_bias"),
        "signal_volume_ratio": position.get("signal_volume_ratio"),
        "signal_conflict": position.get("signal_conflict"),
        "signal_pattern": position.get("signal_pattern"),
        "signal_confidence": position.get("signal_confidence"),
        "signal_snapshot": deepcopy(position.get("signal_snapshot", {})),
        "decline_diagnostic": _decline_diagnostic(
            position,
            bar,
            action=action,
            previous_mark=previous_mark,
        ),
        "data_status": data_status,
    }
    metrics = _execution_metrics(position, mark)
    if data_status != "ok":
        for key in (
            "entry_notional", "gross_pnl_amount", "estimated_transaction_cost",
            "net_pnl_amount", "net_pnl_pct",
        ):
            metrics[key] = None
    result.update(metrics)
    return result


def _firestore_value(value: Any) -> Any:
    """Convert pandas/numpy-like scalars and nested values into Firestore-safe data."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
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


def restore_entry_positions_from_history(
    positions: Sequence[Mapping[str, Any]],
    history_rows: Sequence[Mapping[str, Any]],
    entry_date: str,
) -> tuple[list[dict[str, Any]], int]:
    """Restore the pre-schema-2 baseline without rewriting historical results."""
    updated = [deepcopy(dict(position)) for position in positions if isinstance(position, Mapping)]
    existing_ids = {_position_id(position) for position in updated}
    added = 0
    for row in history_rows:
        if not isinstance(row, Mapping):
            continue
        ticker = str(row.get("代號", "")).strip()
        bar = _quote(row)
        position_id = f"{ticker}:{entry_date}"
        if not ticker or bar is None or position_id in existing_ids:
            continue
        price = bar["close"]
        position = {
            "position_id": position_id,
            "ticker": ticker,
            "name": str(row.get("名稱", ticker)),
            "signal_date": str(entry_date),
            "entry_date": str(entry_date),
            "entry_price": price,
            "status": "OPEN",
            "close_date": None,
            "close_price": None,
            "highest_price": price,
            "lowest_price": price,
            "current_price": price,
            "pnl_pct": 0.0,
            "execution_schema": 1,
            "signal_score": _optional_number(row.get("Score")),
            "signal_rank": _rank(row),
            "signal_change_pct": _optional_number(row.get("漲跌幅")),
            "signal_industry": str(row.get("產業") or ""),
            "signal_rsi": _optional_number(row.get("RSI")),
            "signal_bias": _optional_number(row.get("BIAS")),
            "signal_volume_ratio": _optional_number(row.get("Est_Vol_Ratio")),
            "signal_conflict": str(row.get("Signal_Conflict") or ""),
            "signal_pattern": str(row.get("Entry_Pattern") or ""),
            "signal_confidence": _optional_number(row.get("Confidence")),
            "signal_snapshot": _signal_snapshot(row),
        }
        position.update(_entry_backtest_snapshot(row))
        if position_id in existing_ids:
            continue
        updated.append(position)
        existing_ids.add(position_id)
        added += 1
    return updated, added


def backfill_entry_backtest_snapshots(
    positions: Sequence[Mapping[str, Any]],
    history_by_date: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """Recover authentic entry-day stats from saved rankings; never use a later ranking."""
    updated = [deepcopy(dict(position)) for position in positions if isinstance(position, Mapping)]
    for position in updated:
        if position.get("entry_backtest_samples") is not None:
            continue
        entry_date = str(position.get("signal_date") or position.get("entry_date") or "")
        ticker = str(position.get("ticker", ""))
        ranking = history_by_date.get(entry_date, ())
        source = next(
            (
                row for row in ranking
                if isinstance(row, Mapping) and str(row.get("代號", "")) == ticker
            ),
            None,
        )
        if source is None:
            continue
        snapshot = _entry_backtest_snapshot(source)
        position.update(snapshot)
    return updated


def _pending_fill_price(
    bar: Mapping[str, float],
    low: float,
    high: float,
) -> float | None:
    """Resolve a deterministic next-session zone fill from a complete daily bar."""
    if bar["high"] < low or bar["low"] > high:
        return None
    if low <= bar["open"] <= high:
        return bar["open"]
    if bar["open"] < low:
        return low
    return high


def _new_pending_position(
    row: Mapping[str, Any],
    trading_date: str,
    rank: int,
) -> dict[str, Any] | None:
    ticker = str(row.get("代號", "")).strip()
    bar = _quote(row)
    low = _optional_number(row.get("Entry_Low"))
    high = _optional_number(row.get("Entry_High"))
    stop = _optional_number(row.get("Entry_Stop"))
    target = _optional_number(row.get("Entry_Target"))
    if (
        not ticker or bar is None
        or low is None or high is None or stop is None or target is None
        or not (0 < stop < low <= high < target)
    ):
        return None
    position = {
        "position_id": f"{ticker}:{trading_date}",
        "ticker": ticker,
        "name": str(row.get("名稱", ticker)),
        "signal_date": trading_date,
        "entry_date": None,
        "entry_price": None,
        "status": "PENDING",
        "close_date": None,
        "close_price": None,
        "expire_date": None,
        "current_price": bar["close"],
        "highest_price": None,
        "lowest_price": None,
        "pnl_pct": None,
        "execution_schema": TRACKER_EXECUTION_SCHEMA,
        "entry_plan_type": str(row.get("Entry_Plan_Type") or ""),
        "planned_entry_low": round(low, 4),
        "planned_entry_high": round(high, 4),
        "stop_price": round(stop, 4),
        "target_price": round(target, 4),
        "max_risk_amount": PER_POSITION_MAX_RISK,
        "signal_score": _optional_number(row.get("Score")),
        "signal_rank": _rank(row) or rank,
        "signal_change_pct": _optional_number(row.get("漲跌幅")),
        "signal_industry": str(row.get("產業") or ""),
        "signal_rsi": _optional_number(row.get("RSI")),
        "signal_bias": _optional_number(row.get("BIAS")),
        "signal_volume_ratio": _optional_number(row.get("Est_Vol_Ratio")),
        "signal_conflict": str(row.get("Signal_Conflict") or ""),
        "signal_pattern": str(row.get("Entry_Pattern") or ""),
        "signal_confidence": _optional_number(row.get("Confidence")),
        "signal_snapshot": _signal_snapshot(row),
        "pending_attempts": 0,
    }
    position.update(_entry_backtest_snapshot(row))
    return position


def _activate_pending_position(
    position: dict[str, Any],
    bar: Mapping[str, float],
    trading_date: str,
) -> bool:
    low = _number(position.get("planned_entry_low"))
    high = _number(position.get("planned_entry_high"))
    stop = _number(position.get("stop_price"))
    target = _number(position.get("target_price"))
    fill = _pending_fill_price(bar, low, high) if 0 < stop < low <= high < target else None
    position["pending_attempts"] = int(_number(position.get("pending_attempts"), 0)) + 1
    if fill is None:
        position.update({
            "status": "EXPIRED",
            "expire_date": trading_date,
            "expire_reason": "次一交易日未觸及建議進場區間",
            "current_price": bar["close"],
        })
        return False
    risk_per_share = fill - stop
    shares = math.floor(PER_POSITION_MAX_RISK / risk_per_share) if risk_per_share > 0 else 0
    if shares < 1:
        position.update({
            "status": "EXPIRED",
            "expire_date": trading_date,
            "expire_reason": "單股停損風險超過每檔上限",
            "current_price": bar["close"],
        })
        return False
    position.update({
        "status": "OPEN",
        "entry_date": trading_date,
        "entry_price": round(fill, 4),
        "fill_date": trading_date,
        "fill_rule": (
            "OPEN_IN_ZONE" if low <= bar["open"] <= high
            else ("GAP_BELOW_TOUCH" if bar["open"] < low else "PULLBACK_TOUCH")
        ),
        "shares": shares,
        "risk_per_share": round(risk_per_share, 4),
        "planned_risk_amount": round(risk_per_share * shares, 2),
        "entry_notional": round(fill * shares, 2),
        "highest_price": round(fill, 4),
        "lowest_price": round(fill, 4),
        "current_price": round(bar["close"], 4),
        "pnl_pct": round((bar["close"] / fill - 1) * 100, 2),
        "entry_bar_exit_check": "deferred_due_to_daily_ohlc_order",
    })
    position.update(_execution_metrics(position, bar["close"]))
    return True


def update_positions_with_snapshots(
    positions: Sequence[Mapping[str, Any]],
    top10_results: Sequence[Mapping[str, Any]],
    quotes: Mapping[str, Mapping[str, Any]],
    trading_date: str,
    *,
    take_profit_pct: float = 15.0,
    stop_loss_pct: float = 10.0,
    benchmark: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Track signals, next-session fills, and exits without same-close look-ahead."""
    # A forced same-day rerun rebuilds only signals created by that scan.  Filled
    # positions and older pending orders remain immutable.
    updated = [
        deepcopy(dict(position))
        for position in positions
        if isinstance(position, Mapping)
        and not (
            (
                _schema_version(position) >= TRACKER_EXECUTION_SCHEMA
                and str(position.get("signal_date") or "") == trading_date
                and position.get("status") == "PENDING"
            )
            or (
                _schema_version(position) < TRACKER_EXECUTION_SCHEMA
                and str(position.get("entry_date") or "") == trading_date
                and position.get("status") == "OPEN"
            )
        )
    ]
    top_by_ticker = {str(row.get("代號", "")): dict(row) for row in top10_results if row.get("代號")}
    quote_by_ticker = {str(key): dict(value) for key, value in quotes.items()}
    for ticker, top_quote_row in top_by_ticker.items():
        if _quote(top_quote_row) is not None:
            quote_by_ticker[ticker] = top_quote_row
    snapshots: list[dict[str, Any]] = []

    def make_snapshot(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return _attach_benchmark(_snapshot(*args, **kwargs), benchmark)

    for position in updated:
        status = str(position.get("status") or "")
        if (
            status not in {"OPEN", "PENDING"}
            and str(position.get("close_date") or position.get("expire_date") or "") != trading_date
        ):
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

        if status == "PENDING":
            bar = _quote(quote_by_ticker.get(ticker))
            if bar is None:
                snapshot = make_snapshot(
                    position,
                    trading_date,
                    top_row,
                    None,
                    action="DATA_MISSING",
                    data_status="missing",
                    take_profit_pct=take_profit_pct,
                    stop_loss_pct=stop_loss_pct,
                )
            else:
                filled = _activate_pending_position(position, bar, trading_date)
                snapshot = make_snapshot(
                    position,
                    trading_date,
                    top_row,
                    bar,
                    action="ENTRY" if filled else "ENTRY_EXPIRED",
                    take_profit_pct=take_profit_pct,
                    stop_loss_pct=stop_loss_pct,
                )
            position["last_tracked_date"] = trading_date
            position["last_snapshot"] = snapshot
            snapshots.append(snapshot)
            continue

        if status != "OPEN":
            if str(position.get("close_date") or position.get("expire_date") or "") == trading_date:
                snapshot = make_snapshot(
                    position,
                    trading_date,
                    top_row,
                    None,
                    action="ENTRY_EXPIRED" if status == "EXPIRED" else "EXIT",
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
            snapshot = make_snapshot(
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

        target, stop = _position_levels(
            position,
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
        )
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
        position.update(_execution_metrics(position, mark))
        action = "HOLD"
        if close_status and close_price is not None:
            action = "TAKE_PROFIT" if close_status == "CLOSED_TP" else "STOP_LOSS"
            position.update({
                "status": close_status,
                "close_date": trading_date,
                "close_price": round(close_price, 4),
                "current_price": round(close_price, 4),
            })
        snapshot = make_snapshot(
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
        if position.get("status") in {"OPEN", "PENDING"}
        or str(position.get("close_date") or "") == trading_date
    }
    for rank, row in enumerate(top10_results, start=1):
        ticker = str(row.get("代號", ""))
        if not ticker or ticker in blocked_today:
            continue
        position = _new_pending_position(row, trading_date, rank)
        if position is None:
            continue
        bar = _quote(row)
        if bar is None:
            continue
        ranked_row = dict(row)
        ranked_row.setdefault("Rank", rank)
        snapshot = make_snapshot(
            position,
            trading_date,
            ranked_row,
            bar,
            action="SIGNAL",
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
    benchmark: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Settle positions and create next-session orders for today's Top-10.

    New schema positions are filled only if the following complete daily bar
    intersects the saved entry zone.  Legacy positions retain their historical
    fixed-percentage exits.  If both exits occur in one later OHLC bar, the stop
    is applied first because daily bars cannot reveal intraday ordering.
    """
    updated, _ = update_positions_with_snapshots(
        positions,
        top10_results,
        quotes,
        trading_date,
        take_profit_pct=take_profit_pct,
        stop_loss_pct=stop_loss_pct,
        benchmark=benchmark,
    )
    return updated
