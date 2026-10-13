"""Conservatively join one completed Yahoo daily bar to Yahoo history.

Both inputs must be fetched with auto_adjust=True from the same Yahoo source.
This module fetches nothing, calculates no prices, and does not repair arbitrary
OHLC corruption or corporate-action adjustments. Failures must keep the scan
blocked, not relax its expected-date check.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import math
from typing import Any

import pandas as pd

from market_calendar import is_scheduled_session, next_scheduled_session

TPE = timezone(timedelta(hours=8))
OHLC = ("Open", "High", "Low", "Close")
ACTIONS = ("Dividends", "Stock Splits")
COMPLETION_SOURCE = "Yahoo history(period=1d, auto_adjust=True)"


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
    if (
        not isinstance(frame, pd.DataFrame) or frame.empty
        or not isinstance(frame.index, pd.DatetimeIndex)
        or not frame.columns.is_unique or not set((*OHLC, "Volume")).issubset(frame.columns)
    ):
        raise ValueError("日線格式、OHLCV 欄位或索引無法驗證")
    result = frame.copy(deep=True)
    index = result.index
    if index.tz is not None:
        index = index.tz_convert(TPE).tz_localize(None)
    index = index.normalize()
    if index.hasnans or not index.is_unique or not index.is_monotonic_increasing:
        raise ValueError("日線日期不能缺漏、重複或亂序")
    result.index = index
    return result


def _validate_bar(row: pd.Series) -> dict[str, float]:
    values: dict[str, float] = {}
    for key in (*OHLC, "Volume"):
        value = _number(row.get(key))
        if value is None or (value < 0 if key == "Volume" else value <= 0):
            raise ValueError("日線 OHLCV 必須完整、有限且價格為正")
        values[key] = value
    tolerance = max(values[key] for key in OHLC) * 1e-7
    if (
        values["High"] + tolerance < max(values[key] for key in ("Open", "Close", "Low"))
        or values["Low"] - tolerance > min(values[key] for key in ("Open", "Close"))
    ):
        raise ValueError("日線高低價範圍不合理")
    return values


def _validate_actions(row: pd.Series, *, required: bool) -> None:
    for key in ACTIONS:
        if key not in row.index:
            if required:
                raise ValueError("接合日缺少公司行動欄位，不能驗證調整價基準")
            continue
        value = _number(row[key])
        if value is None or value != 0:
            raise ValueError("接合日有公司行動或缺值，不自動拼接調整股價")


def daily_bar_complete(row: pd.Series) -> bool:
    """Whether an observed candle is usable, without inventing missing fields."""
    try:
        _validate_bar(row)
        return True
    except (TypeError, ValueError):
        return False


def _check_window(expected: date, now: datetime) -> None:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("補日時鐘必須包含時區")
    local = now.astimezone(TPE)
    if is_scheduled_session(expected) is not True:
        raise ValueError("預期日期不是已驗證的預定交易日")
    if expected == local.date() and local.time() >= time(14, 30):
        return
    # Only the most recent scheduled session can be recovered before opening.
    # The next session may lie after a closed weekend/holiday recovery morning.
    following = next_scheduled_session(expected)
    if (
        expected < local.date() and following is not None and following >= local.date()
        and local.time() < time(9)
    ):
        return
    raise ValueError("只可補當日盤後或開盤前上一已完成交易日")


def merge_completed_daily_bar(
    history: pd.DataFrame,
    latest: pd.DataFrame,
    expected_date: str,
    *,
    now: datetime,
    require_actions: bool = True,
) -> pd.DataFrame:
    """Merge exactly one verified completed session; never bridge multiple gaps.

    A missing trailing row or null cells may be filled only by a complete,
    dated one-day candle. Existing non-null prices must agree. The preceding
    history row must be the immediately preceding scheduled trading session.
    Indicator columns are dropped: callers must recompute them after this join.
    For the non-corporate TAIEX index only, pass require_actions=False.
    """
    if not isinstance(expected_date, str):
        raise ValueError("預期日期必須為完整 ISO 日期")
    try:
        expected = date.fromisoformat(expected_date)
    except ValueError:
        raise ValueError("預期日期必須為完整 ISO 日期") from None
    if expected.isoformat() != expected_date:
        raise ValueError("預期日期必須為完整 ISO 日期")
    _check_window(expected, now)
    original_attrs = dict(history.attrs) if isinstance(history, pd.DataFrame) else {}
    work = _normalize(history)
    supplement = _normalize(latest)
    if len(supplement) != 1 or supplement.index[0].date() != expected:
        raise ValueError("補充來源必須只有預期交易日的一根日 K")
    target = pd.Timestamp(expected)
    if work.index[-1] > target:
        raise ValueError("既有歷史含預期交易日之後資料")
    prior = work.loc[work.index < target]
    if prior.empty or next_scheduled_session(prior.index[-1].date()) != expected:
        raise ValueError("只能補一個交易日；接合前日不是緊鄰交易日")
    _validate_bar(prior.iloc[-1])
    _validate_actions(prior.iloc[-1], required=require_actions)
    source_bar = supplement.iloc[0]
    new_values = _validate_bar(source_bar)
    _validate_actions(source_bar, required=require_actions)

    if target in work.index:
        existing = work.loc[target]
        _validate_actions(existing, required=require_actions)
        for key in (*OHLC, "Volume"):
            value = existing[key]
            if pd.isna(value):
                continue
            number = _number(value)
            tolerance = new_values[key] * 1e-7 if key in OHLC else 0
            if number is None or abs(number - new_values[key]) > tolerance:
                raise ValueError("同日既有與補充 OHLCV 不一致，不覆寫真實觀測")

    # Keep raw candle/action columns only. Carrying stale MA/MACD into the new
    # date would silently invalidate scoring; recalculate them at the caller.
    columns = list((*OHLC, "Volume")) + [key for key in ACTIONS if key in work.columns or key in supplement.columns]
    completed = work.reindex(columns=columns).copy(deep=True)
    for key in columns:
        completed.loc[target, key] = source_bar.get(key, float("nan"))
    completed = completed.sort_index()
    completed.attrs = original_attrs
    completed.attrs.update({
        "price_source": "Yahoo history + verified daily bar",
        "completed_daily_bar_source": COMPLETION_SOURCE,
        "completed_daily_bar_date": expected_date,
        "completed_daily_bar_previous_date": prior.index[-1].date().isoformat(),
    })
    return completed
