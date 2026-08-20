"""Pure helpers for daily-scan dates, idempotency, and data quality."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Iterable, Mapping, Tuple


GOOD_STATUSES = {"ok", "realtime", "confirmed"}
PARTIAL_STATUSES = {"partial", "estimated", "empty"}
MAX_FINAL_SCORE_GAIN = 9
DEFAULT_DAILY_SCAN_LIMIT = 300
DEFAULT_WEEKLY_SCAN_LIMIT = 500


def _date_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime().date().isoformat()
    text = str(value).strip()
    return text[:10] if len(text) >= 10 else ""


def latest_trading_date(index: Iterable[Any]) -> str:
    """Return the latest market date represented by an OHLCV index."""
    values = list(index)
    if not values:
        return ""
    return _date_text(values[-1])


def scan_universe_limit(
    trading_date: str,
    override: Any = None,
    *,
    daily_limit: int = DEFAULT_DAILY_SCAN_LIMIT,
    weekly_limit: int = DEFAULT_WEEKLY_SCAN_LIMIT,
) -> int:
    """Use the larger universe on Friday, with an explicit bounded override."""
    if override not in (None, ""):
        try:
            return max(1, min(1000, int(override)))
        except (TypeError, ValueError):
            pass
    try:
        scan_day = date.fromisoformat(str(trading_date)[:10])
    except ValueError:
        return int(daily_limit)
    return int(weekly_limit if scan_day.weekday() == 4 else daily_limit)


def previous_scan_state(
    previous_doc: Mapping[str, Any] | None,
    trading_date: str,
) -> Tuple[Dict[str, int], Dict[str, int], bool]:
    """Preserve prior-day comparisons when a scan is rerun on the same day."""
    payload = dict(previous_doc or {})
    rows = payload.get("data", [])
    if not isinstance(rows, list):
        rows = []
    same_day = str(payload.get("scan_date", "")) == str(trading_date)

    streaks: Dict[str, int] = {}
    ranks: Dict[str, int] = {}
    for position, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            continue
        ticker = str(row.get("代號", "")).strip()
        if not ticker:
            continue
        try:
            streaks[ticker] = max(0, int(row.get("Streak", 0)))
        except (TypeError, ValueError):
            streaks[ticker] = 0

        rank_value = row.get("Prev_Rank", 999) if same_day else row.get("Rank", position)
        try:
            ranks[ticker] = int(rank_value)
        except (TypeError, ValueError):
            ranks[ticker] = 999
    return streaks, ranks, same_day


def next_streak(ticker: str, streaks: Mapping[str, int], same_day: bool) -> int:
    previous = max(0, int(streaks.get(str(ticker), 0)))
    if same_day and previous > 0:
        return previous
    return previous + 1


def should_complete_candidate(
    initial_score: int,
    advanced_pattern_signal: str = "",
    *,
    final_threshold: int = 45,
) -> bool:
    """Keep every candidate that can still reach the final-score boundary."""
    return str(advanced_pattern_signal) == "Buy" or int(initial_score) + MAX_FINAL_SCORE_GAIN >= final_threshold


def build_scan_quality(
    statuses: Mapping[str, str],
    *,
    institutional_days: int = 0,
) -> Tuple[Dict[str, str], int]:
    """Build a persisted quality map and a conservative confidence score."""
    quality = {str(key): str(value or "unknown").lower() for key, value in statuses.items()}
    quality["institutional"] = f"{institutional_days}d" if institutional_days > 0 else quality.get("institutional", "missing")

    penalty_units = 0.0
    for key, status in quality.items():
        if key == "institutional" and status.endswith("d"):
            continue
        if status in GOOD_STATUSES:
            continue
        penalty_units += 0.5 if status in PARTIAL_STATUSES else 1.0
    confidence = max(20, int(round(100 - penalty_units * 12)))
    return quality, confidence
