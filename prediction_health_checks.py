"""Read-only confirmation that today's due prediction-price notices were sent.

A late backup is harmless only when every already-due slot has a complete,
confirmed Telegram receipt. Missing/ambiguous state never counts as delivery.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from market_calendar import is_scheduled_session, next_scheduled_session


TPE = timezone(timedelta(hours=8))
PRICE_FORMAT = "prediction_hourly_prices_v1"
SLOT_DUE_TIMES = (
    (time(9, 5), "0900"), (time(10, 5), "1000"),
    (time(11, 5), "1100"), (time(12, 5), "1200"),
    (time(13, 5), "1300"), (time(13, 35), "close"),
)


def _complete_receipt(value: Any, trading_date: str, slot: str) -> bool:
    if (not isinstance(value, Mapping) or value.get("status") != "sent"
            or value.get("format") != PRICE_FORMAT or value.get("date") != trading_date
            or value.get("in_flight")):
        return False
    metadata = value.get("metadata")
    if (not isinstance(metadata, Mapping) or metadata.get("trading_date") != trading_date
            or metadata.get("slot") != slot):
        return False
    parts, count, receipts = value.get("parts"), value.get("part_count"), value.get("sent_parts")
    if (not isinstance(parts, list) or not 1 <= len(parts) <= 25
            or any(not isinstance(part, str) or not part.strip() for part in parts)
            or isinstance(count, bool) or not isinstance(count, int) or count != len(parts)
            or not isinstance(receipts, Mapping)
            or set(receipts) != {str(index) for index in range(1, count + 1)}):
        return False
    ids = list(receipts.values())
    return (all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in ids)
            and len(set(ids)) == len(ids))


def _confirmed_empty_watchlist(value: Any, now: datetime) -> bool:
    if (not isinstance(value, Mapping) or value.get("schema") != "prediction_watchlist_v1"
            or value.get("trading_date") != now.date().isoformat()
            or not isinstance(value.get("rows"), list) or value["rows"]):
        return False
    analysis = value.get("analysis_date")
    frozen = value.get("frozen_at")
    if not isinstance(analysis, str) or not isinstance(frozen, str):
        return False
    try:
        analysis_day = date.fromisoformat(analysis)
        frozen_at = datetime.fromisoformat(frozen)
    except ValueError:
        return False
    if (analysis_day.isoformat() != analysis or is_scheduled_session(analysis_day) is not True
            or next_scheduled_session(analysis_day) != now.date()
            or frozen_at.tzinfo is None or frozen_at.utcoffset() is None):
        return False
    local_frozen = frozen_at.astimezone(TPE)
    return local_frozen.date() == now.date() and local_frozen <= now


def has_completed_due_notifications(db: Any, now: datetime) -> bool:
    """Read this Taipei day's due slots; never send, write, or infer yesterday.

    A valid empty watchlist frozen today means there were no required notices;
    its analysis date and actual freeze time must also be verified.
    Read failures fail closed, without leaking client exception details.
    """
    if (db is None or not isinstance(now, datetime) or now.tzinfo is None
            or now.utcoffset() is None):
        return False
    local = now.astimezone(TPE)
    if is_scheduled_session(local.date()) is not True:
        return False
    due = [slot for threshold, slot in SLOT_DUE_TIMES if local.time() >= threshold]
    if not due:
        return False
    day = local.date().isoformat()
    try:
        watchlist = db.collection("prediction_watchlists").document(day).get()
        if watchlist.exists and _confirmed_empty_watchlist(watchlist.to_dict(), local):
            return True
        collection = db.collection("notifications")
        for slot in due:
            snapshot = collection.document(f"prediction_prices_{day}_{slot}").get()
            if not snapshot.exists or not _complete_receipt(snapshot.to_dict(), day, slot):
                return False
    except Exception:
        return False
    return True
