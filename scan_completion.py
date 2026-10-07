"""Pure evidence checks for an already-completed scan's delayed backup job."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any

from market_calendar import is_scheduled_session, next_scheduled_session
from scan_schedule import scan_window

TPE = timezone(timedelta(hours=8))


def completed_source_rows(manifest: Any, lock: Any, now: datetime) -> list[dict[str, Any]] | None:
    """Confirm the expected completed session; never reuse an older day's rows.

    Safe windows use exactly the scan's calendar-derived target, including
    same-day post-close and pre-open recovery on weekends/holidays. A late
    daytime backup can only confirm the prior session, never publish it anew.
    """
    if (not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None
            or not isinstance(manifest, Mapping) or not isinstance(lock, Mapping)):
        return None
    today = now.astimezone(TPE).date()
    day = manifest.get("scan_date")
    try:
        analysis = date.fromisoformat(day) if isinstance(day, str) else None
    except ValueError:
        analysis = None
    if (analysis is None or analysis.isoformat() != day or is_scheduled_session(analysis) is not True
            or lock.get("status") != "completed" or lock.get("trading_date") != day):
        return None
    try:
        window = scan_window(now)
    except ValueError:
        return None
    if window is not None:
        if analysis != window.analysis_date:
            return None
    elif is_scheduled_session(today) is not True or next_scheduled_session(analysis) != today:
        return None
    rows = manifest.get("data")
    if (not isinstance(rows, list) or not rows
            or any(not isinstance(row, Mapping) or row.get("Data_Date") != day for row in rows)):
        return None
    return [dict(row) for row in rows]


def _message_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _sent(value: Any, day: str) -> bool:
    return (isinstance(value, Mapping) and value.get("status") == "sent"
            and value.get("date") == day and not value.get("in_flight"))


def _all_parts(receipts: Any, count: Any) -> bool:
    if (not _message_id(count) or count > 1000 or not isinstance(receipts, Mapping)
            or set(receipts) != {str(index) for index in range(1, count + 1)}):
        return False
    ids = list(receipts.values())
    return all(_message_id(value) for value in ids) and len(set(ids)) == len(ids)


def complete_performance_receipt(value: Any, day: str) -> bool:
    return (_sent(value, day) and _all_parts(value.get("sent_pages"), value.get("page_count")))


def complete_daily_receipts(receipts: Mapping[str, Any], day: str, *, performance_is_empty: bool = False) -> bool:
    """Require actual positive Telegram receipts, not a successful job marker."""
    for kind in ("daily_top10", "daily_executable"):
        value = receipts.get(kind)
        if not isinstance(value, Mapping) or not _sent(value, day) or not _message_id(value.get("message_id")):
            return False
    if (not complete_performance_receipt(receipts.get("daily_tracking_performance"), day)
            and performance_is_empty is not True):
        return False
    research = receipts.get("daily_research")
    if not isinstance(research, Mapping) or not _sent(research, day):
        return False
    parts = research.get("parts")
    if (not isinstance(parts, list) or not 1 <= len(parts) <= 25
            or any(not isinstance(part, str) or not part.strip() for part in parts)
            or research.get("part_count") != len(parts)):
        return False
    return _all_parts(research.get("sent_parts"), research.get("part_count"))
