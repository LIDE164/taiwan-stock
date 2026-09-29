"""Offline TWSE scheduled sessions, not confirmation that the market opened.

The 2026 dates were verified against the official annual schedule on 2026-09-29.
Only scheduled closures are covered: emergency/typhoon closures must still be
confirmed with dated market data. Unknown years deliberately return ``None``;
weekdays alone are not evidence of a Taiwan stock-exchange trading session.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

CALENDAR_SOURCE = (
    "https://www.twse.com.tw/holidaySchedule/holidaySchedule"
    "?response=json&queryYear=2026"
)
CALENDAR_VERIFIED_ON = "2026-09-29"
CALENDAR_LIMITATION = "官方預定交易日；不含臨時天然災害或其他緊急休市。"

# Official response: queryYear=2026, title="115 年市場開休市日期".
# Its three open-session notices (Jan 2, Feb 11, Feb 23) are NOT closures.
# Feb 12-13 are settlement-only days and must not be treated as sessions.
_CLOSED_DATES_BY_YEAR = {
    2026: frozenset({
        "2026-01-01",
        "2026-02-12", "2026-02-13",
        "2026-02-15", "2026-02-16", "2026-02-17",
        "2026-02-18", "2026-02-19", "2026-02-20",
        "2026-02-27", "2026-02-28",
        "2026-04-03", "2026-04-04", "2026-04-05", "2026-04-06",
        "2026-05-01",
        "2026-06-19",
        "2026-09-25", "2026-09-28",
        "2026-10-09", "2026-10-10", "2026-10-25", "2026-10-26",
        "2026-12-25",
    }),
}


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, (str, date)):
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (TypeError, ValueError):
        return None


def is_scheduled_session(value: Any) -> bool | None:
    """Return planned open/closed status, or None if the date/year is unknown."""
    parsed = _parse_date(value)
    if parsed is None or parsed.year not in _CLOSED_DATES_BY_YEAR:
        return None
    return parsed.weekday() < 5 and parsed.isoformat() not in _CLOSED_DATES_BY_YEAR[parsed.year]


def next_scheduled_session(analysis_date: Any) -> date | None:
    """Find the next scheduled session, never guess beyond verified calendar years."""
    parsed = _parse_date(analysis_date)
    if parsed is None or parsed.year not in _CLOSED_DATES_BY_YEAR:
        return None
    candidate = parsed + timedelta(days=1)
    while True:
        status = is_scheduled_session(candidate)
        if status is None:
            return None
        if status:
            return candidate
        candidate += timedelta(days=1)
