"""Pure Top-10 position tracking using daily OHLC bars."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date, datetime
from typing import Any

from execution_costs import (
    DEFAULT_TAIWAN_STOCK_COST_MODEL,
    calculate_max_odd_lot_position,
)

TRACKER_EXECUTION_SCHEMA = 2
PER_POSITION_MAX_RISK = 5000.0
BUY_COMMISSION_RATE = DEFAULT_TAIWAN_STOCK_COST_MODEL.buy_commission_rate
SELL_COMMISSION_RATE = DEFAULT_TAIWAN_STOCK_COST_MODEL.sell_commission_rate
SELL_TAX_RATE = DEFAULT_TAIWAN_STOCK_COST_MODEL.sell_tax_rate
MIN_COMMISSION = DEFAULT_TAIWAN_STOCK_COST_MODEL.minimum_commission


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


def _iso_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "").strip()[:10])
    except (TypeError, ValueError):
        return None


def _next_weekday(value: Any) -> str | None:
    """Return a conservative next-session guard when no exchange calendar is available."""
    parsed = _iso_date(value)
    if parsed is None:
        return None
    candidate = parsed.fromordinal(parsed.toordinal() + 1)
    while candidate.weekday() >= 5:
        candidate = candidate.fromordinal(candidate.toordinal() + 1)
    return candidate.isoformat()


def _expected_entry_date(position: Mapping[str, Any]) -> str | None:
    existing = _iso_date(position.get("expected_entry_date"))
    if existing is not None:
        return existing.isoformat()
    return _next_weekday(position.get("signal_date"))


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


def _performance_group(values: Sequence[tuple[float, float]]) -> dict[str, Any]:
    trade_count = len(values)
    if not trade_count:
        return {
            "trade_count": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": None,
            "estimated_net_pnl_total": None,
            "average_return_pct": None,
        }
    wins = sum(net_amount > 0 for net_amount, _ in values)
    return {
        "trade_count": trade_count,
        "wins": wins,
        # A zero net result is conservatively treated as a non-win so the
        # denominator and win/loss counts always describe the same trades.
        "losses": trade_count - wins,
        "win_rate_pct": round(wins / trade_count * 100, 2),
        "estimated_net_pnl_total": round(sum(amount for amount, _ in values), 2),
        "average_return_pct": round(sum(value for _, value in values) / trade_count, 2),
    }


def _has_unresolved_execution(position: Mapping[str, Any]) -> bool:
    markers = (
        position.get("entry_bar_resolution"),
        position.get("entry_bar_exit_check"),
        position.get("last_bar_excursion_status"),
    )
    return any(
        token in str(marker or "").strip().lower()
        for marker in markers
        for token in ("unresolved", "deferred")
    )


def build_cumulative_performance_summary(
    positions: Sequence[Mapping[str, Any]],
    as_of_date: Any,
) -> dict[str, Any]:
    """Summarize only fully observed, realized positions through ``as_of_date``.

    Results from the next-session execution model and legacy tracker are kept
    separate.  Missing, pending, future-dated, or order-unresolved positions
    are never converted into zero-return trades.
    """
    cutoff = _iso_date(as_of_date)
    groups: dict[str, list[tuple[float, float]]] = {
        "execution_schema_2_plus": [],
        "legacy": [],
    }
    excluded_count = 0

    for raw_position in positions:
        if not isinstance(raw_position, Mapping) or cutoff is None:
            excluded_count += 1
            continue
        position = raw_position
        status = str(position.get("status") or "").strip().upper()
        entry_date = _iso_date(position.get("entry_date"))
        close_date = _iso_date(position.get("close_date"))
        entry = _optional_number(position.get("entry_price"))
        close = _optional_number(position.get("close_price"))
        shares_value = _optional_number(position.get("shares"))
        data_status = str(position.get("data_status") or "").strip().lower()
        entry_session_status = str(position.get("entry_session_status") or "").strip().lower()

        last_snapshot = position.get("last_snapshot")
        snapshot_data_status = ""
        if isinstance(last_snapshot, Mapping) and _iso_date(last_snapshot.get("date")) == close_date:
            snapshot_data_status = str(last_snapshot.get("data_status") or "").strip().lower()

        raw_schema = position.get("execution_schema")
        schema_number = _optional_number(raw_schema) if raw_schema is not None else 1.0
        schema_is_valid = (
            schema_number is not None
            and schema_number >= 0
            and float(schema_number).is_integer()
        )
        shares_are_valid = (
            shares_value is not None
            and shares_value > 0
            and float(shares_value).is_integer()
        )
        is_complete = (
            status in {"CLOSED_TP", "CLOSED_SL"}
            and entry_date is not None
            and close_date is not None
            and entry_date <= close_date <= cutoff
            and entry is not None
            and entry > 0
            and close is not None
            and close > 0
            and shares_are_valid
            and schema_is_valid
            and data_status in {"", "ok"}
            and snapshot_data_status in {"", "ok"}
            and entry_session_status in {"", "filled"}
            and not _has_unresolved_execution(position)
        )
        if not is_complete:
            excluded_count += 1
            continue
        # Keep the runtime guard explicit as well as the aggregate validity check
        # above so static analysis cannot accidentally mask a future refactor.
        if close is None or schema_number is None:
            excluded_count += 1
            continue

        metrics = _execution_metrics(position, close)
        net_amount = _optional_number(metrics.get("net_pnl_amount"))
        net_return = _optional_number(metrics.get("net_pnl_pct"))
        if net_amount is None or net_return is None:
            excluded_count += 1
            continue
        group = "execution_schema_2_plus" if int(schema_number) >= 2 else "legacy"
        groups[group].append((net_amount, net_return))

    return {
        "as_of_date": cutoff.isoformat() if cutoff is not None else None,
        "execution_schema_2_plus": _performance_group(groups["execution_schema_2_plus"]),
        "legacy": _performance_group(groups["legacy"]),
        "included_count": sum(len(values) for values in groups.values()),
        "excluded_count": excluded_count,
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
    saved_pnl = _optional_number(position.get("pnl_pct"))
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
        "expected_entry_date": position.get("expected_entry_date"),
        "expected_entry_date_basis": position.get("expected_entry_date_basis"),
        "entry_session_status": position.get("entry_session_status"),
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
        "pnl_pct": round(saved_pnl, 2) if entry > 0 and saved_pnl is not None else None,
        "highest_price": round(highest, 4) if highest is not None and highest > 0 else None,
        "lowest_price": round(lowest, 4) if lowest is not None and lowest > 0 else None,
        "mfe_pct": round((highest / entry - 1) * 100, 2) if entry > 0 and highest is not None else None,
        "mae_pct": round((lowest / entry - 1) * 100, 2) if entry > 0 and lowest is not None else None,
        "target_price": round(target, 4) if target > 0 else None,
        "stop_price": round(stop, 4) if stop > 0 else None,
        "max_risk_amount": position.get("max_risk_amount"),
        "risk_per_share": position.get("risk_per_share"),
        "planned_risk_amount": position.get("planned_risk_amount"),
        "planned_price_risk_amount": position.get("planned_price_risk_amount"),
        "planned_transaction_cost": position.get("planned_transaction_cost"),
        "planned_stop_execution_price": position.get("planned_stop_execution_price"),
        "risk_model": position.get("risk_model"),
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
        "entry_bar_resolution": position.get("entry_bar_resolution"),
        "entry_bar_exit_check": position.get("entry_bar_exit_check"),
        "entry_bar_extremes_included": position.get("entry_bar_extremes_included"),
        "bar_excursion_status": position.get("last_bar_excursion_status"),
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


def _resolve_exit_from_open(
    bar: Mapping[str, float],
    target: float,
    stop: float,
) -> tuple[float | None, str | None, str, float, float, str]:
    """Resolve an open position without using prices observed after its exit.

    Daily bars cannot order their intraday high and low. Once a threshold is
    touched, only the open and the conservatively chosen exit are guaranteed to
    precede that exit. Full extremes are included only when the position remains
    open through the close.
    """
    open_price = bar["open"]
    if open_price <= stop:
        return open_price, "CLOSED_SL", "STOP_LOSS", open_price, open_price, "gap_stop"
    if open_price >= target:
        return open_price, "CLOSED_TP", "TAKE_PROFIT", open_price, open_price, "gap_target"

    hit_stop = bar["low"] <= stop
    hit_target = bar["high"] >= target
    if hit_stop:
        resolution = "both_touch_stop_first" if hit_target else "intraday_stop"
        return stop, "CLOSED_SL", "STOP_LOSS", open_price, stop, resolution
    if hit_target:
        return target, "CLOSED_TP", "TAKE_PROFIT", target, open_price, "intraday_target"
    return None, None, "HOLD", bar["high"], bar["low"], "held_through_close"


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
    expected_entry_date = _next_weekday(trading_date)
    if (
        not ticker or bar is None
        or expected_entry_date is None
        or low is None or high is None or stop is None or target is None
        or not (0 < stop < low <= high < target)
    ):
        return None
    position = {
        "position_id": f"{ticker}:{trading_date}",
        "ticker": ticker,
        "name": str(row.get("名稱", ticker)),
        "signal_date": trading_date,
        "expected_entry_date": expected_entry_date,
        "expected_entry_date_basis": "next_weekday_guard",
        "entry_session_status": "awaiting",
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
) -> tuple[bool, str]:
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
            "entry_session_status": "zone_not_touched",
        })
        return False, "ENTRY_EXPIRED"
    risk_estimate = calculate_max_odd_lot_position(
        fill,
        stop,
        PER_POSITION_MAX_RISK,
    )
    if risk_estimate is None or risk_estimate.shares < 1:
        position.update({
            "status": "EXPIRED",
            "expire_date": trading_date,
            "expire_reason": "單股停損風險超過每檔上限",
            "current_price": bar["close"],
            "entry_session_status": "risk_limit_invalid",
        })
        return False, "ENTRY_EXPIRED"
    shares = risk_estimate.shares
    fill_rule = (
        "OPEN_IN_ZONE" if low <= bar["open"] <= high
        else ("GAP_BELOW_TOUCH" if bar["open"] < low else "PULLBACK_TOUCH")
    )
    position.update({
        "status": "OPEN",
        "entry_date": trading_date,
        "entry_price": round(fill, 4),
        "fill_date": trading_date,
        "fill_rule": fill_rule,
        "shares": shares,
        "risk_per_share": round(risk_estimate.effective_risk_per_share, 4),
        "planned_risk_amount": round(risk_estimate.estimated_net_loss, 2),
        "planned_price_risk_amount": round(risk_estimate.price_loss, 2),
        "planned_transaction_cost": round(risk_estimate.transaction_cost, 2),
        "planned_stop_execution_price": round(risk_estimate.stop_execution_price, 4),
        "risk_model": "commission_tax_stop_slippage",
        "entry_notional": round(fill * shares, 2),
        "highest_price": round(fill, 4),
        "lowest_price": round(fill, 4),
        "current_price": round(bar["close"], 4),
        "pnl_pct": round((bar["close"] / fill - 1) * 100, 2),
        "entry_session_status": "filled",
    })
    action = "ENTRY"
    mark = bar["close"]
    if fill_rule == "OPEN_IN_ZONE":
        (
            close_price,
            close_status,
            exit_action,
            observed_high,
            observed_low,
            resolution,
        ) = _resolve_exit_from_open(bar, target, stop)
        position.update({
            "highest_price": round(max(fill, observed_high), 4),
            "lowest_price": round(min(fill, observed_low), 4),
            "entry_bar_resolution": "resolved",
            "entry_bar_exit_check": "resolved_from_open",
            "entry_bar_extremes_included": close_price is None,
            "last_bar_excursion_status": resolution,
        })
        if close_status is not None and close_price is not None:
            action = exit_action
            mark = close_price
            position.update({
                "status": close_status,
                "close_date": trading_date,
                "close_price": round(close_price, 4),
                "current_price": round(close_price, 4),
            })
    elif fill_rule == "PULLBACK_TOUCH" and bar["low"] <= stop:
        # Opening above the zone means the price must cross the fill before it
        # can reach the lower stop.  A same-day stop is therefore observable;
        # a possible target/stop tie remains conservatively stop-first.
        action = "STOP_LOSS"
        mark = stop
        position.update({
            "status": "CLOSED_SL",
            "close_date": trading_date,
            "close_price": round(stop, 4),
            "current_price": round(stop, 4),
            "highest_price": round(fill, 4),
            "lowest_price": round(stop, 4),
            "entry_bar_resolution": "resolved",
            "entry_bar_exit_check": "pullback_crossed_stop_after_fill",
            "entry_bar_extremes_included": False,
            "last_bar_excursion_status": "pullback_stop_after_fill",
        })
    elif fill_rule == "GAP_BELOW_TOUCH" and bar["low"] > stop:
        # With the whole bar above the stop after opening below the zone, the
        # upward path through the fill is safe to resolve.  Its high occurs
        # after that crossing; the low is not attributed to the position.
        hit_target_after_fill = bar["high"] >= target
        action = "TAKE_PROFIT" if hit_target_after_fill else "ENTRY"
        mark = target if hit_target_after_fill else bar["close"]
        position.update({
            "status": "CLOSED_TP" if hit_target_after_fill else "OPEN",
            "close_date": trading_date if hit_target_after_fill else None,
            "close_price": round(target, 4) if hit_target_after_fill else None,
            "current_price": round(mark, 4),
            "highest_price": round(target if hit_target_after_fill else bar["high"], 4),
            "lowest_price": round(
                fill if hit_target_after_fill else min(fill, bar["close"]),
                4,
            ),
            "entry_bar_resolution": "resolved",
            "entry_bar_exit_check": "gap_below_upward_path_resolved",
            "entry_bar_extremes_included": False,
            "last_bar_excursion_status": (
                "gap_below_target_after_fill" if hit_target_after_fill
                else "gap_below_held_after_fill"
            ),
        })
    elif fill_rule == "PULLBACK_TOUCH" and bar["high"] < target:
        # No barrier can have been touched: the low follows the first downward
        # zone crossing, while the day's high never reached the target.
        position.update({
            "highest_price": round(max(fill, bar["close"]), 4),
            "lowest_price": round(bar["low"], 4),
            "entry_bar_resolution": "resolved",
            "entry_bar_exit_check": "pullback_path_no_exit",
            "entry_bar_extremes_included": False,
            "last_bar_excursion_status": "pullback_held_after_fill",
        })
    else:
        # The remaining gap/touch combinations cannot order a pre-entry
        # extreme against a possible post-entry barrier from daily OHLC alone.
        position.update({
            "status": "UNRESOLVED",
            "entry_session_status": "filled_outcome_unresolved",
            "entry_bar_resolution": "unresolved",
            "entry_bar_exit_check": "unresolved_due_to_daily_ohlc_order",
            "entry_bar_extremes_included": False,
            "last_bar_excursion_status": "entry_order_unresolved",
        })
        action = "EXECUTION_UNRESOLVED"
    if action == "EXECUTION_UNRESOLVED":
        # The zone fill is observable, but daily OHLC cannot tell whether a
        # target seen elsewhere in the bar happened before or after that fill.
        # Do not carry a hypothetical position forward or report a fake P/L.
        position["pnl_pct"] = None
    else:
        position["pnl_pct"] = round((mark / fill - 1) * 100, 2)
        position.update(_execution_metrics(position, mark))
    return True, action


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

        available_bar = _quote(quote_by_ticker.get(ticker))
        retry_missing_entry_session = (
            status == "PENDING"
            and str(position.get("last_tracked_date", "")) == trading_date
            and isinstance(position.get("last_snapshot"), Mapping)
            and str(position["last_snapshot"].get("action") or "") == "DATA_MISSING"
            and available_bar is not None
            and _expected_entry_date(position) == trading_date
        )
        if str(position.get("last_tracked_date", "")) == trading_date and not retry_missing_entry_session:
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
            expected_date = _expected_entry_date(position)
            current_date = _iso_date(trading_date)
            signal_session = _iso_date(position.get("signal_date"))
            benchmark_context = benchmark if isinstance(benchmark, Mapping) else {}
            benchmark_date = _iso_date(benchmark_context.get("date"))
            benchmark_previous = _iso_date(
                benchmark_context.get("previous_trading_date")
            )
            is_confirmed_next_session = (
                current_date is not None
                and signal_session is not None
                and benchmark_date == current_date
                and benchmark_previous is not None
                and benchmark_previous == signal_session
            )
            missed_confirmed_session = (
                current_date is not None
                and signal_session is not None
                and benchmark_date == current_date
                and benchmark_previous is not None
                and current_date > signal_session
                and benchmark_previous != signal_session
            )
            if is_confirmed_next_session:
                # The TAIEX predecessor is authoritative and correctly handles
                # exchange holidays that a weekday-only guess cannot know.
                expected_date = trading_date
                position["expected_entry_date_basis"] = "confirmed_market_predecessor"
            if expected_date is not None:
                position["expected_entry_date"] = expected_date
                position.setdefault("expected_entry_date_basis", "next_weekday_guard")
            expected_session = _iso_date(expected_date)
            bar = available_bar
            if current_date is None or expected_session is None:
                position.update({
                    "status": "EXPIRED",
                    "expire_date": trading_date,
                    "expire_reason": "無法確認預期次一交易日，未假設成交",
                    "entry_session_status": "invalid_date",
                })
                snapshot = make_snapshot(
                    position,
                    trading_date,
                    top_row,
                    None,
                    action="ENTRY_EXPIRED",
                    data_status="missing",
                    take_profit_pct=take_profit_pct,
                    stop_loss_pct=stop_loss_pct,
                )
            elif missed_confirmed_session:
                position.update({
                    "status": "EXPIRED",
                    "expire_date": trading_date,
                    "expire_reason": "大盤交易日序列顯示已錯過次一交易日，未使用較晚行情補成交",
                    "entry_session_status": "missed",
                })
                snapshot = make_snapshot(
                    position,
                    trading_date,
                    top_row,
                    None,
                    action="ENTRY_EXPIRED",
                    data_status="missing",
                    take_profit_pct=take_profit_pct,
                    stop_loss_pct=stop_loss_pct,
                )
            elif current_date < expected_session:
                position["entry_session_status"] = "awaiting"
                snapshot = make_snapshot(
                    position,
                    trading_date,
                    top_row,
                    None,
                    action="WAIT_ENTRY_SESSION",
                    data_status="not_due",
                    take_profit_pct=take_profit_pct,
                    stop_loss_pct=stop_loss_pct,
                )
            elif current_date > expected_session:
                position.update({
                    "status": "EXPIRED",
                    "expire_date": trading_date,
                    "expire_reason": "已超過預期次一交易日，未使用較晚行情補成交",
                    "entry_session_status": "missed",
                })
                snapshot = make_snapshot(
                    position,
                    trading_date,
                    top_row,
                    None,
                    action="ENTRY_EXPIRED",
                    data_status="missing",
                    take_profit_pct=take_profit_pct,
                    stop_loss_pct=stop_loss_pct,
                )
            elif bar is None:
                position["entry_session_status"] = "data_missing"
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
                filled, entry_action = _activate_pending_position(position, bar, trading_date)
                snapshot = make_snapshot(
                    position,
                    trading_date,
                    top_row,
                    bar,
                    action=entry_action if filled else "ENTRY_EXPIRED",
                    data_status=(
                        "unresolved"
                        if entry_action == "EXECUTION_UNRESOLVED"
                        else "ok"
                    ),
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
        close_price, close_status, action, observed_high, observed_low, resolution = (
            _resolve_exit_from_open(bar, target, stop)
        )
        position["highest_price"] = max(
            _number(position.get("highest_price"), entry), observed_high
        )
        position["lowest_price"] = min(
            _number(position.get("lowest_price"), entry), observed_low
        )
        position["current_price"] = bar["close"]
        position["last_bar_excursion_status"] = resolution

        mark = close_price if close_price is not None else bar["close"]
        position["pnl_pct"] = round((mark / entry - 1) * 100, 2)
        position.update(_execution_metrics(position, mark))
        if close_status and close_price is not None:
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
        or (
            position.get("status") == "UNRESOLVED"
            and str(position.get("entry_date") or "") == trading_date
        )
        or str(position.get("close_date") or "") == trading_date
    }
    for rank, row in enumerate(top10_results, start=1):
        ticker = str(row.get("代號", ""))
        if not ticker or ticker in blocked_today:
            continue
        new_position = _new_pending_position(row, trading_date, rank)
        if new_position is None:
            continue
        bar = _quote(row)
        if bar is None:
            continue
        ranked_row = dict(row)
        ranked_row.setdefault("Rank", rank)
        snapshot = make_snapshot(
            new_position,
            trading_date,
            ranked_row,
            bar,
            action="SIGNAL",
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
        )
        new_position["last_tracked_date"] = trading_date
        new_position["last_snapshot"] = snapshot
        updated.append(new_position)
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
